"""mcp 分组：MCP 能力自描述（工具清单与参数 schema）。

目的：客户端不必依赖文档即可发现服务能力 ——
``GET /api/v1/mcp/tools`` 返回与 MCP ``tools/list`` 同源的工具名/描述/参数 schema，
Web 控制台的「工具」页也读同一接口。
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from .. import __version__ as PKG_VERSION
from .deps import API_PREFIX, Services, api_route, not_found

# 工具分组（仅用于展示与筛选；未知工具归入「其他」）
GROUPS: dict[str, list[str]] = {
    "连接与元数据": ["list_datasources", "test_connection", "list_schemas", "list_tables", "describe_table"],
    "查询与脚本": ["execute_query", "execute_script", "save_script"],
    "事务": [
        "begin_transaction",
        "execute_in_transaction",
        "commit_transaction",
        "rollback_transaction",
        "get_transaction_status",
    ],
    "监控与审计": ["get_status", "get_datasource_status", "get_circuit_status", "get_execution_history"],
    "导出": ["start_export", "get_export_status", "list_exports"],
}

_TOOL_GROUP = {name: group for group, names in GROUPS.items() for name in names}


def _all_tools(services: Services) -> list[Any]:
    """取 FastMCP 已注册的工具对象（与 MCP tools/list 同源）。"""
    mcp = getattr(services, "mcp", None)
    if mcp is None:
        return []
    try:
        return list(mcp._tool_manager.list_tools())
    except Exception:  # noqa: BLE001 内部结构变化时不影响其余 API
        return []


def _tool_payload(tool: Any) -> dict[str, Any]:
    params = tool.parameters or {}
    props = params.get("properties") or {}
    return {
        "name": tool.name,
        "title": getattr(tool, "title", None),
        "description": tool.description or "",
        "group": _TOOL_GROUP.get(tool.name, "其他"),
        "parameters": params,
        "requires_confirm": "confirm" in props,
    }


def routes(services: Services) -> list:
    async def info(request: Request) -> dict[str, Any]:
        tools = _all_tools(services)
        return {
            "server": "mcp-dbtools",
            "version": PKG_VERSION,
            "protocol": "mcp",
            "transport": services.settings.transport,
            "endpoint": "/mcp",
            "tool_count": len(tools),
            "groups": GROUPS,
            "tools_endpoint": f"{API_PREFIX}/mcp/tools",
            "ui": "/ui/tools",
        }

    async def tools(request: Request) -> dict[str, Any]:
        group = request.query_params.get("group") or None
        items = [_tool_payload(t) for t in _all_tools(services)]
        if group:
            items = [i for i in items if i["group"] == group]
        items.sort(key=lambda i: (i["group"], i["name"]))
        return {"items": items, "total": len(items), "groups": [g for g in GROUPS if not group or g == group]}

    async def tool_detail(request: Request, name: str) -> dict[str, Any]:
        for t in _all_tools(services):
            if t.name == name:
                return {"item": _tool_payload(t)}
        raise not_found(f"MCP 工具 '{name}' 不存在")

    return [
        api_route(f"{API_PREFIX}/mcp/info", ["GET"], info),
        api_route(f"{API_PREFIX}/mcp/tools", ["GET"], tools),
        api_route(f"{API_PREFIX}/mcp/tools/{{name}}", ["GET"], tool_detail),
    ]
