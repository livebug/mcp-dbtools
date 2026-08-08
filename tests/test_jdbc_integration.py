"""集成测试：依赖本地运行的 openGauss 测试库。

前置条件（参考 README）：
    docker compose -f docker/gaussdb/docker-compose.yml up -d
    python scripts/download_drivers.py

运行:
    .venv/bin/pytest tests/test_jdbc_integration.py -s
"""

from __future__ import annotations

import pytest

from mcp_dbtools.config import DataSource
from mcp_dbtools.jdbc import JDBCError, JDBCManager
from mcp_dbtools.config import Settings

pytestmark = pytest.mark.skipif(
    not __import__("pathlib").Path("drivers/opengauss-jdbc-5.0.0-og.jar").exists(),
    reason="缺少 openGauss JDBC 驱动",
)


@pytest.fixture(scope="module")
def gauss_ds() -> DataSource:
    return DataSource(
        name="gaussdb_test",
        type="gaussdb",
        jdbc_url="jdbc:opengauss://127.0.0.1:5432/gaussdb",
        driver_class="org.opengauss.Driver",
        username="gaussdb",
        password="Gauss@123",
        jars=["opengauss-jdbc-5.0.0-og.jar"],
    )


@pytest.fixture(scope="module")
def manager(gauss_ds) -> JDBCManager:
    settings = Settings(
        drivers_dir="drivers",
        datasources=[gauss_ds],
    )
    return JDBCManager(settings)


def test_connect_and_test(manager, gauss_ds):
    info = manager.test_connection(gauss_ds)
    assert info["ok"] is True
    # openGauss/GaussDB 基于 PG 内核，产品名可能为 PostgreSQL
    assert any(
        k in info["product"] or k in info["version"]
        for k in ("openGauss", "PostgreSQL", "Gauss")
    )


def test_query(manager, gauss_ds):
    res = manager.execute_query(gauss_ds, "SELECT count(*) AS c FROM employees")
    assert res["columns"] == ["c"]
    assert res["row_count"] == 1
    assert res["rows"][0][0] == 4


def test_query_with_limit(manager, gauss_ds):
    res = manager.execute_query(
        gauss_ds, "SELECT * FROM employees ORDER BY id", limit=2
    )
    assert res["row_count"] == 2
    assert res["truncated"] is True


def test_numeric_conversion(manager, gauss_ds):
    """NUMERIC 字段应转换为 JSON 数值而非字符串。"""
    res = manager.execute_query(
        gauss_ds, "SELECT salary FROM employees ORDER BY id LIMIT 1"
    )
    val = res["rows"][0][0]
    assert isinstance(val, (int, float)), f"salary 应为数值，实际: {val!r} ({type(val)})"


def test_list_schemas(manager, gauss_ds):
    schemas = manager.list_schemas(gauss_ds)
    assert "public" in schemas


def test_list_tables(manager, gauss_ds):
    tables = manager.list_tables(gauss_ds)
    names = [t["table"] for t in tables]
    assert "employees" in names and "departments" in names


def test_describe_table(manager, gauss_ds):
    cols = manager.describe_table(gauss_ds, "employees", schema="public")
    names = [c["column"] for c in cols]
    assert {"id", "name", "salary", "hired_at"}.issubset(set(names))


def test_bad_sql_raises(manager, gauss_ds):
    with pytest.raises(JDBCError):
        manager.execute_query(gauss_ds, "SELECT * FROM no_such_table_xyz")
