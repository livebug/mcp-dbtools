"""大数据量异步导出模块。

设计：
- start(): 发起导出（后台线程执行 SQL，逐批写入文本文件，不占内存）；
- status(): 轮询查询导出状态；
- 完成后通过 /export/{id}/download 下载文件。

约定：
- 只导出数据（文本文件），分隔符可自定义（默认逗号）；
- CSV/Excel 等格式由客户端自行解析处理，服务端不生成二进制格式；
- 每行值含分隔符/引号/换行时按 RFC4180 加双引号转义，便于客户端按分隔符解析。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = __import__("logging").getLogger(__name__)


def _join_row(row: Any, delimiter: str) -> str:
    """将一行值用分隔符拼接，并对含分隔符/引号/换行的值做双引号转义。"""
    parts: list[str] = []
    for v in row:
        s = "" if v is None else str(v)
        if delimiter in s or '"' in s or "\n" in s or "\r" in s:
            s = '"' + s.replace('"', '""') + '"'
        parts.append(s)
    return delimiter.join(parts)


class ExportManager:
    """异步导出任务管理（线程安全）。"""

    def __init__(
        self,
        export_dir: str = "exports",
        max_rows: int = 100000,
        jdbc_manager: Any = None,
        keep_seconds: int = 86400,
        max_files: int = 100,
    ):
        self.export_dir = Path(export_dir)
        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("导出目录不可写: %s", exc)
        self.max_rows = max(1, max_rows)
        self.keep_seconds = max(60, keep_seconds)
        self.max_files = max(1, max_files)
        self._jm = jdbc_manager
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._cleaner_thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    def start(
        self,
        ds: Any,
        sql: str,
        delimiter: str = ",",
        include_header: bool = True,
        filename: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """发起导出任务，立即返回任务信息（含 export_id）。"""
        export_id = uuid.uuid4().hex[:12]
        safe = re.sub(r"[^\w.\-]+", "_", (filename or "export").strip()) or "export"
        path = self.export_dir / f"{export_id}_{safe}.txt"
        task: dict[str, Any] = {
            "id": export_id,
            "datasource": ds.name,
            "sql": sql[:200],
            "delimiter": delimiter,
            "include_header": include_header,
            "status": "pending",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "started_at": None,
            "finished_at": None,
            "filename": path.name,
            "rows": 0,
            "columns": [],
            "truncated": False,
            "error": None,
            "_path": str(path),
        }
        with self._lock:
            self._tasks[export_id] = task
        threading.Thread(
            target=self._run,
            args=(task, ds, sql, delimiter, include_header, limit),
            daemon=True,
        ).start()
        return self.status(export_id)

    def _run(self, task, ds, sql, delimiter, include_header, limit) -> None:
        task["status"] = "running"
        task["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            cap = min(limit or self.max_rows, self.max_rows)
            conn = self._jm._acquire(ds)
            cur = None
            try:
                cur = conn.cursor()
                cur.execute(sql)
                cols = [d[0] for d in (cur.description or [])]
                task["columns"] = cols
                n = 0
                truncated = False
                with open(task["_path"], "w", encoding="utf-8", newline="") as fh:
                    if include_header and cols:
                        fh.write(_join_row(cols, delimiter) + "\n")
                    while True:
                        batch = cur.fetchmany(2000)
                        if not batch:
                            break
                        for row in batch:
                            if n >= cap:
                                truncated = True
                                break
                            fh.write(_join_row(row, delimiter) + "\n")
                            n += 1
                        if truncated:
                            break
                task["rows"] = n
                task["truncated"] = truncated
                task["status"] = "succeeded"
                self._jm._record_success(ds.name)
            finally:
                if cur is not None:
                    try:
                        cur.close()
                    except Exception:  # noqa: BLE001
                        pass
                self._jm._release(ds, conn)
        except Exception as exc:  # noqa: BLE001
            self._jm._record_failure(ds.name)
            task["status"] = "failed"
            task["error"] = str(exc)[:1000]
            logger.warning("导出失败 %s: %s", task["id"], exc)
        finally:
            task["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    def status(self, export_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(export_id)
        return self._public(task) if task else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            tasks = [self._public(t) for t in self._tasks.values()]
        return sorted(tasks, key=lambda t: t["created_at"], reverse=True)

    def get_file_path(self, export_id: str) -> Path | None:
        with self._lock:
            task = self._tasks.get(export_id)
        if task and task["status"] == "succeeded" and Path(task["_path"]).is_file():
            return Path(task["_path"])
        return None

    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        return {
            k: v for k, v in task.items() if k != "_path"
        } | {"download_url": f"/export/{task['id']}/download"}

    # ------------------------------------------------------------------
    # 过期任务清理（防止 exports/ 目录无限膨胀）
    # ------------------------------------------------------------------
    def start_cleaner(self, interval: float = 300.0) -> None:
        """启动后台清理线程（daemon），周期性删除超龄/超量的导出文件与任务记录。"""
        if self._cleaner_thread is not None and self._cleaner_thread.is_alive():
            return
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("导出清理失败: %s", exc)

        self._cleaner_thread = threading.Thread(
            target=_loop, name="export-cleaner", daemon=True
        )
        self._cleaner_thread.start()

    def stop_cleaner(self) -> None:
        self._stop.set()
        if self._cleaner_thread is not None:
            try:
                self._cleaner_thread.join(timeout=2.0)
            except RuntimeError:
                pass

    def cleanup(self, keep_seconds: int | None = None, max_files: int | None = None) -> dict[str, Any]:
        """清理过期导出文件与任务记录。

        规则：
        1. 删除 finished（succeeded/failed）且超过 keep_seconds 的任务文件与记录；
        2. 若剩余任务数仍超过 max_files，删除最旧的已完成任务文件与记录。
        返回清理统计。运行中（pending/running）的任务不清理。
        """
        keep_seconds = keep_seconds or self.keep_seconds
        max_files = max_files or self.max_files
        now = time.time()
        removed: list[str] = []
        with self._lock:
            finished = [
                t for t in self._tasks.values() if t["status"] in ("succeeded", "failed")
            ]
            # 按完成时间排序（无完成时间按创建时间兜底）
            def _finish_ts(t: dict[str, Any]) -> float:
                ts = t.get("finished_at") or t.get("created_at") or ""
                try:
                    return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
                except (ValueError, TypeError):
                    return 0.0

            finished.sort(key=_finish_ts)
            for t in finished:
                if now - _finish_ts(t) >= keep_seconds:
                    self._drop_task(t, removed)
            # 超出数量上限，删最旧的
            active = [t for t in self._tasks.values() if t["status"] in ("pending", "running")]
            remaining_finished = [
                t for t in self._tasks.values() if t["status"] in ("succeeded", "failed")
            ]
            overflow = len(active) + len(remaining_finished) - max_files
            for t in remaining_finished[: max(0, overflow)]:
                self._drop_task(t, removed)
        return {"removed": removed, "removed_count": len(removed)}

    def _drop_task(self, task: dict[str, Any], removed: list[str]) -> None:
        """删除单个任务的文件与记录（调用方需持有 self._lock）。"""
        eid = task["id"]
        p = task.get("_path")
        if p:
            try:
                fp = Path(p)
                if fp.is_file():
                    fp.unlink()
            except OSError as exc:
                logger.warning("删除导出文件失败 %s: %s", eid, exc)
        self._tasks.pop(eid, None)
        removed.append(eid)
