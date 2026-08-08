"""监控与审计模块。

- 记录每次工具调用（工具名、参数、耗时、是否成功、错误信息、客户端 IP/UA）；
- 维护环形执行历史（内存，可查询）与 JSONL 审计日志（落盘）；
- SQLite 持久化存储（供人工审计页面查询）；
- 汇总工具调用统计（次数/错误/总耗时）；
- 提供 JVM 内存（JDBC 层）与进程内存（RSS）采集。
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ARG_LEN = 500  # 单个参数最大记录长度（防审计日志过大）

# 当前 HTTP 请求的客户端信息（ASGI 中间件写入，工具执行时读取）
_client_ctx: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "mcp_dbtools_client", default={}
)


_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tool TEXT NOT NULL,
    client_ip TEXT,
    user_agent TEXT,
    args TEXT,
    duration_ms REAL,
    ok INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit_logs(tool);
CREATE INDEX IF NOT EXISTS idx_audit_ip ON audit_logs(client_ip);
CREATE INDEX IF NOT EXISTS idx_audit_ok ON audit_logs(ok);
"""


def _iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def set_client_info(client_ip: str | None = None, user_agent: str | None = None) -> None:
    """在 ASGI 请求入口记录客户端信息（contextvar，随请求上下文传递）。"""
    _client_ctx.set({"client_ip": client_ip, "user_agent": user_agent})


def get_client_info() -> dict[str, str]:
    """读取当前请求的客户端信息。"""
    return _client_ctx.get()


def _sanitize_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """裁剪过长的参数（如 SQL），避免审计日志无限膨胀。"""
    if not args:
        return {}
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str):
            out[k] = v if len(v) <= _MAX_ARG_LEN else v[:_MAX_ARG_LEN] + f"...({len(v)}字符)"
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = str(v)  # 其它类型兜底转字符串
    return out


