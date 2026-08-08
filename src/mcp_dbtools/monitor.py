"""监控与审计模块。

- 记录每次工具调用（工具名、参数、耗时、是否成功、错误信息）；
- 维护环形执行历史（内存，可查询）与 JSONL 审计日志（落盘）；
- 汇总工具调用统计（次数/错误/总耗时）；
- 提供 JVM 内存（JDBC 层）与进程内存（RSS）采集。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ARG_LEN = 500  # 单个参数最大记录长度（防审计日志过大）


def _iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


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

    def __init__(self, history_size: int = 500, audit_file: str | None = "logs/audit.jsonl"):
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
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": _iso(),
            "tool": tool,
            "args": _sanitize_args(args),
            "duration_ms": round((duration or 0.0) * 1000, 2),
            "ok": ok,
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
        return entry

    def _append_audit(self, entry: dict[str, Any]) -> None:
        try:
            with open(self._audit_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("写入审计日志失败: %s", exc)

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
    """装饰器：包装 MCP 工具函数，自动记录执行历史与审计。"""

    def deco(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            call_args: dict[str, Any] = {}
            if args:  # 位置参数（MCP 客户端通常全用关键字）
                call_args["_positional"] = [_sanitize_plain(a) for a in args]
            call_args.update(kwargs)
            try:
                result = fn(*args, **kwargs)
                monitor.record_tool_call(name, call_args, time.monotonic() - t0, ok=True)
                return result
            except Exception as exc:  # noqa: BLE001
                monitor.record_tool_call(name, call_args, time.monotonic() - t0, ok=False, error=str(exc))
                raise

        return wrapper

    return deco


def _sanitize_plain(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)
