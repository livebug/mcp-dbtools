"""配置加载单元测试。"""

from __future__ import annotations

import json
import os

import pytest

from mcp_dbtools.config import (
    ConfigError,
    DataSource,
    get_datasource,
    load_settings,
)


def test_datasource_env_substitution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_PW", "secret123")
    ds = DataSource.from_dict(
        {
            "name": "demo",
            "type": "gaussdb",
            "jdbc_url": "jdbc:opengauss://h:5432/db",
            "driver_class": "org.opengauss.Driver",
            "username": "u",
            "password": "{ENV:MY_PW}",
        },
        env=dict(os.environ),
    )
    assert ds.password == "secret123"
    assert ds.safe_dict.get("password") is None  # 脱敏


def test_load_settings(tmp_path, monkeypatch):
    cfg = tmp_path / "datasources.json"
    cfg.write_text(
        json.dumps(
            {
                "datasources": [
                    {
                        "name": "a",
                        "type": "tdh",
                        "jdbc_url": "jdbc:transwarp://h:10000/db",
                        "driver_class": "com.transwarp.jdbc.jdbc.TWJdbcDriver",
                        "username": "u",
                        "password": "p",
                        "jars": ["inceptor-driver.jar"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MCP_DBTOOLS_CONFIG", str(cfg))
    settings = load_settings()
    assert [d.name for d in settings.datasources] == ["a"]
    assert settings.transport == "streamable-http"


def test_unknown_datasource():
    from mcp_dbtools.config import Settings

    s = Settings(
        datasources=[
            DataSource(name="a", type="g", jdbc_url="j", driver_class="x.Driver")
        ]
    )
    with pytest.raises(ConfigError):
        get_datasource(s, "nope")


def test_missing_name_raises():
    with pytest.raises(ConfigError):
        DataSource.from_dict({"jdbc_url": "jdbc:x"})
