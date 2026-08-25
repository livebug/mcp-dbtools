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
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .audit_page import AUDIT_HTML
from .config import ConfigError, load_settings
from . import datasources_page as ds_page
from .export import ExportManager
from .home_page import HOME_HTML
from .jdbc import JDBCManager
from .logs_page import LOGS_HTML, tail_lines
from .monitor import Monitor, set_client_info
from .tools import register_tools

logger = logging.getLogger(__name__)

# 运行时由 build_app 设置
_mcp: FastMCP | None = None
_manager: JDBCManager | None = None


def _build_core(settings: Any) -> tuple[FastMCP, JDBCManager, Monitor, ExportManager]:
    mcp = FastMCP(
        "mcp-dbtools",
        instructions=(
            "数据库 MCP 服务：可通过 JDBC 连接 TDH(Inceptor)、GaussDB/openGauss 等数据源。"
            "先调用 list_datasources 查看可用数据源，再用 execute_query 执行只读 SQL，"
            "list_schemas / list_tables / describe_table 查看元数据；"
            "get_status / get_execution_history 查看监控与审计；"
            "大数据量用 start_export 异步导出。"
        ),
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
    export_mgr = ExportManager(
        export_dir=settings.export_dir,
        max_rows=settings.export_max_rows,
        jdbc_manager=manager,
        keep_seconds=settings.export_keep_seconds,
        max_files=settings.export_max_files,
    )
    register_tools(mcp, settings, manager, monitor, export_mgr)
    # 后台守护：数据源探活/自动重连 + 导出文件过期清理
    manager.start_health_checker()
    export_mgr.start_cleaner()
    return mcp, manager, monitor, export_mgr


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

    mcp, manager, monitor, export_mgr = _build_core(settings)

    if settings.transport == "sse":
        app = mcp.sse_app()
    else:
        app = mcp.streamable_http_app()

    async def health(request: Request):
        # 默认浅检查（缓存状态）；?deep=1 时对每个数据源真实执行 SELECT 1
        deep = request.query_params.get("deep", "").lower() in ("1", "true", "yes", "on")
        checks = [manager.health(ds, deep=deep) for ds in settings.datasources]
        all_ok = all(c["ok"] for c in checks)
        return JSONResponse(
            {
                "status": "ok" if all_ok else "degraded",
                "service": "mcp-dbtools",
                "transport": settings.transport,
                "datasources": checks,
            }
        )

    async def metrics(request: Request):
        """监控指标（JSON）：数据源状态、内存、工具统计、缓存与事务。"""
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
                "meta_cache": manager.meta_cache_stats(),
                "transactions": manager.all_transaction_status(),
                "config": {
                    "history_size": settings.history_size,
                    "audit_file": settings.audit_file,
                    "max_rows": settings.max_rows,
                    "rate_limit_enabled": settings.rate_limit_enabled,
                    "rate_limit_qps": settings.rate_limit_qps,
                    "meta_cache_ttl": settings.meta_cache_ttl,
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

    # ---------- 服务日志查看页面 / API ----------
    async def logs_page(request: Request):
        return HTMLResponse(LOGS_HTML)

    async def logs_api(request: Request):
        """服务日志查询 API：action=tail（尾部 N 行，可按关键字过滤）。"""
        p = request.query_params
        action = p.get("action", "tail")
        if action != "tail":
            return JSONResponse({"error": f"未知 action: {action}"}, status_code=400)
        lines = _to_int(p.get("lines", "200"), 200)
        q = p.get("q") or None
        if settings.log_file:
            data = tail_lines(settings.log_file, lines=lines, q=q)
        else:
            data = {"content": "(未配置日志文件，仅输出到控制台)", "file": None}
        return JSONResponse(data)

    app.router.routes.insert(0, Route("/logs/api", logs_api))
    app.router.routes.insert(0, Route("/logs", logs_page))

    # ---------- 首页导航 ----------
    async def home_page(request: Request):
        return HTMLResponse(HOME_HTML)

    app.router.routes.insert(0, Route("/", home_page))

    # ---------- 数据源管理页面 / API（密码自动加密） ----------
    async def datasources_page(request: Request):
        return HTMLResponse(ds_page.page_html())

    async def datasources_api(request: Request):
        return JSONResponse(ds_page.api_list(settings.config_path))

    async def datasources_save(request: Request):
        form = await request.form()
        ok, msg = ds_page.api_save(settings.config_path, dict(form))
        return JSONResponse({"ok": True, "message": msg} if ok else {"ok": False, "error": msg})

    async def datasources_delete(request: Request):
        form = await request.form()
        ok, msg = ds_page.api_delete(settings.config_path, (form.get("name") or "").strip())
        return JSONResponse({"ok": True, "message": msg} if ok else {"ok": False, "error": msg})

    app.router.routes.insert(0, Route("/datasources/api/save", datasources_save, methods=["POST"]))
    app.router.routes.insert(0, Route("/datasources/api/delete", datasources_delete, methods=["POST"]))
    app.router.routes.insert(0, Route("/datasources/api", datasources_api))
    app.router.routes.insert(0, Route("/datasources", datasources_page))

    # ---------- 导出文件下载 ----------
    from starlette.responses import FileResponse

    async def export_download(request: Request):
        eid = request.path_params.get("id", "")
        path = export_mgr.get_file_path(eid)
        if path is None:
            return JSONResponse({"error": "导出文件不存在或任务未完成"}, status_code=404)
        return FileResponse(path, media_type="text/plain", filename=path.name)

    app.router.routes.insert(0, Route("/export/{id}/download", export_download))

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
                    # 免鉴权：健康检查 与 审计/日志 页面（HTML 壳，数据接口受保护）
                    if path.startswith("/health") or (
                        path.startswith("/audit") and not path.startswith("/audit/api")
                    ) or (
                        path.startswith("/logs") and not path.startswith("/logs/api")
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

    # ---------- 可选按客户端 IP 的 QPS 限流（最外层，保护所有接口）----------
    if settings.rate_limit_enabled:
        from .ratelimit import RateLimitMiddleware

        app = RateLimitMiddleware(
            app,
            qps=settings.rate_limit_qps,
            burst=settings.rate_limit_burst,
            exempt_paths=("/health",),
        )  # type: ignore[assignment]

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
    mcp, _, _, _ = _build_core(settings)
    mcp.run(transport="stdio")


def _setup_logging(settings: Any) -> None:
    """配置日志：输出到控制台，并可选落盘（按大小轮转，便于服务器上查看）。"""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        try:
            Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                settings.log_file,
                maxBytes=max(1024, settings.log_max_bytes),
                backupCount=max(1, settings.log_backup_count),
                encoding="utf-8",
            )
            handlers.append(fh)
        except OSError as exc:
            logger.warning("日志文件不可写，仅输出控制台: %s", exc)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
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

    _setup_logging(settings)

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
