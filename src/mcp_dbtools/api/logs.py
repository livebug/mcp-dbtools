"""logs 分组：服务日志尾部读取（服务端日志文件受轮转上限约束）。"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from ..services.logs import tail_lines
from .deps import API_PREFIX, Services, api_route, qs_int


def routes(services: Services) -> list:
    settings = services.settings

    async def tail(request: Request) -> dict[str, Any]:
        lines = qs_int(request, "lines", 200, minimum=1, maximum=2000)
        q = request.query_params.get("q") or None
        if not settings.log_file:
            return {"content": "(未配置日志文件，仅输出到控制台)", "file": None, "returned": 0, "truncated": False}
        return tail_lines(settings.log_file, lines=lines, q=q)

    return [api_route(f"{API_PREFIX}/logs", ["GET"], tail)]
