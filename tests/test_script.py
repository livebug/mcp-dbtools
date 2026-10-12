"""SQL 脚本执行与参数替换单元测试（用假桥替身，无需真实数据库）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_dbtools.bridge import BridgeError
from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.jdbc import (
    JDBCError,
    JDBCManager,
    _is_readonly,
    resolve_script_params,
    split_sql_script,
)

from fake_bridge import connection_open_response, make_manager as _make_manager, query_result


def make_manager(tmp_path, cursor_result=None, respond=None):
    root = Path(tmp_path) / "scripts/sql"
    root.mkdir(parents=True, exist_ok=True)
    ds = DataSource(
        name="g",
        type="gaussdb",
        jdbc_url="jdbc:opengauss://h:5432/db",
        driver_class="org.opengauss.Driver",
        jars=["x.jar"],
    )
    settings = Settings(script_root=str(root), datasources=[ds])
    base = {
        "driver.register": {},
        "connection.open": connection_open_response(),
        "query.execute": query_result(rows=cursor_result or [], columns=[{"name": "col", "label": "col"}]),
    }
    if respond:
        base.update(respond)
    m = _make_manager(settings, respond=base)
    return m, ds, root


# ---------- 脚本拆分 ----------
def test_split_sql_script_multiple():
    text = "SELECT 1;\nSELECT 'a;b' AS x;\n-- comment;\nSELECT 2;"
    stmts = split_sql_script(text)
    assert len(stmts) == 3
    assert stmts[1] == "SELECT 'a;b' AS x"
    assert stmts[2] == "SELECT 2"


def test_split_sql_script_leading_comment():
    stmts = split_sql_script("-- 说明注释\nSELECT 1;")
    assert stmts == ["SELECT 1"]


def test_split_sql_script_no_trailing():
    assert split_sql_script("SELECT 1") == ["SELECT 1"]
    assert split_sql_script("  \n  ") == []


# ---------- 参数替换 ----------
def test_resolve_params_from_params():
    out = resolve_script_params("WHERE d <= DATE '${V_DATE}'", {"V_DATE": "2026-08-08"})
    assert "2026-08-08" in out and "${V_DATE}" not in out


def test_resolve_params_from_env():
    out = resolve_script_params("WHERE d = ${V_ENV}", env={"V_ENV": "42"})
    assert out == "WHERE d = 42"


def test_resolve_params_missing_raises():
    with pytest.raises(JDBCError):
        resolve_script_params("WHERE d = ${V_MISSING}", {}, env={})


# ---------- 只读判断 ----------
def test_is_readonly():
    assert _is_readonly("SELECT 1")
    assert _is_readonly("  with t as (select 1) select * from t")
    assert _is_readonly("SHOW TABLES")
    assert _is_readonly("DESCRIBE t")
    assert not _is_readonly("INSERT INTO t VALUES (1)")
    assert not _is_readonly("DELETE FROM t")


# ---------- 脚本执行 ----------
def test_execute_script_with_params(tmp_path):
    m, ds, root = make_manager(tmp_path, [("row1",)])
    script = root / "demo.sql"
    script.write_text("SELECT '${V_DATE}' AS d;\nSELECT 2 AS n;", encoding="utf-8")
    res = m.execute_script(ds, str(script), params={"V_DATE": "2026-01-01"})
    assert res["statement_count"] == 2
    assert res["results"][0]["rows"] == [["row1"]]
    assert res["results"][0]["ok"] is True
    assert res["params"] == {"V_DATE": "2026-01-01"}


def test_execute_script_readonly_block(tmp_path):
    m, ds, root = make_manager(tmp_path)
    script = root / "write.sql"
    script.write_text("INSERT INTO t VALUES (1);", encoding="utf-8")
    with pytest.raises(JDBCError):
        m.execute_script(ds, str(script))


def test_execute_script_write_allowed(tmp_path):
    m, ds, root = make_manager(tmp_path, respond={
        "query.execute": query_result(update_count=1),
    })
    script = root / "write2.sql"
    script.write_text("INSERT INTO t VALUES (1);", encoding="utf-8")
    with pytest.raises(JDBCError):
        m.execute_script(ds, str(script), read_only=False)
    res = m.execute_script(ds, str(script), read_only=False, confirm=True)
    assert res["results"][0]["ok"] is True


def test_execute_script_stop_on_error(tmp_path):
    state = {"n": 0}

    def execute(params):
        state["n"] += 1
        if state["n"] == 2:
            raise BridgeError("SQL_ERROR", "boom")
        return query_result(rows=[], columns=[{"name": "col", "label": "col"}])

    m, ds, root = make_manager(tmp_path, respond={"query.execute": execute})
    script = root / "multi.sql"
    script.write_text("SELECT 1;\nSELECT 2;\nSELECT 3;", encoding="utf-8")
    res = m.execute_script(ds, str(script))
    assert res["statement_count"] == 2
    assert res["results"][1]["ok"] is False
    assert "boom" in res["results"][1]["error"]


def test_execute_script_outside_root(tmp_path):
    m, ds, root = make_manager(tmp_path)
    outside = Path(tmp_path) / "outside.sql"
    outside.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(JDBCError):
        m.execute_script(ds, str(outside))


def test_execute_script_missing_file(tmp_path):
    m, ds, root = make_manager(tmp_path)
    with pytest.raises(JDBCError):
        m.execute_script(ds, str(root / "nope.sql"))


# ---------- save_script 与 execute_script 的协作（回归） ----------
def test_save_then_execute_with_returned_values(tmp_path):
    m, ds, root = make_manager(tmp_path, [("row1",)])
    saved = m.save_script("demo_saved.sql", "SELECT 1 AS n;")

    by_name = m.execute_script(ds, saved["saved"])
    assert by_name["statement_count"] == 1
    assert by_name["script_path"] == "demo_saved.sql"

    by_abs = m.execute_script(ds, str((root / "demo_saved.sql").resolve()))
    assert by_abs["statement_count"] == 1
    assert by_abs["script_path"] == by_name["script_path"]


def test_save_then_execute_relative_name_from_other_cwd(tmp_path, monkeypatch):
    m, ds, root = make_manager(tmp_path, [("row1",)])
    saved = m.save_script("sub/nested.sql", "SELECT 1 AS n;")

    monkeypatch.chdir(tmp_path)
    res = m.execute_script(ds, saved["saved"])
    assert res["results"][0]["ok"] is True
    assert res["script_path"] == "sub/nested.sql"


def test_execute_script_relative_traversal_blocked(tmp_path):
    m, ds, root = make_manager(tmp_path)
    outside = root.parent / "evil.sql"
    outside.write_text("SELECT 1;", encoding="utf-8")
    assert outside.is_file()
    with pytest.raises(JDBCError, match="脚本根目录内"):
        m.execute_script(ds, "../evil.sql")


def test_execute_script_empty_path(tmp_path):
    m, ds, root = make_manager(tmp_path)
    with pytest.raises(JDBCError, match="不能为空"):
        m.execute_script(ds, "   ")


def test_script_tools_do_not_leak_absolute_paths(tmp_path):
    m, ds, root = make_manager(tmp_path, [("row1",)])
    abs_root = str(root.resolve())

    saved = m.save_script("leak_check.sql", "SELECT 1;")
    dumped = json.dumps(saved, ensure_ascii=False, default=str)
    assert abs_root not in dumped, f"save_script 响应泄露绝对路径: {dumped}"
    assert saved["saved"] == "leak_check.sql"

    res = m.execute_script(ds, saved["saved"])
    dumped = json.dumps(res, ensure_ascii=False, default=str)
    assert abs_root not in dumped, f"execute_script 响应泄露绝对路径: {dumped}"

    with pytest.raises(JDBCError) as exc:
        m.execute_script(ds, "missing.sql")
    assert abs_root not in str(exc.value)

    with pytest.raises(JDBCError) as exc2:
        m.execute_script(ds, "../evil.sql")
    assert abs_root not in str(exc2.value)