class Monitor:
    """执行历史 + 审计 + 指标聚合。线程安全。"""

    def __init__(
        self,
        history_size: int = 500,
        audit_file: str | None = "logs/audit.jsonl",
        audit_db: str | None = "logs/audit.db",
    ):
        self._history: deque[dict[str, Any]] = deque(maxlen=max(1, history_size))
        self._lock = threading.RLock()
        self._started_at = time.time()
        self._tool_counts: dict[str, dict[str, float]] = {}
        self._audit_file: str | None = audit_file
        if audit_file:
            try:
                Path(audit_file).parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # 无写权限时降级为不落盘
                logger.warning("审计日志目录不可写，关闭落盘: %s", exc)
                self._audit_file = None
        # SQLite 持久化（人工审计页面数据源）
        self._audit_db: str | None = audit_db
        self._db: sqlite3.Connection | None = None
        self._db_lock = threading.Lock()
        if audit_db:
            try:
                Path(audit_db).parent.mkdir(parents=True, exist_ok=True)
                self._db = sqlite3.connect(audit_db, check_same_thread=False)
                self._db.executescript(_AUDIT_SCHEMA)
                self._db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("审计数据库不可用，关闭: %s", exc)
                self._db = None

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------
    def record_tool_call(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        duration: float | None = None,
        ok: bool = True,
        error: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": _iso(),
            "tool": tool,
            "args": _sanitize_args(args),
            "duration_ms": round((duration or 0.0) * 1000, 2),
            "ok": ok,
            "client_ip": client_ip,
            "user_agent": user_agent,
        }
        if error:
            entry["error"] = error if len(error) <= 1000 else error[:1000] + "..."
        with self._lock:
            self._history.append(entry)
            stat = self._tool_counts.setdefault(tool, {"calls": 0, "errors": 0, "total_time_ms": 0.0})
            stat["calls"] += 1
            stat["total_time_ms"] += entry["duration_ms"]
            if not ok:
                stat["errors"] += 1
            if self._audit_file:
                self._append_audit(entry)
            if self._db is not None:
                self._append_db(entry)
        return entry

    def _append_audit(self, entry: dict[str, Any]) -> None:
        try:
            with open(self._audit_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("写入审计日志失败: %s", exc)

    def _append_db(self, entry: dict[str, Any]) -> None:
        try:
            with self._db_lock:
                self._db.execute(
                    "INSERT INTO audit_logs (ts, tool, client_ip, user_agent, args, duration_ms, ok, error) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        entry["ts"],
                        entry["tool"],
                        entry.get("client_ip"),
                        entry.get("user_agent"),
                        json.dumps(entry.get("args", {}), ensure_ascii=False),
                        entry["duration_ms"],
                        1 if entry["ok"] else 0,
                        entry.get("error"),
                    ),
                )
                self._db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入审计数据库失败: %s", exc)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def execution_history(
        self, limit: int = 50, tool: str | None = None, ok: bool | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(reversed(self._history))
        if tool:
            items = [i for i in items if i["tool"] == tool]
        if ok is not None:
            items = [i for i in items if i["ok"] is ok]
        return items[: max(0, limit)]

    def tool_summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                name: {k: (round(v, 2) if k == "total_time_ms" else v) for k, v in stat.items()}
                for name, stat in sorted(self._tool_counts.items())
            }

    def uptime_seconds(self) -> float:
        return time.time() - self._started_at

    # ------------------------------------------------------------------
    # SQLite 审计查询（人工审计页面 / API）
    # ------------------------------------------------------------------
    def query_audit(
        self,
        page: int = 1,
        page_size: int = 20,
        tool: str | None = None,
        ip: str | None = None,
        ok: bool | None = None,
        q: str | None = None,
        ts_from: str | None = None,
        ts_to: str | None = None,
    ) -> dict[str, Any]:
        """按条件分页查询审计记录（参数化查询防注入）。"""
        if self._db is None:
            return {"total": 0, "page": page, "page_size": page_size, "items": []}
        where: list[str] = []
        params: list[Any] = []
        if tool:
            where.append("tool = ?")
            params.append(tool)
        if ip:
            where.append("client_ip LIKE ?")
            params.append(f"%{ip}%")
        if ok is not None:
            where.append("ok = ?")
            params.append(1 if ok else 0)
        if q:
            where.append("(args LIKE ? OR error LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if ts_from:
            where.append("ts >= ?")
            params.append(ts_from)
        if ts_to:
            where.append("ts <= ?")
            params.append(ts_to)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        page = max(1, page)
        page_size = min(max(1, page_size), 200)
        offset = (page - 1) * page_size
        with self._db_lock:
            total = self._db.execute(
                f"SELECT COUNT(*) FROM audit_logs{w}", params
            ).fetchone()[0]
            rows = self._db.execute(
                f"SELECT id, ts, tool, client_ip, user_agent, args, duration_ms, ok, error "
                f"FROM audit_logs{w} ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
        items = []
        for r in rows:
            try:
                args = json.loads(r[5] or "{}")
            except json.JSONDecodeError:
                args = {}
            items.append(
                {
                    "id": r[0],
                    "ts": r[1],
                    "tool": r[2],
                    "client_ip": r[3],
                    "user_agent": r[4],
                    "args": args,
                    "duration_ms": r[6],
                    "ok": bool(r[7]),
                    "error": r[8],
                }
            )
        return {"total": total, "page": page, "page_size": page_size, "items": items}

    def get_audit(self, rid: int) -> dict[str, Any] | None:
        """按 ID 查询单条审计记录。"""
        if self._db is None:
            return None
        with self._db_lock:
            r = self._db.execute(
                "SELECT id, ts, tool, client_ip, user_agent, args, duration_ms, ok, error "
                "FROM audit_logs WHERE id = ?",
                (rid,),
            ).fetchone()
        if not r:
            return None
        try:
            args = json.loads(r[5] or "{}")
        except json.JSONDecodeError:
            args = {}
        return {
            "id": r[0], "ts": r[1], "tool": r[2], "client_ip": r[3], "user_agent": r[4],
            "args": args, "duration_ms": r[6], "ok": bool(r[7]), "error": r[8],
        }

    def audit_summary(self) -> dict[str, Any]:
        """审计概览：总量、今日、成功/失败、Top 工具与 IP。"""
        if self._db is None:
            return {"total": 0, "today": 0, "success": 0, "failed": 0, "top_tools": [], "top_ips": []}
        today = datetime.now().strftime("%Y-%m-%d")
        with self._db_lock:
            total = self._db.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
            today_n = self._db.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE ts LIKE ?", [today + "%"]
            ).fetchone()[0]
            success = self._db.execute("SELECT COUNT(*) FROM audit_logs WHERE ok = 1").fetchone()[0]
            top_tools = self._db.execute(
                "SELECT tool, COUNT(*) c FROM audit_logs GROUP BY tool ORDER BY c DESC LIMIT 10"
            ).fetchall()
            top_ips = self._db.execute(
                "SELECT client_ip, COUNT(*) c FROM audit_logs WHERE client_ip IS NOT NULL "
                "GROUP BY client_ip ORDER BY c DESC LIMIT 10"
            ).fetchall()
        return {
            "total": total,
            "today": today_n,
            "success": success,
            "failed": total - success,
            "top_tools": [{"tool": t, "count": c} for t, c in top_tools],
            "top_ips": [{"ip": ip, "count": c} for ip, c in top_ips],
        }

    # ------------------------------------------------------------------
    # 内存采集
    # ------------------------------------------------------------------
    @staticmethod
    def jvm_memory_mb() -> dict[str, float] | None:
        """JVM 堆内存（MB）。JVM 未启动时返回 None。"""
        try:
            import jpype

            if not jpype.isJVMStarted():
                return None
            rt = jpype.JClass("java.lang.Runtime").getRuntime()
            total = float(rt.totalMemory()) / 1048576.0
            free = float(rt.freeMemory()) / 1048576.0
            return {
                "used": round(total - free, 2),
                "committed": round(total, 2),
                "max": round(float(rt.maxMemory()) / 1048576.0, 2),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("获取 JVM 内存失败: %s", exc)
            return None

    @staticmethod
    def process_memory_mb() -> float | None:
        """Python 进程 RSS 内存（MB，Linux）。"""
        try:
            with open("/proc/self/statm", encoding="ascii") as fh:
                fields = fh.read().split()
            page = os.sysconf("SC_PAGE_SIZE")
            return round(int(fields[1]) * page / 1048576.0, 2)
        except Exception:  # noqa: BLE001
            return None


def wrap_tool(monitor: Monitor, name: str):
    """装饰器：包装 MCP 工具函数，自动记录执行历史与审计（含客户端 IP/UA）。"""

    def deco(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            call_args: dict[str, Any] = {}
            if args:  # 位置参数（MCP 客户端通常全用关键字）
                call_args["_positional"] = [_sanitize_plain(a) for a in args]
            call_args.update(kwargs)
            client = get_client_info()
            try:
                result = fn(*args, **kwargs)
                monitor.record_tool_call(
                    name, call_args, time.monotonic() - t0, ok=True,
                    client_ip=client.get("client_ip"),
                    user_agent=client.get("user_agent"),
                )
                return result
            except Exception as exc:  # noqa: BLE001
                monitor.record_tool_call(
                    name, call_args, time.monotonic() - t0, ok=False, error=str(exc),
                    client_ip=client.get("client_ip"),
                    user_agent=client.get("user_agent"),
                )
                raise

        return wrapper

    return deco


def _sanitize_plain(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)
