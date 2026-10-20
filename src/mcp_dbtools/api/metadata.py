"""metadata 分组：库/表/字段元数据浏览（走 jdbc 层缓存，重复查询不重复打库）。"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from .deps import API_PREFIX, Services, api_route, bad_request


def _ds(services: Services, name: str) -> Any:
    from .datasources import _find

    return _find(services, name)


def routes(services: Services) -> list:
    manager = services.manager

    async def schemas(request: Request, name: str) -> dict[str, Any]:
        ds = _ds(services, name)
        items = await _to_thread(manager.list_schemas, ds)
        return {"items": items, "total": len(items)}

    async def tables(request: Request, name: str) -> dict[str, Any]:
        ds = _ds(services, name)
        schema = request.query_params.get("schema") or None
        search = request.query_params.get("search") or None
        items = await _to_thread(manager.list_tables, ds, schema, search)
        return {"items": items, "total": len(items), "schema": schema, "search": search}

    async def columns(request: Request, name: str, table: str) -> dict[str, Any]:
        if not table:
            raise bad_request("table 必填")
        ds = _ds(services, name)
        schema = request.query_params.get("schema") or None
        items = await _to_thread(manager.describe_table, ds, table, schema)
        return {"items": items, "total": len(items), "table": table, "schema": schema}

    return [
        api_route(f"{API_PREFIX}/metadata/datasources/{{name}}/schemas", ["GET"], schemas),
        api_route(f"{API_PREFIX}/metadata/datasources/{{name}}/tables", ["GET"], tables),
        api_route(
            f"{API_PREFIX}/metadata/datasources/{{name}}/tables/{{table}}/columns", ["GET"], columns
        ),
    ]


async def _to_thread(fn: Any, *args: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, *args)
