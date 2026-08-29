"""MySQL 方言（information_schema）单元测试（使用桩游标，无需真实 MySQL）。

覆盖 list_schemas / list_tables / describe_table / _guess_schema / _ping_sql 的
mysql 分支：SQL 构造正确性 + 结果解析（nullable 转 Y/N、列注释）。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.jdbc import JDBCManager


class FakeCursor:
    def __init__(self, result):
        self._result = result
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        pass

    @property
    def description(self):
        return None


def make_manager(result):
    ds = DataSource(
        name="mysql_test",
        type="mysql",
        jdbc_url="jdbc:mysql://127.0.0.1:3306/testdb",
        driver_class="com.mysql.cj.jdbc.Driver",
        username="mysql",
        jars=["mysql-connector-j-8.4.0.jar"],
    )
    settings = Settings(drivers_dir="drivers", datasources=[ds])
    m = JDBCManager(settings)

    def fake_cursor(data_source):
        cur = FakeCursor(result)
        m._last_cursor = cur

        @contextmanager
        def _cm():
            try:
                yield cur
            finally:
                pass

        return _cm()

    m.cursor = fake_cursor
    return m, ds


def _sql(m):
    return m._last_cursor.sql


def _params(m):
    return m._last_cursor.params


def test_mysql_list_schemas():
    m, ds = make_manager([("testdb",), ("mysql",), ("sys",)])
    schemas = m.list_schemas(ds)
    assert schemas == ["testdb", "mysql", "sys"]
    assert "information_schema.schemata" in _sql(m)
    assert "performance_schema" in _sql(m)  # 排除系统库


def test_mysql_list_tables_no_schema():
    m, ds = make_manager(
        [("testdb", "EMPLOYEES", "BASE TABLE"), ("testdb", "DEPARTMENTS", "BASE TABLE")]
    )
    tables = m.list_tables(ds)
    assert len(tables) == 2
    assert tables[0] == {"schema": "testdb", "table": "EMPLOYEES", "type": "BASE TABLE"}
    assert "information_schema.tables" in _sql(m)


def test_mysql_list_tables_with_schema_and_search():
    m, ds = make_manager([("testdb", "EMPLOYEES", "BASE TABLE"), ("testdb", "ORDERS", "BASE TABLE")])
    tables = m.list_tables(ds, schema="testdb", search="emp")
    assert len(tables) == 1
    assert tables[0]["table"] == "EMPLOYEES"
    assert "table_schema = ?" in _sql(m)
    assert _params(m) == ["testdb"]


def test_mysql_describe_table():
    rows = [
        ("ID", "int", "NO", None, "员工ID"),
        ("NAME", "varchar", "NO", None, "姓名"),
        ("SALARY", "decimal", "YES", None, "工资"),
    ]
    m, ds = make_manager(rows)
    cols = m.describe_table(ds, "EMPLOYEES", schema="testdb")
    assert cols[0]["column"] == "ID"
    assert cols[0]["data_type"] == "int"
    assert cols[0]["nullable"] == "N"
    assert cols[0]["comment"] == "员工ID"
    assert cols[2]["nullable"] == "Y"
    assert "information_schema.columns" in _sql(m)
    assert _params(m) == ["testdb", "EMPLOYEES"]


def test_mysql_guess_schema():
    m, ds = make_manager([("testdb",)])
    schema = m._guess_schema(ds, "EMPLOYEES")
    assert schema == "testdb"
    assert "information_schema.tables" in _sql(m)


def test_mysql_guess_schema_fallback():
    m, ds = make_manager([])
    schema = m._guess_schema(ds, "NOPE")
    assert schema == "mysql"  # 回退到用户名 schema


def test_mysql_ping_sql():
    """MySQL 支持无 FROM 的 SELECT 1。"""
    m, ds = make_manager([])
    assert m._ping_sql(ds) == "SELECT 1"


def test_mysql_write_requires_confirm():
    """MySQL 写操作默认拦截，需 confirm=true。"""
    m, ds = make_manager([])
    from mcp_dbtools.jdbc import JDBCError

    with pytest.raises(JDBCError):
        m.execute_query(ds, "DELETE FROM employees WHERE 1=0", confirm=False)
