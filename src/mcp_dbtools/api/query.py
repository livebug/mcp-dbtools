"""query 分组：SQL 执行与执行历史。

安全约定与 MCP 工具保持一致：写操作必须显式 ``confirm=true``；
REST 侧执行的 SQL 同样写审计（工具名带 ``api.`` 前缀，便于区分来源）。
"""

from __future__ import annotations

import time
from typing import Any

from starlette.requests import Request

from ..monitor import get_client_info
from .deps import API_PREFIX, Services, api_route, bad_request, json_body, qs_bool, qs_int


def _ds(services: Services, name: str) -> Any:
    from .datasources import _find  # 复用同一套"数据源不存在"语义

    return _find(services, name)


def routes(services: Services) -> list:
    manager, monitor = services.manager, services.monitor

    async def execute(request: Request) -> dict[str, Any]:
        body = await json_body(request)
        name = (body.get("datasource") or "").strip()
        sql = body.get("sql") or ""
        if not name:
            raise bad_request("datasource 必填")
        if not str(sql).strip():
            raise bad_request("sql 必填")
        ds = _ds(services, name)
        limit = body.get("limit")
        if limit is not None:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                raise bad_request("limit 必须是整数") from None
        confirm = bool(body.get("confirm", False))

        t0 = time.monotonic()
        client = get_client_info()
        try:
            result = await _to_thread(manager.execute_query, ds, str(sql), limit, confirm)
        except Exception as exc:
            monitor.record_tool_call(
                "api.query.execute",
                {"datasource": name, "sql": str(sql)[:500], "confirm": confirm},
                time.monotonic() - t0,
                ok=False,
                error=str(exc),
                client_ip=client.get("client_ip"),
                user_agent=client.get("user_agent"),
            )
            raise
        monitor.record_tool_call(
            "api.query.execute",
            {"datasource": name, "sql": str(sql)[:500], "confirm": confirm},
            time.monotonic() - t0,
            ok=True,
            client_ip=client.get("client_ip"),
            user_agent=client.get("user_agent"),
        )
        return result

    async def history(request: Request) -> dict[str, Any]:
        limit = qs_int(request, "limit", 50, minimum=1, maximum=500)
        tool = request.query_params.get("tool") or None
        ok_raw = request.query_params.get("ok")
        ok = None if ok_raw in (None, "") else qs_bool(request, "ok")
        items = monitor.execution_history(limit=limit, tool=tool, ok=ok)
        return {"items": items, "total": len(items)}

    async def summary(request: Request) -> dict[str, Any]:
        return {"tool_summary": monitor.tool_summary(), "uptime_seconds": round(monitor.uptime_seconds(), 1)}

    return [
        api_route(f"{API_PREFIX}/query/execute", ["POST"], execute),
        api_route(f"{API_PREFIX}/query/history", ["GET"], history),
        api_route(f"{API_PREFIX}/query/summary", ["GET"], summary),
    ]


async def _to_thread(fn: Any, *args: Any) -> Any:
    """SQL 执行可能很慢，放到线程池，避免阻塞事件循环。"""
    import asyncio

    return await asyncio.to_thread(fn, *args)
