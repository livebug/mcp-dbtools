"""ASGI 应用装配层：把 MCP、REST API、Web 控制台和中间件组装成一个应用。

分层关系（详见 docs/架构设计.md）：

    server.py (CLI)  ->  app.build_app(settings)
                            ├── MCP 端点            /mcp（FastMCP，顶层 ASGI）
                            ├── 运维端点            /health, /metrics
                            ├── REST API            /api/v1/*（api/ 分组模块）
                            ├── Web 控制台          /ui/*（web/ 静态资源）
                            └── 中间件             客户端信息 / 鉴权 / 限流

注意：``mcp.streamable_http_app()`` 自带 lifespan（初始化 MCP session manager），
必须作为**顶层 ASGI 应用**，只能往它的 router 里插路由，不能把它 mount 到别的 app 下。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from .api import register_routes
from .api.deps import Services
from .export import ExportManager
from .jdbc import JDBCManager
from .monitor import Monitor, set_client_info
from .tools import register_tools
from .web import register_web

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "数据库 MCP 服务（TDH / GaussDB / openGauss 等）。先 list_datasources 选数据源，"
    "再用 execute_query 查数据、list_tables/describe_table 看元数据；写操作需 confirm=true。"
)


def build_core(settings: Any) -> tuple[FastMCP, JDBCManager, Monitor, ExportManager]:
    """构建服务层核心对象（MCP 与 HTTP 共用）。"""
    mcp = FastMCP(
        "mcp-dbtools",
        instructions=INSTRUCTIONS,
        host=settings.host,
        port=settings.port,
    )
    manager = JDBCManager(settings)
    monitor = Monitor(
        history_size=settings.history_size,
        audit_file=settings.audit_file,
        audit_db=settings.audit_db,
        audit_max_bytes=settings.audit_max_bytes,
        audit_backup_count=settings.audit_backup_count,
    )
    exports = ExportManager(
        export_dir=settings.export_dir,
        max_rows=settings.export_max_rows,
        jdbc_manager=manager,
        keep_seconds=settings.export_keep_seconds,
        max_files=settings.export_max_files,
    )
    register_tools(mcp, settings, manager, monitor, exports)
    # 后台守护：数据源探活/自动重连 + 导出文件过期清理
    manager.start_health_checker()
    exports.start_cleaner()
    return mcp, manager, monitor, exports


def build_app(settings: Any):
    """构建 ASGI 应用（MCP + API + Web + 中间件）。"""
    mcp, manager, monitor, exports = build_core(settings)
    app = mcp.sse_app() if settings.transport == "sse" else mcp.streamable_http_app()

    services = Services(
        settings=settings,
        manager=manager,
        monitor=monitor,
        exports=exports,
        started_at=time.time(),
        mcp=mcp,
    )

    # ---------- 路由：REST API 与 Web 控制台插在 MCP 路由之前 ----------
    register_routes(app, services)
    register_web(app)

    # ---------- 中间件：客户端信息 -> 鉴权 -> 限流（从内到外） ----------
    app = _client_info_middleware(app)
    if settings.auth_token:
        app = _auth_middleware(app, settings.auth_token)
    if settings.rate_limit_enabled:
        from .ratelimit import RateLimitMiddleware

        app = RateLimitMiddleware(
            app,
            qps=settings.rate_limit_qps,
            burst=settings.rate_limit_burst,
            exempt_paths=("/health", "/ui"),
        )  # type: ignore[assignment]

    return app


def _client_info_middleware(app: Any) -> Any:
    """把客户端 IP / UA 写入 contextvar，供审计记录使用。"""

    class ClientInfoMiddleware:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
                client = scope.get("client") or ("", 0)
                set_client_info(
                    client_ip=client[0] if client and client[0] else None,
                    user_agent=headers.get("user-agent"),
                )
            await self.inner(scope, receive, send)

    return ClientInfoMiddleware(app)


def _auth_middleware(app: Any, token: str) -> Any:
    """Bearer Token 鉴权。

    公开路径（无需 Token）：
    - ``/health``：探针；
    - ``/ui`` 与 ``/ui/*``：控制台外壳与静态资源（数据仍走受保护的 /api/v1）。

    Token 可放在 ``Authorization: Bearer <token>`` 或 ``?token=``（兼容 MCP 客户端）。
    """
    from urllib.parse import parse_qsl

    def _query_token(scope: Any) -> str:
        for k, v in parse_qsl(scope.get("query_string", b"").decode()):
            if k == "token":
                return v
        return ""

    def _public(path: str) -> bool:
        return path == "/health" or path == "/ui" or path.startswith("/ui/")

    class AuthMiddleware:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or _public(scope.get("path", "")):
                await self.inner(scope, receive, send)
                return
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            if headers.get("authorization", "") == f"Bearer {token}" or _query_token(scope) == token:
                await self.inner(scope, receive, send)
                return
            body = json.dumps(
                {"ok": False, "error": {"code": "UNAUTHORIZED", "message": "未认证或 Token 无效"}},
                ensure_ascii=False,
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

    return AuthMiddleware(app)


def run_http(settings: Any) -> None:
    import uvicorn

    app = build_app(settings)
    logger.info(
        "mcp-dbtools 启动: MCP=http://%s:%d/mcp  控制台=http://%s:%d/ui/  API=http://%s:%d/api/v1 "
        "(mode=%s, transport=%s)",
        settings.host,
        settings.port,
        settings.host,
        settings.port,
        settings.host,
        settings.port,
        settings.mode,
        settings.transport,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


def run_stdio(settings: Any) -> None:
    mcp, _, _, _ = build_core(settings)
    mcp.run(transport="stdio")
