"""SQL 脚本执行与参数替换单元测试（使用桩游标，无需真实数据库）。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.jdbc import (
    JDBCError,
    JDBCManager,
    _is_readonly,
    resolve_script_params,
    split_sql_script,
)


class FakeCursor:
    def __init__(self, results):
        self._results = list(results)
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    @property
    def description(self):
        return [("col",)] if self._results else None

    def fetchmany(self, size):
        out, self._results = self._results[:size], self._results[size:]
        return out

    @property
    def rowcount(self):
        return -1

    def close(self):
        pass


def make_manager(tmp_path, cursor_result):
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
    m = JDBCManager(settings)

    def fake_cursor(data_source):
        cur = FakeCursor(cursor_result)

        @contextmanager
        def _cm():
            try:
                yield cur
            finally:
                pass

        return _cm()

    m.cursor = fake_cursor
    return m, ds, root


# ---------- 脚本拆分 ----------
def test_split_sql_script_multiple():
    text = "SELECT 1;\nSELECT 'a;b' AS x;\n-- comment;\nSELECT 2;"
    stmts = split_sql_script(text)
    assert len(stmts) == 3
    assert stmts[1] == "SELECT 'a;b' AS x"
    # -- 注释（含注释内分号）应被剔除，不作为独立语句
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
    m, ds, root = make_manager(tmp_path, [])
    script = root / "write.sql"
    script.write_text("INSERT INTO t VALUES (1);", encoding="utf-8")
    with pytest.raises(JDBCError):
        m.execute_script(ds, str(script))


def test_execute_script_write_allowed(tmp_path):
    m, ds, root = make_manager(tmp_path, [])
    script = root / "write2.sql"
    script.write_text("INSERT INTO t VALUES (1);", encoding="utf-8")
    res = m.execute_script(ds, str(script), read_only=False)
    assert res["results"][0]["ok"] is True


def test_execute_script_stop_on_error(tmp_path):
    class BoomCursor(FakeCursor):
        def __init__(self):
            super().__init__([])
            self.executed = 0

        def execute(self, sql, params=None):
            self.executed += 1
            if self.executed == 2:
                raise RuntimeError("boom")

    m, ds, root = make_manager(tmp_path, [])
    boom = BoomCursor()

    @contextmanager
    def _cm():
        try:
            yield boom
        finally:
            pass

    m.cursor = lambda data_source: _cm()

    script = root / "multi.sql"
    script.write_text("SELECT 1;\nSELECT 2;\nSELECT 3;", encoding="utf-8")
    res = m.execute_script(ds, str(script))
    # 第 2 条失败即停止，共 2 条结果
    assert res["statement_count"] == 2
    assert res["results"][1]["ok"] is False
    assert "boom" in res["results"][1]["error"]


def test_execute_script_outside_root(tmp_path):
    m, ds, root = make_manager(tmp_path, [])
    outside = Path(tmp_path) / "outside.sql"
    outside.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(JDBCError):
        m.execute_script(ds, str(outside))


def test_execute_script_missing_file(tmp_path):
    m, ds, root = make_manager(tmp_path, [])
    with pytest.raises(JDBCError):
        m.execute_script(ds, str(root / "nope.sql"))
