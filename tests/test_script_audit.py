"""save_script + 审计回填（脚本内容快照）测试。"""
from __future__ import annotations

import asyncio
import json

import pytest

from mcp_dbtools.config import Settings
from mcp_dbtools.jdbc import JDBCManager, JDBCError
from mcp_dbtools.monitor import Monitor, wrap_tool, _sanitize_args


def make_manager(tmp_path):
    settings = Settings(
        drivers_dir=str(tmp_path / "drivers"),
        script_root=str(tmp_path / "scripts" / "sql"),
    )
    (tmp_path / "scripts" / "sql").mkdir(parents=True, exist_ok=True)
    return JDBCManager(settings), tmp_path / "scripts" / "sql"


def test_save_script_ok(tmp_path):
    m, root = make_manager(tmp_path)
    res = m.save_script("etl_daily.sql", "SELECT ${V_DATE};")
    assert res["saved"] == "etl_daily.sql"
    assert res["bytes"] > 0
    assert res["overwritten"] is False
    assert (root / "etl_daily.sql").read_text(encoding="utf-8") == "SELECT ${V_DATE};"
    # 审计补充字段
    extra = res["_audit_extra"]
    assert extra["script_content"] == "SELECT ${V_DATE};"
    assert extra["script_name"] == "etl_daily.sql"
    assert len(extra["script_sha256"]) == 16


def test_save_script_overwrite_guard(tmp_path):
    m, root = make_manager(tmp_path)
    m.save_script("a.sql", "SELECT 1;")
    with pytest.raises(JDBCError, match="已存在"):
        m.save_script("a.sql", "SELECT 2;")
    res = m.save_script("a.sql", "SELECT 2;", overwrite=True)
    assert res["overwritten"] is True
    assert (root / "a.sql").read_text(encoding="utf-8") == "SELECT 2;"


def test_save_script_rejects_traversal(tmp_path):
    m, root = make_manager(tmp_path)
    with pytest.raises(JDBCError, match="脚本根目录内|目录外"):
        m.save_script("../evil.sql", "SELECT 1;")
    with pytest.raises(JDBCError, match="脚本根目录内|目录外"):
        m.save_script("/tmp/evil.sql", "SELECT 1;")


def test_save_script_rejects_empty(tmp_path):
    m, _ = make_manager(tmp_path)
    with pytest.raises(JDBCError):
        m.save_script("", "SELECT 1;")
    with pytest.raises(JDBCError):
        m.save_script("a.sql", "   ")


def test_sanitize_args_keeps_long_script_content():
    """script_content 应保留长内容（审计页面要能看执行的脚本）。"""
    big = "SELECT 1;\n" * 500  # 5500 字符
    args = _sanitize_args({"script_content": big, "other": big})
    assert args["script_content"] == big          # 完整保留（< 200k）
    assert args["other"] != big                    # 其他字段仍截断
    assert "字符)" in args["other"]


def test_wrap_tool_extracts_audit_extra():
    m = Monitor(history_size=10, audit_file=None, audit_db=None)

    @wrap_tool(m, "save_script")
    def fake_save(name, content):
        return {"saved": name, "_audit_extra": {"script_content": content, "script_name": name}}

    res = asyncio.run(fake_save(name="x.sql", content="SELECT 1;"))
    # 返回给客户端的结构不含 _audit_extra
    assert res == {"saved": "x.sql"}
    # 审计里含脚本内容
    entry = m._history[-1]
    assert entry["tool"] == "save_script"
    assert entry["args"]["script_content"] == "SELECT 1;"
    assert entry["args"]["script_name"] == "x.sql"
