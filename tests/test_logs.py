"""服务日志查看功能测试：tail_lines + /ui 控制台与 /api/v1/logs。"""

from __future__ import annotations

import httpx
import pytest

from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.services.logs import tail_lines


def _ds(name="g"):
    return DataSource(
        name=name, type="gaussdb", jdbc_url="jdbc:opengauss://h:5432/db",
        driver_class="org.opengauss.Driver", jars=["x.jar"],
    )


# ---------- tail_lines ----------
def test_tail_empty(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("", encoding="utf-8")
    r = tail_lines(f, lines=100)
    assert r["content"] == ""
    assert r["returned"] == 0
    assert r["truncated"] is False


def test_tail_last_n(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
    r = tail_lines(f, lines=3)
    assert r["returned"] == 3
    assert "line9" in r["content"]
    assert "line0" not in r["content"]
    assert r["truncated"] is True


def test_tail_not_truncated(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("a\nb\nc", encoding="utf-8")
    r = tail_lines(f, lines=10)
    assert r["truncated"] is False
    assert r["returned"] == 3


def test_tail_filter(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("INFO ok\nERROR boom\nINFO ok2\nERROR boom2", encoding="utf-8")
    r = tail_lines(f, lines=10, q="ERROR")
    assert "boom" in r["content"] and "boom2" in r["content"]
    assert "INFO" not in r["content"]
    assert r["returned"] == 2


def test_tail_no_file(tmp_path):
    r = tail_lines(tmp_path / "none.log", lines=10)
    assert r["content"] == ""
    assert r["returned"] == 0


# ---------- HTTP 页面 / API ----------
async def test_logs_page_and_api(tmp_path):
    logfile = tmp_path / "app.log"
    logfile.write_text("line1\nERROR test-error\nline3", encoding="utf-8")
    settings = Settings(auth_token="tok", log_file=str(logfile), datasources=[_ds()])

    from mcp_dbtools.app import build_app

    app = build_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 控制台外壳免鉴权
        r = await c.get("/ui/logs")
        assert r.status_code == 200
        assert "/ui/assets/app.js" in r.text
        # 数据接口需鉴权
        r3 = await c.get("/api/v1/logs")
        assert r3.status_code == 401
        # 带 token 查询
        r2 = await c.get(
            "/api/v1/logs",
            params={"lines": "10"},
            headers={"Authorization": "Bearer tok"},
        )
        assert r2.status_code == 200
        data = r2.json()["data"]
        assert "test-error" in data["content"]
        assert data["file"] == str(logfile)
        # 关键字过滤
        r4 = await c.get(
            "/api/v1/logs",
            params={"q": "ERROR"},
            headers={"Authorization": "Bearer tok"},
        )
        data4 = r4.json()["data"]
        assert "test-error" in data4["content"]
        assert "line1" not in data4["content"]
