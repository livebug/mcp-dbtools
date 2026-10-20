"""exports 分组：异步导出任务的创建、查询与文件下载。

任务在后台线程执行（jdbc 分页流式落盘），本分组只做任务管理。
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import FileResponse

from .deps import API_PREFIX, Services, api_route, bad_request, json_body, not_found, qs_int


def _ds(services: Services, name: str) -> Any:
    from .datasources import _find

    return _find(services, name)


def routes(services: Services) -> list:
    exports = services.exports

    async def list_(request: Request) -> dict[str, Any]:
        items = exports.list()
        total = len(items)
        page = qs_int(request, "page", 1, minimum=1)
        page_size = qs_int(request, "page_size", 50, minimum=1, maximum=200)
        start = (page - 1) * page_size
        return {
            "items": items[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_more": start + page_size < total,
        }

    async def create(request: Request) -> dict[str, Any]:
        body = await json_body(request)
        name = (body.get("datasource") or "").strip()
        sql = str(body.get("sql") or "")
        if not name:
            raise bad_request("datasource 必填")
        if not sql.strip():
            raise bad_request("sql 必填")
        ds = _ds(services, name)
        task = exports.start(
            ds,
            sql,
            delimiter=str(body.get("delimiter") or ","),
            include_header=bool(body.get("include_header", True)),
            filename=body.get("filename"),
            limit=body.get("limit"),
        )
        return {"task": task, "export_id": task["id"]}

    async def detail(request: Request, export_id: str) -> dict[str, Any]:
        task = exports.status(export_id)
        if task is None:
            raise not_found(f"导出任务 '{export_id}' 不存在")
        return {"task": task}

    async def download(request: Request, export_id: str) -> FileResponse:
        path = exports.get_file_path(export_id)
        if path is None:
            raise not_found("导出文件不存在或任务未完成")
        return FileResponse(path, media_type="text/plain", filename=path.name)

    return [
        api_route(f"{API_PREFIX}/exports", ["GET"], list_),
        api_route(f"{API_PREFIX}/exports", ["POST"], create),
        api_route(f"{API_PREFIX}/exports/{{export_id}}", ["GET"], detail),
        api_route(f"{API_PREFIX}/exports/{{export_id}}/download", ["GET"], download),
    ]
