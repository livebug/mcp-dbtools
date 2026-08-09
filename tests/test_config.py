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


def test_datasource_encrypted_password():
    """{ENC:...} 加密密码应被密钥正确解密，且密文不含明文。"""
    from mcp_dbtools.crypto import encrypt_password

    secret = "s3cret-key"
    env = {"MCP_DBTOOLS_SECRET_KEY": secret}
    enc = encrypt_password("Gauss@123", secret)
    assert "Gauss@123" not in enc

    ds = DataSource.from_dict(
        {
            "name": "demo",
            "type": "gaussdb",
            "jdbc_url": "jdbc:opengauss://h:5432/db",
            "driver_class": "org.opengauss.Driver",
            "username": "u",
            "password": enc,
        },
        env=env,
    )
    assert ds.password == "Gauss@123"


def test_encrypted_password_wrong_key():
    """密钥错误时应明确报错，而非静默连错库。"""
    from mcp_dbtools.crypto import encrypt_password

    env = {"MCP_DBTOOLS_SECRET_KEY": "keyA"}
    enc = encrypt_password("Gauss@123", "keyA")
    env_wrong = {"MCP_DBTOOLS_SECRET_KEY": "keyB"}
    with pytest.raises(ConfigError):
        DataSource.from_dict(
            {
                "name": "demo",
                "type": "gaussdb",
                "jdbc_url": "jdbc:opengauss://h:5432/db",
                "driver_class": "org.opengauss.Driver",
                "username": "u",
                "password": enc,
            },
            env=env_wrong,
        )


def test_encrypted_password_missing_key():
    """配置含 {ENC:...} 但未设置密钥时应报错。"""
    from mcp_dbtools.crypto import encrypt_password

    enc = encrypt_password("Gauss@123", "k")
    with pytest.raises(ConfigError) as e:
        DataSource.from_dict(
            {
                "name": "demo",
                "type": "gaussdb",
                "jdbc_url": "jdbc:opengauss://h:5432/db",
                "driver_class": "org.opengauss.Driver",
                "username": "u",
                "password": enc,
            },
            env={},
        )
    assert "MCP_DBTOOLS_SECRET_KEY" in str(e.value)


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
