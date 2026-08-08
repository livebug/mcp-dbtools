"""MCP 工具定义（TDH / GaussDB / openGauss）。

所有工具都通过数据源名称访问配置中定义好的连接，
不允许在工具参数中传入任意 JDBC URL，避免任意连接风险。

参数使用标量 + Annotated 方式定义，以保证在不同 FastMCP 版本下
生成一致的输入 schema。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import ConfigError, Settings, get_datasource
from .jdbc import JDBCError, JDBCManager

logger = logging.getLogger(__name__)

DatasourceArg = Annotated[
    str, Field(description="数据源名称（先调用 list_datasources 获取）")
]


def _err(msg: str) -> str:
    return f"❌ {msg}"


def register_tools(mcp: FastMCP, settings: Settings, manager: JDBCManager) -> None:
    @mcp.tool()
    def list_datasources() -> dict[str, Any]:
        """列出所有已配置的数据库数据源（名称、类型、驱动、说明），不含密码。"""
        return {"datasources": [ds.safe_dict for ds in settings.datasources]}

    @mcp.tool()
    def test_connection(datasource: DatasourceArg) -> dict[str, Any] | str:
        """测试到指定数据源的 JDBC 连接，返回数据库产品与版本信息。"""
        try:
            ds = get_datasource(settings, datasource)
            return manager.test_connection(ds)
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    def execute_query(
        datasource: DatasourceArg,
        sql: Annotated[
            str, Field(description="只读 SQL 语句（SELECT / SHOW / DESCRIBE 等）")
        ],
        limit: Annotated[
            int, Field(default=1000, ge=1, le=10000, description="最多返回的行数")
        ] = 1000,
    ) -> dict[str, Any] | str:
        """在指定数据源上执行只读 SQL，返回列名与数据行。"""
        try:
            ds = get_datasource(settings, datasource)
            return manager.execute_query(ds, sql, limit=limit)
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    def list_schemas(datasource: DatasourceArg) -> dict[str, Any] | str:
        """列出指定数据源下的所有 schema / 数据库。"""
        try:
            ds = get_datasource(settings, datasource)
            return {"schemas": manager.list_schemas(ds)}
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    def list_tables(
        datasource: DatasourceArg,
        schema_name: Annotated[
            str | None, Field(description="schema/库名（可选，默认全部）")
        ] = None,
        search: Annotated[
            str | None, Field(description="按表名模糊过滤（可选）")
        ] = None,
    ) -> dict[str, Any] | str:
        """列出数据源中的表，可按 schema 过滤、按表名模糊搜索。"""
        try:
            ds = get_datasource(settings, datasource)
            tables = manager.list_tables(ds, schema=schema_name, search=search)
            return {"count": len(tables), "tables": tables}
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    def describe_table(
        datasource: DatasourceArg,
        table: Annotated[str, Field(description="表名")],
        schema_name: Annotated[
            str | None, Field(description="schema/库名（可选）")
        ] = None,
    ) -> dict[str, Any] | str:
        """查看指定表的字段结构（列名、类型、可空性、默认值）。"""
        try:
            ds = get_datasource(settings, datasource)
            cols = manager.describe_table(ds, table, schema=schema_name)
            return {"table": table, "columns": cols}
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))
