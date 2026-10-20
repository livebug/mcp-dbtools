"""audit 分组：人工审计查询（来自审计 SQLite 库，未开启则返回空集）。"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from .deps import API_PREFIX, Services, api_route, not_found, page_params, qs_bool


def routes(services: Services) -> list:
    monitor = services.monitor

    async def events(request: Request) -> dict[str, Any]:
        page, page_size = page_params(request, default_size=20, max_size=200)
        tool = request.query_params.get("tool") or None
        ip = request.query_params.get("ip") or None
        q = request.query_params.get("q") or None
        ts_from = request.query_params.get("from") or None
        ts_to = request.query_params.get("to") or None
        ok_raw = request.query_params.get("ok")
        ok = None if ok_raw in (None, "") else qs_bool(request, "ok")
        return monitor.query_audit(
            page=page,
            page_size=page_size,
            tool=tool,
            ip=ip,
            ok=ok,
            q=q,
            ts_from=ts_from,
            ts_to=ts_to,
        )

    async def detail(request: Request, audit_id: str) -> dict[str, Any]:
        try:
            rid = int(audit_id)
        except (TypeError, ValueError):
            raise not_found(f"审计记录 '{audit_id}' 不存在") from None
        item = monitor.get_audit(rid)
        if item is None:
            raise not_found(f"审计记录 {rid} 不存在")
        return {"item": item}

    async def summary(request: Request) -> dict[str, Any]:
        return monitor.audit_summary()

    return [
        api_route(f"{API_PREFIX}/audit/events", ["GET"], events),
        api_route(f"{API_PREFIX}/audit/events/{{audit_id}}", ["GET"], detail),
        api_route(f"{API_PREFIX}/audit/summary", ["GET"], summary),
    ]
