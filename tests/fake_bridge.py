"""测试共用：JDBCManager 的假桥替身，避免任何真实 JVM/驱动依赖。"""

from __future__ import annotations

from typing import Any, Callable

from mcp_dbtools.jdbc import JDBCManager


class FakeBridge:
    """JDBCManager 的测试替身。

    ``respond`` 把 method 映射到：
    - dict：直接作为 result 返回；
    - callable(params) -> dict：按请求参数动态生成结果；
    - Exception：请求时抛出（会被 JDBCManager._request 包成 JDBCError）。
    """

    def __init__(self, respond: dict[str, Any] | None = None):
        self.respond: dict[str, Any] = dict(respond or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._alive = True

    def start(self) -> None:
        self._alive = True

    def close(self) -> None:
        self._alive = False

    @property
    def alive(self) -> bool:
        return self._alive

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        p = dict(params or {})
        self.calls.append((method, p))
        handler = self.respond.get(method)
        if handler is None:
            return {}
        if callable(handler):
            return handler(p)
        if isinstance(handler, Exception):
            raise handler
        return handler

    def count(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)

    def last_params(self, method: str) -> dict[str, Any]:
        for m, p in reversed(self.calls):
            if m == method:
                return p
        return {}


def make_manager(settings, respond: dict[str, Any] | None = None) -> JDBCManager:
    """构造 JDBCManager 并注入假桥。"""
    manager = JDBCManager(settings)
    manager._bridge = FakeBridge(respond)
    return manager


def query_result(
    rows: list[list[Any]] | None = None,
    columns: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    update_count: int | None = None,
) -> dict[str, Any]:
    """构造 query.execute 的典型响应（行规范化为 list，与 JSON 线格式一致）。"""
    if update_count is not None:
        return {"hasResultSet": False, "updateCount": update_count, "elapsedMillis": 1}
    cols = columns or [{"name": "id", "label": "id"}]
    normalized = [list(r) for r in (rows or [])]
    return {
        "hasResultSet": True,
        "queryId": "q1",
        "columns": cols,
        "rows": normalized,
        "totalRows": len(normalized),
        "truncated": truncated,
        "elapsedMillis": 1,
    }


def connection_open_response(name: str = "Test", version: str = "1.0") -> Callable[[dict], dict]:
    def _open(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "connectionId": params["connectionId"],
            "driverClassName": params["driverClassName"],
            "capabilities": {
                "databaseProductName": name,
                "databaseProductVersion": version,
                "supportsTransactions": True,
            },
        }
    return _open
