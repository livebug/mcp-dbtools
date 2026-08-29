"""DB2 方言（SYSCAT 目录）单元测试（使用桩游标，无需真实 DB2）。

覆盖 list_schemas / list_tables / describe_table / _guess_schema 的 db2 分支：
SQL 构造正确性 + 结果解析。
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
        name="db2",
        type="db2",
        jdbc_url="jdbc:db2://127.0.0.1:50000/sample",
        driver_class="com.ibm.db2.jcc.DB2Driver",
        username="db2inst1",
        jars=["db2jcc4.jar"],
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


def test_db2_list_schemas():
    m, ds = make_manager([("DB2INST1",), ("SAMPLE",), ("SYSIBM",), ("SYSCAT",)])
    schemas = m.list_schemas(ds)
    assert schemas == ["DB2INST1", "SAMPLE", "SYSIBM", "SYSCAT"]
    assert "syscat.schemata" in _sql(m)


def test_db2_list_tables_no_schema():
    m, ds = make_manager([("DB2INST1", "ORDERS", "T"), ("DB2INST1", "CUSTOMER", "T")])
    tables = m.list_tables(ds)
    assert len(tables) == 2
    assert tables[0] == {"schema": "DB2INST1", "table": "ORDERS", "type": "T"}
    assert "syscat.tables" in _sql(m)


def test_db2_list_tables_with_schema_and_search():
    m, ds = make_manager([("DB2INST1", "ORDERS", "T"), ("DB2INST1", "ORDER_LINE", "T")])
    tables = m.list_tables(ds, schema="DB2INST1", search="line")
    assert len(tables) == 1
    assert tables[0]["table"] == "ORDER_LINE"
    assert "tabschema = ?" in _sql(m)
    assert _params(m) == ["DB2INST1"]


def test_db2_describe_table():
    rows = [
        ("ORDER_ID", "BIGINT", None, None, "N", "订单ID"),
        ("AMOUNT", "DECIMAL", 18, 2, "N", "金额"),
        ("CUST_NAME", "VARCHAR", 128, None, "Y", "客户名"),
    ]
    m, ds = make_manager(rows)
    cols = m.describe_table(ds, "ORDERS", schema="DB2INST1")
    assert cols[0]["column"] == "ORDER_ID"
    assert cols[0]["data_type"] == "BIGINT"
    assert cols[0]["nullable"] == "N"
    assert cols[0]["comment"] == "订单ID"
    assert cols[1]["data_type"] == "DECIMAL(18,2)"
    assert cols[2]["data_type"] == "VARCHAR(128)"
    assert cols[2]["nullable"] == "Y"
    assert "syscat.columns" in _sql(m)
    assert _params(m) == ["DB2INST1", "ORDERS"]


def test_db2_guess_schema():
    m, ds = make_manager([("DB2INST1",)])
    schema = m._guess_schema(ds, "ORDERS")
    assert schema == "DB2INST1"
    assert "syscat.tables" in _sql(m)


def test_db2_guess_schema_fallback():
    m, ds = make_manager([])
    schema = m._guess_schema(ds, "NOPE")
    assert schema == "db2inst1"  # 回退到用户名 schema


def test_db2_ping_sql():
    """DB2 探测 SQL 必须带 FROM（DB2 不允许无 FROM 的 SELECT）。"""
    m, ds = make_manager([])
    assert m._ping_sql(ds) == "SELECT 1 FROM SYSIBM.SYSDUMMY1"
    assert "FROM" in m._ping_sql(ds)


def test_non_db2_ping_sql():
    m, ds = make_manager([])
    ds.type = "gaussdb"
    assert m._ping_sql(ds) == "SELECT 1"


def test_is_alive_uses_ping_sql():
    """_is_alive 应把方言探测 SQL 传给游标（DB2 场景验证）。"""
    executed = []

    class FakeCur:
        def execute(self, sql):
            executed.append(sql)

        def fetchall(self):
            return []

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCur()

    m, ds = make_manager([])
    assert m._is_alive(FakeConn(), m._ping_sql(ds)) is True
    assert executed == ["SELECT 1 FROM SYSIBM.SYSDUMMY1"]


def test_columns_converted_via_jsonable():
    """列名必须是 Python 原生 str（DB2 返回 java.lang.String 会破坏 FastMCP 序列化）。"""
    from mcp_dbtools.jdbc import _jsonable

    class JString:
        """模拟 java.lang.String（JPype 包装）。"""
        __module__ = "java.lang"

        def __init__(self, s):
            self._s = s

        def toString(self):
            return self._s

    # _jsonable 对 Java 对象应返回原生 str
    converted = _jsonable(JString("ORDER_ID"))
    assert converted == "ORDER_ID"
    assert type(converted) is str
    # 非 Java 原生 str 原样返回
    assert _jsonable("CUST_NAME") == "CUST_NAME"


def test_db2_fetchmany_exhausted_handled():
    """DB2 驱动取完最后一行后再次 fetchmany 抛 \"result set is closed\"，应视为正常结束返回已有行。"""

    class ExhaustedCursor:
        def __init__(self, rows):
            self._rows = rows
            self._calls = 0
            self.description = [("c", None, None, None, None, None, None)]

        def execute(self, sql, params=None):
            pass

        def fetchmany(self, size):
            self._calls += 1
            if self._calls == 1:
                return list(self._rows)
            raise RuntimeError("result set is closed")

        def close(self):
            pass

    m, ds = make_manager([])

    @contextmanager
    def fake_cursor(data_source):
        yield ExhaustedCursor([(5,)])

    m.cursor = fake_cursor
    res = m.execute_query(ds, "SELECT count(*) FROM employees")
    assert res["row_count"] == 1
    assert res["rows"][0][0] == 5
    assert res["truncated"] is False


def test_db2_fetchmany_empty_first_raises():
    """若第一次 fetchmany 就抛异常（无任何行），应向上抛出而非静默吞掉。"""

    class BrokenCursor:
        def __init__(self):
            self.description = [("c", None, None, None, None, None, None)]

        def execute(self, sql, params=None):
            pass

        def fetchmany(self, size):
            raise RuntimeError("connection lost")

        def close(self):
            pass

    m, ds = make_manager([])

    @contextmanager
    def fake_cursor(data_source):
        yield BrokenCursor()

    m.cursor = fake_cursor
    with pytest.raises(Exception):
        m.execute_query(ds, "SELECT count(*) FROM employees")
