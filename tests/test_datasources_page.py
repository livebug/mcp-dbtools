"""数据源管理页单元测试：保存/加密/删除逻辑（无需真实数据库）。"""

from __future__ import annotations

import json
import os

import pytest

from mcp_dbtools import crypto
from mcp_dbtools import datasources_page as ds_page


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("MCP_DBTOOLS_SECRET_KEY", "test-secret-key")


@pytest.fixture()
def cfg(tmp_path):
    p = tmp_path / "datasources.json"
    p.write_text(json.dumps({"datasources": []}), encoding="utf-8")
    return str(p)


def test_save_new_encrypts_password(cfg):
    ok, msg = ds_page.api_save(cfg, {
        "name": "db2_test", "type": "db2",
        "jdbc_url": "jdbc:db2://h:50000/sample",
        "driver_class": "com.ibm.db2.jcc.DB2Driver",
        "username": "db2inst1", "password": "secret123",
        "jars": "db2jcc4.jar", "description": "测试", "original": "",
    })
    assert ok
    raw = json.loads(open(cfg, encoding="utf-8").read())
    ds = raw["datasources"][0]
    # 密码应为 {ENC:...} 密文，且可解密回原文
    assert ds["password"].startswith("{ENC:")
    assert ds["type"] == "db2"
    assert ds["jars"] == ["db2jcc4.jar"]
    payload = ds["password"][5:-1]
    assert crypto.decrypt_password(payload, "test-secret-key") == "secret123"


def test_save_duplicate_rejected(cfg):
    ds_page.api_save(cfg, {"name": "a", "type": "db2", "jdbc_url": "j", "driver_class": "d",
                           "password": "x", "original": ""})
    ok, msg = ds_page.api_save(cfg, {"name": "a", "type": "db2", "jdbc_url": "j2",
                                     "driver_class": "d", "password": "y", "original": ""})
    assert not ok
    assert "已存在" in msg


def test_save_edit_keeps_password_when_blank(cfg):
    ds_page.api_save(cfg, {"name": "a", "type": "db2", "jdbc_url": "j", "driver_class": "d",
                           "password": "secret", "original": ""})
    ok, _ = ds_page.api_save(cfg, {"name": "a", "type": "db2", "jdbc_url": "j2",
                                   "driver_class": "d", "password": "", "original": "a"})
    assert ok
    raw = json.loads(open(cfg, encoding="utf-8").read())
    ds = raw["datasources"][0]
    assert ds["jdbc_url"] == "j2"          # 更新了 URL
    assert ds["password"].startswith("{ENC:")  # 密码保持原密文


def test_save_without_secret_rejected(monkeypatch, cfg):
    monkeypatch.delenv("MCP_DBTOOLS_SECRET_KEY", raising=False)
    ok, msg = ds_page.api_save(cfg, {"name": "a", "type": "db2", "jdbc_url": "j",
                                     "driver_class": "d", "password": "x", "original": ""})
    assert not ok
    assert "SECRET_KEY" in msg


def test_delete(cfg):
    ds_page.api_save(cfg, {"name": "a", "type": "db2", "jdbc_url": "j", "driver_class": "d",
                           "password": "x", "original": ""})
    ok, _ = ds_page.api_delete(cfg, "a")
    assert ok
    raw = json.loads(open(cfg, encoding="utf-8").read())
    assert raw["datasources"] == []


def test_list(cfg):
    ds_page.api_save(cfg, {"name": "a", "type": "db2", "jdbc_url": "j", "driver_class": "d",
                           "password": "x", "original": ""})
    lst = ds_page.api_list(cfg)
    assert len(lst) == 1
    assert lst[0]["name"] == "a"
    assert lst[0]["password"].startswith("{ENC:")
