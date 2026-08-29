"""集成测试：依赖本地运行的 MySQL 测试库（Docker mysql-test）。

前置条件：
    docker run -d --name mysql-test -e MYSQL_ROOT_PASSWORD=rootpass \
        -e MYSQL_DATABASE=testdb -e MYSQL_USER=mysql -e MYSQL_PASSWORD=mysql123 \
        -p 3306:3306 mysql:8
    并初始化 employees / departments 测试表（含中文数据）。

运行:
    .venv/bin/pytest tests/test_mysql_integration.py -s
"""

from __future__ import annotations

import pytest

from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.jdbc import JDBCError, JDBCManager

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("drivers/mysql-connector-j-8.4.0.jar").exists(),
    reason="缺少 MySQL JDBC 驱动 (mysql-connector-j-8.4.0.jar)",
)


@pytest.fixture(scope="module")
def mysql_ds() -> DataSource:
    return DataSource(
        name="mysql_test",
        type="mysql",
        jdbc_url="jdbc:mysql://127.0.0.1:3306/testdb?useUnicode=true&characterEncoding=UTF-8",
        driver_class="com.mysql.cj.jdbc.Driver",
        username="mysql",
        password="mysql123",
        jars=["mysql-connector-j-8.4.0.jar"],
    )


@pytest.fixture(scope="module")
def manager(mysql_ds) -> JDBCManager:
    settings = Settings(
        drivers_dir="drivers",
        datasources=[mysql_ds],
    )
    return JDBCManager(settings)


def test_connect_and_test(manager, mysql_ds):
    info = manager.test_connection(mysql_ds)
    assert info["ok"] is True
    assert "MySQL" in info["product"] or "MariaDB" in info["product"]


def test_query(manager, mysql_ds):
    res = manager.execute_query(mysql_ds, "SELECT count(*) AS cnt FROM employees")
    assert res["row_count"] == 1
    assert res["rows"][0][0] == 4


def test_query_with_limit(manager, mysql_ds):
    """limit 为客户端截断，不依赖 MySQL 的 LIMIT 语法。"""
    res = manager.execute_query(
        mysql_ds, "SELECT * FROM employees ORDER BY id", limit=2
    )
    assert res["row_count"] == 2
    assert res["truncated"] is True


def test_numeric_conversion(manager, mysql_ds):
    """DECIMAL 字段应转换为 JSON 数值而非字符串。"""
    res = manager.execute_query(
        mysql_ds, "SELECT salary FROM employees ORDER BY id"
    )
    val = res["rows"][0][0]
    assert isinstance(val, (int, float)), f"salary 应为数值，实际: {val!r} ({type(val)})"


def test_list_schemas(manager, mysql_ds):
    schemas = manager.list_schemas(mysql_ds)
    assert "testdb" in schemas
    # 系统库应被排除
    assert "mysql" not in schemas
    assert "information_schema" not in schemas


def test_list_tables(manager, mysql_ds):
    tables = manager.list_tables(mysql_ds)
    names = [t["table"].lower() for t in tables]
    assert "employees" in names and "departments" in names


def test_describe_table(manager, mysql_ds):
    cols = manager.describe_table(mysql_ds, "employees", schema="testdb")
    names = [c["column"].lower() for c in cols]
    assert {"id", "name", "salary", "hired_at"}.issubset(set(names))
    # MySQL 列注释
    assert any(c["comment"] for c in cols)


def test_bad_sql_raises(manager, mysql_ds):
    with pytest.raises(JDBCError):
        manager.execute_query(mysql_ds, "SELECT * FROM no_such_table_xyz")


def test_write_requires_confirm(manager, mysql_ds):
    """MySQL 写操作默认拦截，需 confirm=true 才放行。"""
    with pytest.raises(JDBCError):
        manager.execute_query(
            mysql_ds, "DELETE FROM employees WHERE 1=0", confirm=False
        )


def test_write_with_confirm(manager, mysql_ds):
    """confirm=true 允许写操作，并清理测试数据（不影响其他用例的 count 断言）。"""
    res = manager.execute_query(
        mysql_ds,
        "INSERT INTO employees (name, department, salary) "
        "VALUES ('测试', '测试部', 999.99)",
        confirm=True,
    )
    assert res["row_count"] == 0 or res["row_count"] == 1
    # 清理：删除刚插入的测试记录，恢复原状
    manager.execute_query(
        mysql_ds, "DELETE FROM employees WHERE name = '测试'", confirm=True
    )
