"""REST API（/api/v1）与控制台路由测试：鉴权、统一响应包、脱敏、远程只读。

不依赖真实数据库：只覆盖不触发 SQL 的端点；桥相关行为由 test_metadata/test_safety 覆盖。
"""

from __future__ import annotations

import json

import httpx
import pytest

from mcp_dbtools.app import build_app
from mcp_dbtools.config import DataSource, Settings

TOKEN = "test-token"


def _ds(name: str = "g") -> DataSource:
    return DataSource(
        name=name,
        type="gaussdb",
        jdbc_url="jdbc:opengauss://h:5432/db",
        driver_class="org.opengauss.Driver",
        jars=["x.jar"],
    )


def _settings(tmp_path, **overrides) -> Settings:
    cfg = tmp_path / "datasources.json"
    cfg.write_text(
        json.dumps(
            {
                "datasources": [
                    {
                        "name": "g",
                        "type": "gaussdb",
                        "jdbc_url": "jdbc:opengauss://h:5432/db",
                        "driver_class": "org.opengauss.Driver",
                        "username": "u",
                        "password": "{ENC:cipher}",
                        "jars": ["x.jar"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    base = dict(
        auth_token=TOKEN,
        config_path=str(cfg),
        datasources=[_ds()],
        metrics_enabled=True,
        rate_limit_enabled=False,
        log_file=None,
        audit_file=None,
        audit_db=None,
        export_dir=str(tmp_path / "exports"),
    )
    base.update(overrides)
    return Settings(**base)


def _client(settings):
    app = build_app(settings)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


AUTH = {"Authorization": f"Bearer {TOKEN}"}


# ---------- 鉴权与公开路径 ----------


async def test_health_is_public_and_plain(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        r = await c.get("/health")
        assert r.status_code == 200
        # 运维端点保持"裸"结构（探针/抓取器直接用），不套响应包
        assert r.json()["service"] == "mcp-dbtools"
        assert r.json()["datasources"][0]["datasource"] == "g"


async def test_ui_shell_is_public_but_data_is_not(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        page = await c.get("/ui/logs")
        assert page.status_code == 200
        assert "/ui/assets/app.js" in page.text
        asset = await c.get("/ui/assets/app.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        # 数据接口仍需 Token
        assert (await c.get("/api/v1/logs")).status_code == 401
        assert (await c.get("/api/v1/system/info", headers=AUTH)).status_code == 200


async def test_unauthorized_envelope(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        r = await c.get("/api/v1/system/info")
        assert r.status_code == 401
        body = r.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "UNAUTHORIZED"


# ---------- 统一响应包与元信息 ----------


async def test_system_info_envelope(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        r = await c.get("/api/v1/system/info", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        info = body["data"]
        assert info["endpoints"] == {
            "mcp": "/mcp",
            "api": "/api/v1",
            "ui": "/ui/",
            "health": "/health",
            "metrics": "/metrics",
        }
        assert info["auth_required"] is True
        assert info["mode"] == "local"
        assert [d["name"] for d in info["datasources"]] == ["g"]


async def test_metrics_available(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        assert (await c.get("/metrics", headers=AUTH)).status_code == 200
        body = (await c.get("/api/v1/system/metrics", headers=AUTH)).json()
        assert body["ok"] is True and "tool_summary" in body["data"]


# ---------- 数据源：脱敏 ----------


async def test_datasource_list_is_masked(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        body = (await c.get("/api/v1/datasources", headers=AUTH)).json()
        item = body["data"]["items"][0]
        assert "password" not in item
        assert item["password_set"] is True
        assert item["password_kind"] == "encrypted"
        assert body["data"]["write_allowed"] is True


async def test_remote_mode_readonly_by_default(tmp_path):
    settings = _settings(tmp_path, mode="remote")
    async with _client(settings) as c:
        body = (await c.get("/api/v1/datasources", headers=AUTH)).json()
        assert body["data"]["write_allowed"] is False
        r = await c.post("/api/v1/datasources", headers=AUTH, json={"name": "x", "password": "p"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_remote_mode_can_be_opened_explicitly(tmp_path):
    settings = _settings(tmp_path, mode="remote", allow_remote_edit=True)
    async with _client(settings) as c:
        body = (await c.get("/api/v1/datasources", headers=AUTH)).json()
        assert body["data"]["write_allowed"] is True


# ---------- 数据源：增删改查 ----------


async def test_datasource_crud(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_DBTOOLS_SECRET_KEY", "test-secret")
    async with _client(_settings(tmp_path)) as c:
        payload = {
            "name": "newdb",
            "type": "mysql",
            "jdbc_url": "jdbc:mysql://h:3306/db",
            "driver_class": "com.mysql.cj.jdbc.Driver",
            "username": "u",
            "password": "p@ss",
            "jars": "mysql.jar",
            "description": "测试",
        }
        created = await c.post("/api/v1/datasources", headers=AUTH, json=payload)
        assert created.status_code == 200, created.text
        assert created.json()["ok"] is True

        listed = (await c.get("/api/v1/datasources", headers=AUTH)).json()["data"]
        names = [d["name"] for d in listed["items"]]
        assert "newdb" in names

        # 重复创建 -> 409
        dup = await c.post("/api/v1/datasources", headers=AUTH, json=payload)
        assert dup.status_code == 409
        assert dup.json()["error"]["code"] == "CONFLICT"

        # 编辑：密码留空保持原值
        payload2 = dict(payload, password="", jdbc_url="jdbc:mysql://h:3307/db")
        upd = await c.put("/api/v1/datasources/newdb", headers=AUTH, json=payload2)
        assert upd.status_code == 200, upd.text

        # 重命名被拒绝
        bad = await c.put("/api/v1/datasources/newdb", headers=AUTH, json=dict(payload, name="other"))
        assert bad.status_code == 400

        deleted = await c.delete("/api/v1/datasources/newdb", headers=AUTH)
        assert deleted.status_code == 200
        assert (await c.delete("/api/v1/datasources/newdb", headers=AUTH)).status_code == 404


async def test_unknown_datasource_404(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        r = await c.get("/api/v1/metadata/datasources/nope/schemas", headers=AUTH)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"


# ---------- 审计 / 查询历史 / 日志 ----------


async def test_audit_events_shape(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        body = (await c.get("/api/v1/audit/events", headers=AUTH)).json()
        assert body["ok"] is True
        assert body["data"]["items"] == []
        assert body["data"]["total"] == 0


async def test_query_history_after_api_call(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        # 非法 SQL 请求会先失败在参数校验，不触发桥
        bad = await c.post("/api/v1/query/execute", headers=AUTH, json={"datasource": "g"})
        assert bad.status_code == 400
        body = (await c.get("/api/v1/query/history", headers=AUTH)).json()
        assert body["ok"] is True and "items" in body["data"]


# ---------- 旧 URL 已下线 ----------


@pytest.mark.parametrize("old", ["/audit", "/audit/api", "/logs", "/logs/api", "/datasources"])
async def test_legacy_urls_removed(tmp_path, old):
    async with _client(_settings(tmp_path)) as c:
        r = await c.get(old, headers=AUTH)
        assert r.status_code == 404


# ---------- MCP 能力自描述 ----------


async def test_mcp_tools_self_description(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        info = (await c.get("/api/v1/mcp/info", headers=AUTH)).json()["data"]
        assert info["endpoint"] == "/mcp"
        assert info["tool_count"] == 20
        assert "查询与脚本" in info["groups"]

        body = (await c.get("/api/v1/mcp/tools", headers=AUTH)).json()["data"]
        names = {t["name"] for t in body["items"]}
        assert len(names) == 20
        assert {"execute_query", "start_export", "list_datasources"} <= names

        q = next(t for t in body["items"] if t["name"] == "execute_query")
        assert q["group"] == "查询与脚本"
        assert q["requires_confirm"] is True
        props = q["parameters"]["properties"]
        assert {"datasource", "sql", "limit", "confirm"} <= set(props)
        assert q["description"]  # 描述来自工具注册表，非空


async def test_mcp_tools_group_filter_and_detail(tmp_path):
    async with _client(_settings(tmp_path)) as c:
        tx = (await c.get("/api/v1/mcp/tools", params={"group": "事务"}, headers=AUTH)).json()["data"]
        assert tx["total"] == 5
        assert all(t["group"] == "事务" for t in tx["items"])

        one = (await c.get("/api/v1/mcp/tools/list_tables", headers=AUTH)).json()["data"]["item"]
        assert one["name"] == "list_tables"
        assert "search" in one["parameters"]["properties"]

        missing = await c.get("/api/v1/mcp/tools/nope", headers=AUTH)
        assert missing.status_code == 404
