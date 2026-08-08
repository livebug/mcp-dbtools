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
from .export import ExportManager
from .jdbc import JDBCError, JDBCManager, _sql_kind
from .monitor import Monitor, wrap_tool

logger = logging.getLogger(__name__)

DatasourceArg = Annotated[
    str, Field(description="数据源名称（先调用 list_datasources 获取）")
]


def _err(msg: str) -> str:
    return f"❌ {msg}"


def register_tools(
    mcp: FastMCP,
    settings: Settings,
    manager: JDBCManager,
    monitor: Monitor,
    export_mgr: ExportManager,
) -> None:
    @mcp.tool()
    @wrap_tool(monitor, "list_datasources")
    def list_datasources() -> dict[str, Any]:
        """列出所有已配置的数据库数据源（名称、类型、驱动、说明），不含密码。"""
        return {"datasources": [ds.safe_dict for ds in settings.datasources]}

    @mcp.tool()
    @wrap_tool(monitor, "test_connection")
    def test_connection(datasource: DatasourceArg) -> dict[str, Any] | str:
        """测试到指定数据源的 JDBC 连接，返回数据库产品与版本信息。"""
        try:
            ds = get_datasource(settings, datasource)
            return manager.test_connection(ds)
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "execute_query")
    def execute_query(
        datasource: DatasourceArg,
        sql: Annotated[
            str, Field(description="SQL 语句（默认只读，写操作需 confirm=true 才执行）")
        ],
        limit: Annotated[
            int, Field(default=300, ge=1, le=10000, description="最多返回行数（上限 10000，默认 300）")
        ] = 300,
        confirm: Annotated[
            bool, Field(description="写操作（DELETE/UPDATE/INSERT/DROP 等）二次确认，默认 false 会拦截")
        ] = False,
    ) -> dict[str, Any] | str:
        """在指定数据源上执行 SQL，返回列名与数据行（含 execution_time_ms）。

        安全：默认只读模式，写操作（DELETE/UPDATE 等）需 confirm=true 二次确认并记审计；
        大数据量查询默认最多返回 300 行，如需更多请用 start_export 异步导出。
        """
        try:
            ds = get_datasource(settings, datasource)
            return manager.execute_query(ds, sql, limit=limit, confirm=confirm)
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "execute_script")
    def execute_script(
        datasource: DatasourceArg,
        script_path: Annotated[
            str, Field(description="服务器上的 SQL 脚本文件路径（须在脚本根目录内）")
        ],
        params: Annotated[
            dict[str, Any] | None, Field(description="脚本参数，替换 ${V_DATE} 等占位符")
        ] = None,
        read_only: Annotated[
            bool, Field(description="是否只允许只读语句（默认 true）")
        ] = True,
        limit: Annotated[
            int, Field(default=300, ge=1, le=10000, description="每条语句最多返回行数")
        ] = 300,
        confirm: Annotated[
            bool, Field(description="写操作二次确认（read_only=false 且含写语句时需 confirm=true）")
        ] = False,
    ) -> dict[str, Any] | str:
        """执行服务器上的 SQL 脚本文件，支持 ${VAR} 参数占位符（如 ${V_DATE}）。

        参数来源：params -> 环境变量 -> 缺失报错。脚本须位于脚本根目录内。
        默认只读；写操作需 read_only=false + confirm=true 二次确认并记审计。
        """
        try:
            ds = get_datasource(settings, datasource)
            return manager.execute_script(
                ds, script_path, params=params, read_only=read_only,
                limit=limit, confirm=confirm,
            )
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "list_schemas")
    def list_schemas(datasource: DatasourceArg) -> dict[str, Any] | str:
        """列出指定数据源下的所有 schema / 数据库。"""
        try:
            ds = get_datasource(settings, datasource)
            return {"schemas": manager.list_schemas(ds)}
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "list_tables")
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
    @wrap_tool(monitor, "describe_table")
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

    # ------------------------------------------------------------------
    # 监控与审计工具
    # ------------------------------------------------------------------
    @mcp.tool()
    @wrap_tool(monitor, "get_status")
    def get_status() -> dict[str, Any]:
        """监控总览：各数据源 JDBC 连接/执行状态、JVM 与进程内存、工具调用统计、运行时长。"""
        return {
            "uptime_seconds": round(monitor.uptime_seconds(), 1),
            "datasources": manager.all_status(),
            "memory_mb": {
                "process_rss": Monitor.process_memory_mb(),
                "jvm_heap": Monitor.jvm_memory_mb(),
            },
            "tool_summary": monitor.tool_summary(),
            "meta_cache": manager.meta_cache_stats(),
            "transactions": manager.all_transaction_status(),
        }

    @mcp.tool()
    @wrap_tool(monitor, "get_datasource_status")
    def get_datasource_status(datasource: DatasourceArg) -> dict[str, Any] | str:
        """查看单个数据源的 JDBC 状态：连接时间、最近活动、查询/错误次数、平均耗时等。"""
        try:
            get_datasource(settings, datasource)  # 校验数据源存在
            return manager.ds_status(datasource)
        except ConfigError as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "get_execution_history")
    def get_execution_history(
        limit: Annotated[
            int, Field(default=50, ge=1, le=1000, description="返回最近 N 条")
        ] = 50,
        tool: Annotated[
            str | None, Field(description="按工具名过滤（如 execute_query）")
        ] = None,
        ok: Annotated[
            bool | None, Field(description="按结果过滤（true=成功 / false=失败）")
        ] = None,
    ) -> dict[str, Any]:
        """查询工具/SQL 执行历史（审计）：时间、工具、参数、耗时、是否成功。"""
        items = monitor.execution_history(limit=limit, tool=tool, ok=ok)
        return {"count": len(items), "history": items}

    # ------------------------------------------------------------------
    # 熔断状态
    # ------------------------------------------------------------------
    @mcp.tool()
    @wrap_tool(monitor, "get_circuit_status")
    def get_circuit_status(datasource: DatasourceArg) -> dict[str, Any] | str:
        """查询数据源的熔断状态：是否熔断、连续失败次数、冷却剩余时间。"""
        try:
            get_datasource(settings, datasource)  # 校验存在
            return manager.circuit_status(datasource)
        except ConfigError as exc:
            return _err(str(exc))

    # ------------------------------------------------------------------
    # 显式事务（BEGIN / COMMIT / ROLLBACK）
    # ------------------------------------------------------------------
    @mcp.tool()
    @wrap_tool(monitor, "begin_transaction")
    def begin_transaction(datasource: DatasourceArg) -> dict[str, Any] | str:
        """在指定数据源开启事务：独占一个连接并关闭自动提交。

        同一数据源同时只允许一个活动事务；超时（默认 300s）自动回滚释放。
        后续用 execute_in_transaction 执行、commit_transaction 提交、rollback_transaction 回滚。
        """
        try:
            ds = get_datasource(settings, datasource)
            return manager.begin_transaction(ds)
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "execute_in_transaction")
    def execute_in_transaction(
        datasource: DatasourceArg,
        sql: Annotated[
            str, Field(description="事务内执行的 SQL（写操作需 confirm=true）")
        ],
        limit: Annotated[
            int, Field(default=300, ge=1, le=10000, description="最多返回行数")
        ] = 300,
        confirm: Annotated[
            bool, Field(description="写操作二次确认，默认 false 会拦截")
        ] = False,
    ) -> dict[str, Any] | str:
        """在活动事务连接上执行 SQL（不自动提交，可继续执行或提交/回滚）。

        必须先调用 begin_transaction；返回结果带 in_transaction 提示。
        """
        try:
            ds = get_datasource(settings, datasource)
            return manager.execute_in_transaction(
                ds, sql, limit=limit, confirm=confirm
            )
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "commit_transaction")
    def commit_transaction(datasource: DatasourceArg) -> dict[str, Any] | str:
        """提交并结束事务，连接归还连接池。"""
        try:
            ds = get_datasource(settings, datasource)
            return manager.commit_transaction(ds)
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "rollback_transaction")
    def rollback_transaction(datasource: DatasourceArg) -> dict[str, Any] | str:
        """回滚并结束事务，连接归还连接池。"""
        try:
            ds = get_datasource(settings, datasource)
            return manager.rollback_transaction(ds)
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "get_transaction_status")
    def get_transaction_status(datasource: DatasourceArg) -> dict[str, Any] | str:
        """查询数据源是否有活动事务及其已持续时间。"""
        try:
            get_datasource(settings, datasource)  # 校验存在
            return manager.transaction_status(
                get_datasource(settings, datasource)
            )
        except ConfigError as exc:
            return _err(str(exc))

    # ------------------------------------------------------------------
    # 大数据量异步导出
    # ------------------------------------------------------------------
    @mcp.tool()
    @wrap_tool(monitor, "start_export")
    def start_export(
        datasource: DatasourceArg,
        sql: Annotated[str, Field(description="导出的 SQL（应为查询语句）")],
        delimiter: Annotated[
            str, Field(description="字段分隔符（默认逗号，可自定义 \\t、| 等）")
        ] = ",",
        include_header: Annotated[bool, Field(description="是否包含表头行")] = True,
        filename: Annotated[
            str | None, Field(description="导出文件名（可选，自动追加 .txt）")
        ] = None,
        limit: Annotated[
            int, Field(default=100000, ge=1, le=1000000, description="导出最大行数")
        ] = 100000,
        confirm: Annotated[
            bool, Field(description="写操作确认（导出仅支持查询语句，默认 false）")
        ] = False,
    ) -> dict[str, Any] | str:
        """发起大数据量异步导出：后台执行 SQL 并写入文本文件（只导出数据）。

        返回 export_id 与 download_url；用 get_export_status 轮询状态，
        完成后访问 /export/{id}/download 下载文件。
        分隔符可自定义（默认逗号）；CSV/Excel 等格式由客户端自行处理，服务端只导出数据。
        """
        try:
            ds = get_datasource(settings, datasource)
            if _sql_kind(sql) == "write" and not confirm:
                return _err("导出仅支持查询语句；写操作需 confirm=true")
            return export_mgr.start(
                ds, sql, delimiter=delimiter, include_header=include_header,
                filename=filename, limit=limit,
            )
        except (ConfigError, JDBCError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @wrap_tool(monitor, "get_export_status")
    def get_export_status(
        export_id: Annotated[str, Field(description="导出任务 ID（start_export 返回）")]
    ) -> dict[str, Any] | str:
        """轮询查询异步导出任务状态（pending / running / succeeded / failed）。"""
        task = export_mgr.status(export_id)
        if not task:
            return _err(f"导出任务不存在: {export_id}")
        return task

    @mcp.tool()
    @wrap_tool(monitor, "list_exports")
    def list_exports() -> dict[str, Any]:
        """列出全部异步导出任务及状态（含 download_url）。"""
        return {"exports": export_mgr.list()}
