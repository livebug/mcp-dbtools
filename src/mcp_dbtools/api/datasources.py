"""datasources 分组：数据源配置的查询与维护。

- 列表/详情永远脱敏（只回 ``password_set`` / ``password_kind``）；
- 写操作（新增/编辑/删除）在 remote 模式下默认关闭，需显式
  ``MCP_DBTOOLS_ALLOW_REMOTE_EDIT=1`` 才开放；
- 修改写入配置文件，**重启服务生效**（运行中的连接池不会热更新）。
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request

from ..services import datasources as store
from .deps import (
    API_PREFIX,
    Services,
    api_route,
    bad_request,
    conflict,
    forbidden,
    json_body,
    not_found,
    unavailable,
)

WRITE_DISABLED = "远程部署默认禁止修改数据源配置（如需开放请设置 MCP_DBTOOLS_ALLOW_REMOTE_EDIT=1）"


def _find(services: Services, name: str) -> Any:
    for ds in services.settings.datasources:
        if ds.name == name:
            return ds
    raise not_found(f"数据源 '{name}' 不存在")


def _require_write(services: Services) -> None:
    if not services.write_allowed:
        raise forbidden(WRITE_DISABLED)


def routes(services: Services) -> list:
    cfg = services.settings.config_path

    async def list_(request: Request) -> dict[str, Any]:
        items = [store.mask(d) for d in store.list_datasources(cfg)]
        return {"items": items, "total": len(items), "mode": services.mode, "write_allowed": services.write_allowed}

    async def detail(request: Request, name: str) -> dict[str, Any]:
        entry = store.get_raw(cfg, name)
        if entry is None:
            raise not_found(f"数据源 '{name}' 不存在")
        return {"item": store.mask(entry), "write_allowed": services.write_allowed}

    async def create(request: Request) -> dict[str, Any]:
        _require_write(services)
        body = await json_body(request)
        ok, msg = store.save(cfg, body)
        if not ok:
            raise conflict(msg) if "已存在" in msg else bad_request(msg)
        return {"message": msg, "name": body.get("name")}

    async def update(request: Request, name: str) -> dict[str, Any]:
        _require_write(services)
        body = await json_body(request)
        new_name = (body.get("name") or name).strip()
        if new_name != name:
            raise bad_request("不支持重命名数据源，请新建后删除旧项")
        body["original"] = name
        ok, msg = store.save(cfg, body)
        if not ok:
            raise bad_request(msg)
        return {"message": msg, "name": name}

    async def remove(request: Request, name: str) -> dict[str, Any]:
        _require_write(services)
        ok, msg = store.delete(cfg, name)
        if not ok:
            raise not_found(msg)
        return {"message": msg, "name": name}

    async def test(request: Request, name: str) -> dict[str, Any]:
        ds = _find(services, name)
        try:
            return services.manager.test_connection(ds)
        except Exception as exc:  # noqa: BLE001 连接失败是预期结果，转成 503
            raise unavailable(f"数据源 '{name}' 连接失败: {exc}") from exc

    return [
        api_route(f"{API_PREFIX}/datasources", ["GET"], list_),
        api_route(f"{API_PREFIX}/datasources", ["POST"], create),
        api_route(f"{API_PREFIX}/datasources/{{name}}", ["GET"], detail),
        api_route(f"{API_PREFIX}/datasources/{{name}}", ["PUT"], update),
        api_route(f"{API_PREFIX}/datasources/{{name}}", ["DELETE"], remove),
        api_route(f"{API_PREFIX}/datasources/{{name}}/test", ["POST"], test),
    ]
