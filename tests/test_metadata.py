"""元数据与查询走桥的映射测试（无方言 SQL：所有数据源同一条路径）。"""

from __future__ import annotations

import pytest

from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.jdbc import JDBCError

from fake_bridge import connection_open_response, make_manager, query_result


def _ds():
    return DataSource(
        name="g", type="gaussdb", jdbc_url="jdbc:opengauss://h:5432/db",
        driver_class="org.opengauss.Driver", jars=["x.jar"],
    )


def _base():
    return {
        "driver.register": {},
        "connection.open": connection_open_response(),
    }


def test_test_connection_reports_capabilities():
    m = make_manager(Settings(datasources=[_ds()]), respond={
        **_base(),
        "connection.open": connection_open_response("GaussDB", "505.1.0"),
    })
    res = m.test_connection(_ds())
    assert res["ok"] is True
    assert res["product"] == "GaussDB"
    assert res["version"] == "505.1.0"


def test_list_schemas():
    m = make_manager(Settings(datasources=[_ds()]), respond={
        **_base(),
        "metadata.schemas": {"schemas": ["public", "pg_catalog"]},
    })
    assert m.list_schemas(_ds()) == ["public", "pg_catalog"]


def test_list_tables_maps_shape():
    m = make_manager(Settings(datasources=[_ds()]), respond={
        **_base(),
        "metadata.tables": {"tables": [{"name": "users", "type": "TABLE", "schema": "public"}]},
    })
    tables = m.list_tables(_ds(), schema="public")
    assert tables == [{"schema": "public", "table": "users", "type": "TABLE"}]


def test_list_tables_search_sets_name_pattern():
    m = make_manager(Settings(datasources=[_ds()]), respond={
        **_base(),
        "metadata.tables": {"tables": []},
    })
    m.list_tables(_ds(), search="user")
    assert m._bridge.last_params("metadata.tables")["namePattern"] == "%user%"


def test_describe_table_maps_columns():
    m = make_manager(Settings(datasources=[_ds()]), respond={
        **_base(),
        "metadata.columns": {"columns": [
            {"name": "id", "typeName": "int4", "displayType": "int4", "nullable": False,
             "defaultValue": None, "remarks": "主键"},
        ]},
    })
    cols = m.describe_table(_ds(), "users", schema="public")
    assert cols == [{"column": "id", "type": "int4", "nullable": "N", "default": "", "comment": "主键"}]


def test_execute_query_returns_rows_and_columns():
    m = make_manager(Settings(datasources=[_ds()]), respond={
        **_base(),
        "query.execute": query_result(
            rows=[[1, "a"], [2, "b"]],
            columns=[{"name": "id", "label": "id"}, {"name": "name", "label": "name"}],
        ),
    })
    res = m.execute_query(_ds(), "SELECT id,name FROM t")
    assert res["columns"] == ["id", "name"]
    assert res["rows"] == [[1, "a"], [2, "b"]]
    assert res["row_count"] == 2
    assert res["truncated"] is False


def test_execute_query_write_guard_without_confirm():
    m = make_manager(Settings(datasources=[_ds()]), respond=_base())
    with pytest.raises(JDBCError):
        m.execute_query(_ds(), "DROP TABLE t")


def test_execute_query_bridge_error_maps_to_jdbc_error():
    from mcp_dbtools.bridge import BridgeError

    m = make_manager(Settings(datasources=[_ds()]), respond={
        **_base(),
        "query.execute": BridgeError("SQL_ERROR", "relation missing", "42P01", 99),
    })
    with pytest.raises(JDBCError) as exc:
        m.execute_query(_ds(), "SELECT 1")
    assert "relation missing" in str(exc.value)
