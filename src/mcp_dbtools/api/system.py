"""system 分组：服务元信息、健康检查、指标、运行配置。

同时承载两个**运维端点**（不在 /api/v1 下，供负载均衡/K8s 探针与抓取器使用）：

- ``GET /health``  ：存活/就绪检查（默认浅检查，``?deep=1`` 逐个数据源真实探活）；
- ``GET /metrics`` ：监控指标 JSON（``MCP_DBTOOLS_METRICS_ENABLED`` 控制开关）。
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import __version__ as PKG_VERSION
from .deps import API_PREFIX, Services, api_route, qs_bool


def _version() -> str:
    """以包内 `__version__` 为准（editable 安装的 dist 元数据可能过期）。"""
    return PKG_VERSION


def health_payload(services: Services, deep: bool) -> dict[str, Any]:
    settings, manager = services.settings, services.manager
    checks = [manager.health(ds, deep=deep) for ds in settings.datasources]
    return {
        "status": "ok" if all(c["ok"] for c in checks) else "degraded",
        "service": "mcp-dbtools",
        "version": _version(),
        "mode": services.mode,
        "transport": settings.transport,
        "datasources": checks,
    }


def metrics_payload(services: Services) -> dict[str, Any]:
    settings, manager, monitor = services.settings, services.manager, services.monitor
    return {
        "service": "mcp-dbtools",
        "version": _version(),
        "uptime_seconds": round(monitor.uptime_seconds(), 1),
        "datasources": manager.all_status(),
        "memory_mb": {
            "process_rss": monitor.process_memory_mb(),
            "jvm_heap": monitor.jvm_memory_mb(),
        },
        "tool_summary": monitor.tool_summary(),
        "meta_cache": manager.meta_cache_stats(),
        "transactions": manager.all_transaction_status(),
        "config": {
            "mode": services.mode,
            "history_size": settings.history_size,
            "audit_file": settings.audit_file,
            "max_rows": settings.max_rows,
            "rate_limit_enabled": settings.rate_limit_enabled,
            "rate_limit_qps": settings.rate_limit_qps,
            "meta_cache_ttl": settings.meta_cache_ttl,
        },
    }


def routes(services: Services) -> list[Route]:
    settings = services.settings

    async def ops_health(request: Request) -> JSONResponse:
        deep = qs_bool(request, "deep", False)
        return JSONResponse(health_payload(services, deep))

    async def ops_metrics(request: Request) -> JSONResponse:
        return JSONResponse(metrics_payload(services))

    async def info(request: Request) -> dict[str, Any]:
        bridge_mode = "remote" if getattr(settings, "bridge_url", None) else "spawn"
        return {
            "service": "mcp-dbtools",
            "version": _version(),
            "mode": services.mode,
            "write_allowed": services.write_allowed,
            "auth_required": bool(settings.auth_token),
            "transport": settings.transport,
            "endpoints": {
                "mcp": "/mcp",
                "api": API_PREFIX,
                "ui": "/ui/",
                "health": "/health",
                "metrics": "/metrics",
            },
            "bridge": {
                "mode": bridge_mode,
                "jar": getattr(settings, "bridge_jar", None) if bridge_mode == "spawn" else None,
                "url": getattr(settings, "bridge_url", None),
                "alive": bool(getattr(services.manager, "_bridge", None) and services.manager._bridge.alive),
            },
            "datasources": [
                {"name": ds.name, "type": ds.type, "description": getattr(ds, "description", "")}
                for ds in settings.datasources
            ],
        }

    async def config(request: Request) -> dict[str, Any]:
        """运行配置（脱敏）：只暴露排障需要的标量，不含密码/URL 凭据。"""
        return {
            "mode": services.mode,
            "transport": settings.transport,
            "host": settings.host,
            "port": settings.port,
            "max_rows": settings.max_rows,
            "max_rows_limit": settings.max_rows_limit,
            "pool_size": settings.pool_size,
            "connect_timeout": settings.connect_timeout,
            "meta_cache_ttl": settings.meta_cache_ttl,
            "tx_timeout": settings.tx_timeout,
            "export_max_rows": settings.export_max_rows,
            "export_keep_seconds": settings.export_keep_seconds,
            "history_size": settings.history_size,
            "audit_enabled": bool(settings.audit_file or settings.audit_db),
            "rate_limit": {
                "enabled": settings.rate_limit_enabled,
                "qps": settings.rate_limit_qps,
                "burst": settings.rate_limit_burst,
            },
            "bridge": {
                "timeout": settings.bridge_timeout,
                "shared": bool(settings.bridge_url),
                "tenant_id": settings.tenant_id,
            },
            "security": {
                "auth_required": bool(settings.auth_token),
                "allow_remote_edit": bool(settings.allow_remote_edit),
            },
        }

    async def health(request: Request) -> dict[str, Any]:
        return health_payload(services, qs_bool(request, "deep", False))

    async def metrics(request: Request) -> dict[str, Any]:
        return metrics_payload(services)

    out = [
        Route("/health", ops_health),
        api_route(f"{API_PREFIX}/system/health", ["GET"], health),
        api_route(f"{API_PREFIX}/system/info", ["GET"], info),
        api_route(f"{API_PREFIX}/system/config", ["GET"], config),
        api_route(f"{API_PREFIX}/system/metrics", ["GET"], metrics),
    ]
    if settings.metrics_enabled:
        out.insert(1, Route("/metrics", ops_metrics))
    return out
