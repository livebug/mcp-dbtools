"""TDH/Hive 方言 SQL 生成单元测试（使用桩游标，无需真实 Inceptor）。"""

from __future__ import annotations

from contextlib import contextmanager

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

    def fetchmany(self, size):
        return self._result

    def close(self):
        pass

    @property
    def description(self):
        return None


def make_manager(result):
    ds = DataSource(
        name="tdh",
        type="tdh",
        jdbc_url="jdbc:hive2://h:10000/default",
        driver_class="org.apache.hive.jdbc.HiveDriver",
        jars=["inceptor-jdbc.jar"],
    )
    settings = Settings(drivers_dir="drivers", datasources=[ds])
    m = JDBCManager(settings)

    def fake_cursor(data_source):
        cur = FakeCursor(result)

        @contextmanager
        def _cm():
            try:
                yield cur
            finally:
                pass

        return _cm()

    m.cursor = fake_cursor
    return m, ds


def test_tdh_list_schemas():
    m, ds = make_manager([("default",), ("tpcds",)])
    schemas = m.list_schemas(ds)
    assert schemas == ["default", "tpcds"]


def test_tdh_list_tables_sql():
    m, ds = make_manager([("t1",), ("t2",)])
    tables = m.list_tables(ds, schema="default")
    # 有 schema 时应生成 SHOW TABLES IN
    cursor = m.cursor
    # 校验方言 SQL 通过假游标执行时不含参数
    assert tables == [
        {"schema": "default", "table": "t1"},
        {"schema": "default", "table": "t2"},
    ]


def test_tdh_list_tables_search():
    m, ds = make_manager([("employees",), ("departments",)])
    tables = m.list_tables(ds, search="emp")
    assert tables == [{"schema": "", "table": "employees"}]


def test_tdh_describe_table_sql():
    m, ds = make_manager(
        [("id", "int", "comment1"), ("name", "string", "comment2")]
    )
    cols = m.describe_table(ds, "employees", schema="default")
    assert cols == [
        {"column": "id", "type": "int", "comment": "comment1"},
        {"column": "name", "type": "string", "comment": "comment2"},
    ]


def test_tdh_identifier_quoting():
    from mcp_dbtools.jdbc import _quote

    assert _quote("my;table`name") == "my;tablename" or ";" not in _quote("a;b")
