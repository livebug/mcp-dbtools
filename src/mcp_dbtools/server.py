"""MCP 服务入口。

支持三种传输方式（由 MCP_DBTOOLS_TRANSPORT 控制）：
- streamable-http（默认）：通过 HTTP 对外提供 MCP 服务，远程客户端可直连；
- sse           ：SSE 传输（兼容旧客户端）；
- stdio         ：本地开发/测试。

HTTP 模式使用 uvicorn 启动，内置 /health 健康检查与可选的 Bearer Token 鉴权。
MCP 端点固定在 /mcp。
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from .audit_page import AUDIT_HTML
from .config import ConfigError, load_settings
from .jdbc import JDBCManager
from .monitor import Monitor, set_client_info
from .tools import register_tools

logger = logging.getLogger(__name__)

# 运行时由 build_app 设置
_mcp: FastMCP | None = None
_manager: JDBCManager | None = None


def _build_core(settings: Any) -> tuple[FastMCP, JDBCManager, Monitor]:
    mcp = FastMCP(
        "mcp-dbtools",
        instructions=(
            "数据库 MCP 服务：可通过 JDBC 连接 TDH(Inceptor)、GaussDB/openGauss 等数据源。"
            "先调用 list_datasources 查看可用数据源，再用 execute_query 执行只读 SQL，"
            "list_schemas / list_tables / describe_table 查看元数据；"
            "get_status / get_execution_history 查看监控与审计。"
        ),
        host=settings.host,
        port=settings.port,
    )
    manager = JDBCManager(settings)
    monitor = Monitor(
        history_size=settings.history_size,
        audit_file=settings.audit_file,
        audit_db=settings.audit_db,
    )
    register_tools(mcp, settings, manager, monitor)
    return mcp, manager, monitor


def build_app(settings: Any):
    """构建 ASGI 应用（MCP 端点 + /health + 可选鉴权）。

    说明：mcp.streamable_http_app()/sse_app() 返回的 Starlette app 自带
    lifespan（用于初始化 MCP session manager 的 task group），必须作为
    顶层应用交给 uvicorn，不能再次 mount 到别的 app 下，否则 lifespan
    不会执行、请求会报 "Task group is not initialized"。
    """
    global _mcp, _manager
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    mcp, manager, monitor = _build_core(settings)

    if settings.transport == "sse":
        app = mcp.sse_app()
    else:
        app = mcp.streamable_http_app()

    async def health(request: Request):
        return JSONResponse(
            {
                "status": "ok",
                "service": "mcp-dbtools",
                "transport": settings.transport,
                "datasources": [ds.name for ds in settings.datasources],
            }
        )

    async def metrics(request: Request):
        """监控指标（JSON）：数据源状态、内存、工具统计。"""
        return JSONResponse(
            {
                "service": "mcp-dbtools",
                "uptime_seconds": round(monitor.uptime_seconds(), 1),
                "datasources": manager.all_status(),
                "memory_mb": {
                    "process_rss": Monitor.process_memory_mb(),
                    "jvm_heap": Monitor.jvm_memory_mb(),
                },
                "tool_summary": monitor.tool_summary(),
                "config": {
                    "history_size": settings.history_size,
                    "audit_file": settings.audit_file,
                    "max_rows": settings.max_rows,
                },
            }
        )

    # 在 MCP 路由之前插入 /health、/metrics、/audit（Starlette 按顺序匹配）
    app.router.routes.insert(0, Route("/health", health))
    if settings.metrics_enabled:
        app.router.routes.insert(0, Route("/metrics", metrics))

    # ---------- 审计管理页面 / API ----------
    from starlette.responses import HTMLResponse

    async def audit_page(request: Request):
        return HTMLResponse(AUDIT_HTML)

    def _to_int(v: str, default: int) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    async def audit_api(request: Request):
        """人工审计查询 API。"""
        p = request.query_params
        action = p.get("action", "list")
        if action == "summary":
            return JSONResponse(monitor.audit_summary())
        if action == "get":
            item = monitor.get_audit(_to_int(p.get("id", "0"), 0))
            return JSONResponse({"item": item})
        # list / export
        page = _to_int(p.get("page", "1"), 1)
        page_size = _to_int(p.get("page_size", "10000" if action == "export" else "20"), 20)
        ok_raw = p.get("ok", "")
        ok = None if ok_raw in ("", None) else ok_raw == "1"
        data = monitor.query_audit(
            page=page,
            page_size=min(page_size, 10000),
            tool=p.get("tool") or None,
            ip=p.get("ip") or None,
            ok=ok,
            q=p.get("q") or None,
            ts_from=p.get("from") or None,
            ts_to=p.get("to") or None,
        )
        return JSONResponse(data)

    app.router.routes.insert(0, Route("/audit/api", audit_api))
    app.router.routes.insert(0, Route("/audit", audit_page))

    # ---------- 记录客户端 IP / UA（contextvar，随请求上下文传递到工具）----------
    class ClientInfoMiddleware:
        def __init__(self, inner: Any):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                headers = {
                    k.decode().lower(): v.decode()
                    for k, v in scope.get("headers", [])
                }
                client = scope.get("client") or ("", 0)
                set_client_info(
                    client_ip=client[0] if client and client[0] else None,
                    user_agent=headers.get("user-agent"),
                )
            await self.inner(scope, receive, send)

    app = ClientInfoMiddleware(app)  # type: ignore[assignment]

    # ---------- 可选 Bearer Token 鉴权 ----------
    token = settings.auth_token
    if token:
        from urllib.parse import unquote, urlsplit, parse_qsl

        def _query_token(scope: Any) -> str:
            qs = scope.get("query_string", b"").decode()
            for k, v in parse_qsl(qs):
                if k == "token":
                    return v
            return ""

        class AuthMiddleware:
            def __init__(self, inner: Any):
                self.inner = inner

            async def __call__(self, scope, receive, send):
                if scope["type"] == "lifespan":
                    await self.inner(scope, receive, send)
                    return
                if scope["type"] == "http":
                    path = scope.get("path", "")
                    # 免鉴权：健康检查 与 审计页面（HTML 壳，数据在 /audit/api 保护）
                    if path.startswith("/health") or (
                        path.startswith("/audit") and not path.startswith("/audit/api")
                    ):
                        await self.inner(scope, receive, send)
                        return
                    headers = {
                        k.decode().lower(): v.decode()
                        for k, v in scope.get("headers", [])
                    }
                    authorized = headers.get("authorization", "") == f"Bearer {token}"
                    if not authorized:
                        authorized = _query_token(scope) == token
                    if authorized:
                        await self.inner(scope, receive, send)
                        return
                    body = b'{"detail":"unauthorized"}'
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    return
                await self.inner(scope, receive, send)

        app = AuthMiddleware(app)  # type: ignore[assignment]

    return app


def run_http(settings: Any) -> None:
    import uvicorn

    app = build_app(settings)
    logger.info(
        "MCP-DBTools HTTP 服务启动: http://%s:%d/mcp (transport=%s)",
        settings.host,
        settings.port,
        settings.transport,
    )
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


def run_stdio(settings: Any) -> None:
    mcp, _, _ = _build_core(settings)
    mcp.run(transport="stdio")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="mcp-dbtools MCP 服务")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "sse", "stdio"],
        default=None,
        help="覆盖 MCP_DBTOOLS_TRANSPORT",
    )
    parser.add_argument("--host", default=None, help="覆盖监听地址")
    parser.add_argument("--port", type=int, default=None, help="覆盖监听端口")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.error("配置错误: %s", exc)
        return 1

    transport = args.transport or settings.transport
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port

    if transport == "stdio":
        run_stdio(settings)
    else:
        settings.transport = transport
        run_http(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
