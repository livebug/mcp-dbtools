"""REST API 层（/api/v1），按资源分组，详见 docs/架构设计.md。

分组一览：

| 分组          | 前缀                          | 职责                         |
| ------------- | ----------------------------- | ---------------------------- |
| system        | /api/v1/system                | 元信息、健康、指标、运行配置 |
| mcp           | /api/v1/mcp                   | MCP 工具清单与参数 schema（自描述） |
| datasources   | /api/v1/datasources           | 数据源配置增删改查与连通性测试 |
| query         | /api/v1/query                 | SQL 执行、执行历史           |
| metadata      | /api/v1/metadata              | 库/表/字段浏览               |
| exports       | /api/v1/exports               | 异步导出任务与文件下载       |
| audit         | /api/v1/audit                 | 审计记录查询                 |
| logs          | /api/v1/logs                  | 服务日志尾部读取             |

另有两个**运维端点**不在 /api/v1 下：``GET /health``、``GET /metrics``。
"""

from __future__ import annotations

from starlette.routing import Route

from .deps import Services, register_routes

__all__ = ["register_routes", "Services", "Route"]
