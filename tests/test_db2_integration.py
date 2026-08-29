"""集成测试：依赖本地运行的 DB2 测试库（Docker db2server）。

前置条件：
    docker start db2server   # icr.io/db2_community/db2 容器
    # 初始化测试表（employees / departments）已由 db2server 初始化脚本准备

运行:
    .venv/bin/pytest tests/test_db2_integration.py -s
"""

from __future__ import annotations

import pytest

from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.jdbc import JDBCError, JDBCManager

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("drivers/db2jcc4.jar").exists(),
    reason="缺少 DB2 JDBC 驱动 (db2jcc4.jar)",
)


@pytest.fixture(scope="module")
def db2_ds() -> DataSource:
    return DataSource(
        name="db2_test",
        type="db2",
        jdbc_url="jdbc:db2://127.0.0.1:50000/testdb",
        driver_class="com.ibm.db2.jcc.DB2Driver",
        username="db2inst1",
        password="password",
        jars=["db2jcc4.jar"],
    )


@pytest.fixture(scope="module")
def manager(db2_ds) -> JDBCManager:
    settings = Settings(
        drivers_dir="drivers",
        datasources=[db2_ds],
    )
    return JDBCManager(settings)


def test_connect_and_test(manager, db2_ds):
    info = manager.test_connection(db2_ds)
    assert info["ok"] is True
    assert "DB2" in info["product"] or "DB2" in info["version"]


def test_query(manager, db2_ds):
    res = manager.execute_query(db2_ds, "SELECT count(*) AS c FROM employees")
    # DB2 默认将未加引号的标识符转为大写
    assert res["columns"] == ["C"]
    assert res["row_count"] == 1
    assert res["rows"][0][0] == 4


def test_query_with_limit(manager, db2_ds):
    """limit 为客户端截断，不依赖 DB2 的 LIMIT 语法。"""
    res = manager.execute_query(
        db2_ds, "SELECT * FROM employees ORDER BY id", limit=2
    )
    assert res["row_count"] == 2
    assert res["truncated"] is True


def test_numeric_conversion(manager, db2_ds):
    """DECIMAL 字段应转换为 JSON 数值而非字符串。"""
    res = manager.execute_query(
        db2_ds, "SELECT salary FROM employees ORDER BY id"
    )
    val = res["rows"][0][0]
    assert isinstance(val, (int, float)), f"salary 应为数值，实际: {val!r} ({type(val)})"


def test_list_schemas(manager, db2_ds):
    schemas = manager.list_schemas(db2_ds)
    assert "DB2INST1" in schemas


def test_list_tables(manager, db2_ds):
    tables = manager.list_tables(db2_ds)
    names = [t["table"].lower() for t in tables]
    assert "employees" in names and "departments" in names


def test_describe_table(manager, db2_ds):
    cols = manager.describe_table(db2_ds, "EMPLOYEES", schema="DB2INST1")
    names = [c["column"].lower() for c in cols]
    assert {"id", "name", "salary", "hired_at"}.issubset(set(names))


def test_bad_sql_raises(manager, db2_ds):
    with pytest.raises(JDBCError):
        manager.execute_query(db2_ds, "SELECT * FROM no_such_table_xyz")


def test_write_requires_confirm(manager, db2_ds):
    """DB2 写操作默认拦截，需 confirm=true 才放行。"""
    with pytest.raises(JDBCError):
        manager.execute_query(
            db2_ds, "DELETE FROM employees WHERE 1=0", confirm=False
        )


def test_write_with_confirm(manager, db2_ds):
    """confirm=true 允许写操作，并清理测试数据（不影响其他用例的 count 断言）。"""
    res = manager.execute_query(
        db2_ds,
        "INSERT INTO employees (name, department, salary) "
        "VALUES ('测试', '测试部', 999.99)",
        confirm=True,
    )
    assert res["row_count"] == 0 or res["row_count"] == 1
    # 清理：删除刚插入的测试记录，恢复原状
    manager.execute_query(
        db2_ds, "DELETE FROM employees WHERE name = '测试'", confirm=True
    )
