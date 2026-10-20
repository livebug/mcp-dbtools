"""API 层公共设施：统一响应包、错误码、参数解析、服务容器、路由装配。

约定（详见 docs/架构设计.md）：

- 所有 REST 端点位于 ``/api/v1``，成功 ``{"ok": true, "data": ...}``，
  失败 ``{"ok": false, "error": {"code", "message", "details"}}``；
- 列表类响应统一为 ``{"items", "page", "page_size", "total", "has_more"}``；
- 业务异常抛 :class:`ApiError`，由 :func:`api_route` 统一转成 HTTP 响应。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


class ApiError(Exception):
    """带 HTTP 状态与稳定错误码的业务异常。"""

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


# 常用错误构造器，保证全站错误码一致
def bad_request(message: str, details: Any = None) -> ApiError:
    return ApiError(400, "INVALID_PARAM", message, details)


def unauthorized(message: str = "未认证或 Token 无效") -> ApiError:
    return ApiError(401, "UNAUTHORIZED", message)


def forbidden(message: str, details: Any = None) -> ApiError:
    return ApiError(403, "FORBIDDEN", message, details)


def not_found(message: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", message)


def conflict(message: str) -> ApiError:
    return ApiError(409, "CONFLICT", message)


def unavailable(message: str, details: Any = None) -> ApiError:
    return ApiError(503, "UPSTREAM_UNAVAILABLE", message, details)


@dataclass
class Services:
    """应用级依赖容器，由 ``app.build_app`` 注入各路由模块。"""

    settings: Any
    manager: Any
    monitor: Any
    exports: Any
    started_at: float
    mcp: Any = None  # FastMCP 实例，供 /api/v1/mcp 自描述工具清单

    @property
    def mode(self) -> str:
        return getattr(self.settings, "mode", "local")

    @property
    def write_allowed(self) -> bool:
        """是否允许经 API 修改数据源配置（远程模式默认只读）。"""
        return self.mode != "remote" or bool(getattr(self.settings, "allow_remote_edit", False))


# ----------------------------------------------------------------------
# 响应构造
# ----------------------------------------------------------------------


def ok(data: Any = None, **extra: Any) -> JSONResponse:
    payload: dict[str, Any] = {"ok": True, "data": data}
    payload.update(extra)
    return JSONResponse(payload)


def fail(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"ok": False, "error": error}, status_code=status)


def paginated(items: list[Any], page: int, page_size: int, **extra: Any) -> dict[str, Any]:
    total = len(items)
    start = (page - 1) * page_size
    out: dict[str, Any] = {
        "items": items[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_more": start + page_size < total,
    }
    out.update(extra)
    return out


# ----------------------------------------------------------------------
# 参数解析
# ----------------------------------------------------------------------


def qs_int(request: Request, name: str, default: int, minimum: int = 0, maximum: int = 10**9) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        return max(minimum, min(maximum, int(raw)))
    except (TypeError, ValueError):
        raise bad_request(f"参数 {name} 必须是整数") from None


def qs_bool(request: Request, name: str, default: bool = False) -> bool:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def page_params(request: Request, default_size: int = 20, max_size: int = 200) -> tuple[int, int]:
    return (
        qs_int(request, "page", 1, minimum=1),
        qs_int(request, "page_size", default_size, minimum=1, maximum=max_size),
    )


async def json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 解析失败统一按参数错误处理
        raise bad_request("请求体必须是合法 JSON") from None
    if not isinstance(body, dict):
        raise bad_request("请求体必须是 JSON 对象")
    return body


# ----------------------------------------------------------------------
# 路由装配
# ----------------------------------------------------------------------

Handler = Callable[..., Awaitable[Any]]


def api_route(path: str, methods: list[str], handler: Handler) -> Route:
    """包装 handler：统一异常 → 统一响应包。"""

    async def endpoint(request: Request) -> JSONResponse:
        try:
            # Starlette 的普通 Route 不会把路径参数传给 endpoint，从 request 取
            result = await handler(request, **request.path_params)
            if isinstance(result, Response):
                return result
            return ok(result)
        except ApiError as exc:
            return fail(exc.status, exc.code, exc.message, exc.details)
        except ValueError as exc:  # 配置/参数类可预期错误
            return fail(400, "INVALID_PARAM", str(exc))
        except Exception:  # noqa: BLE001 兜底：记录堆栈，响应不暴露内部细节
            logger.exception("API 处理失败: %s %s", request.method, request.url.path)
            return fail(500, "INTERNAL_ERROR", "服务内部错误，请查看服务日志")

    return Route(path, endpoint, methods=methods)


def register_routes(app: Any, services: Services) -> None:
    """把所有 API 路由插到 MCP 路由之前（Starlette 按注册顺序匹配）。"""
    from . import audit, datasources, exports, logs, mcp as mcp_api, metadata, query, system

    routes: list[Route] = []
    for module in (system, datasources, mcp_api, query, metadata, exports, audit, logs):
        routes.extend(module.routes(services))
    app.router.routes[0:0] = routes
