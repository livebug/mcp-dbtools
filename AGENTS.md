# mcp-dbtools 项目指引（AI Rules）

> 本文件为在此仓库工作的 AI 助手（Copilot / Claude 等）提供项目级规则。
> 文档索引见 `docs/README.md`；详细操作手册见 `docs/使用说明.md`；版本历史见 `CHANGELOG.md`。

## 项目是什么

基于 **Python + JDBC** 的数据库 MCP 服务，通过 HTTP（streamable-http）让 LLM 客户端查询
GaussDB / openGauss / TDH Inceptor / Hive / DB2 / MySQL / PostgreSQL 等数据库。
JDBC 访问通过 **open-dbclient Java 桥子进程**（NDJSON-RPC，见 `src/mcp_dbtools/bridge.py`），
不再是 jaydebeapi/JPype 内嵌 JVM。桥 jar 路径用 `MCP_DBTOOLS_BRIDGE_JAR` 配置（默认 `bridge.jar`）。

## 构建与测试

```bash
# 安装（开发模式）
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .

# 运行测试（必须全部通过后再提交）
. .venv/bin/activate && python -m pytest -q

# 启动本地服务
GAUSS_PASSWORD=... . .venv/bin/activate && python -m mcp_dbtools --transport streamable-http
```

- 依赖锁定：`mcp` 必须是 **1.x（<2.0）**，2.x 是全新 API 不可用。
- `tests/test_jdbc_integration.py` 需要本地 openGauss 容器 + JDBC 驱动，缺失时自动 skip，不算失败。
- 集成测试前确保 `drivers/` 有 jar：`python scripts/download_drivers.py`。

## 架构速览（改代码前先读对应模块）

| 文件 | 职责 |
| --- | --- |
| `src/mcp_dbtools/server.py` | CLI 入口：参数解析、日志、transport 选择 |
| `src/mcp_dbtools/app.py` | ASGI 装配：MCP + `/api/v1` + `/ui` + 中间件（客户端信息/鉴权/限流） |
| `src/mcp_dbtools/api/` | REST API 分组：`system` / `mcp` / `datasources` / `query` / `metadata` / `exports` / `audit` / `logs`（`deps.py` 放统一响应包与错误码） |
| `src/mcp_dbtools/web/` | Web 控制台：`/ui` 路由 + `static/` 零构建单页（index.html / app.js / style.css） |
| `src/mcp_dbtools/services/` | 与传输无关的业务逻辑：数据源配置读写与密码加密、日志读取 |
| `src/mcp_dbtools/bridge.py` | Java 桥客户端：spawn `bridge.jar`、NDJSON-RPC 请求/响应/事件、错误映射 |
| `src/mcp_dbtools/jdbc.py` | 数据访问：经桥做查询/元数据/事务/健康，含熔断、元数据缓存、脚本执行 |
| `src/mcp_dbtools/tools.py` | 20 个 MCP 工具定义 |
| `src/mcp_dbtools/config.py` | 配置加载（datasources.json + `MCP_DBTOOLS_*` 环境变量 + `{ENC:...}` 密码解密） |
| `src/mcp_dbtools/monitor.py` | 审计（JSONL 轮转 + SQLite）、执行历史、指标 |
| `src/mcp_dbtools/export.py` | 异步大数据导出 + 过期文件清理 |
| `src/mcp_dbtools/crypto.py` | 密码 AES-256-GCM 加密（`{ENC:...}`） |
| `src/mcp_dbtools/ratelimit.py` | 令牌桶限流中间件 |

## 代码约定

- Python 3.10+，每个模块 `from __future__ import annotations`；注释用**中文**。
- MCP 工具参数用**标量 + `Annotated[str, Field(...)]`**，不要用单个 pydantic model 参数（跨版本行为不一致）。
- FastMCP 的 `streamable_http_app()` 自带 lifespan，**必须作为顶层 ASGI app**，不能二次 mount。
- **`jdbc.py` 里 `import time as _time`**——`from datetime import ... time` 会遮蔽 `time` 模块。
- JDBC 错误统一由 `jdbc._request` 映射为 `JDBCError`；桥错误码的中文文案集中在 `_BRIDGE_ERROR_MESSAGES`。
- 新增配置项必须同时更新：`config.py`（Settings 字段 + 环境变量）、`.env.example`、`docs/使用说明.md`。
- 新增 MCP 工具必须 `@wrap_tool(monitor, "工具名")` 包装以记录审计；**工具描述保持一句话**，
  详细参数由 `/api/v1/mcp/tools` 自动暴露，不要往 docstring 和文档里堆参数表。

## 安全红线（不可违反）

- **密码不得明文入库/提交**：一律用 `{ENC:...}`（`scripts/encrypt_password.py` 生成，密钥 `MCP_DBTOOLS_SECRET_KEY`）。
- **写操作默认拦截**：DELETE/UPDATE/INSERT/DROP 等必须 `confirm=true` 才放行并记审计。
- 工具只能连 `datasources.json` 预配置的连接，禁止接受任意 JDBC URL 参数。
- 审计/监控对外输出不得包含密码（`safe_dict` 已脱敏，新加输出注意同样处理）。
- 工具响应与错误信息**不得输出服务器绝对路径**：脚本相关统一用脚本根目录内的相对路径，
  目录提示走 `JDBCManager._safe_path_display`（相对配置值原样、绝对路径只留末尾两级）。

## 提交要求

- 改动需配套：代码 + `tests/` 测试（全绿）+ 文档（README / docs / .env.example）+ 必要时 CHANGELOG。
- 提交信息用中文，`feat:` / `fix:` / `docs:` / `release:` 前缀。
- 发布新版本：遵循 `.github/skills/release` 技能流程。

## 专项流程（Skills）

- 离线部署打包 / 内网安装 → `.github/skills/offline-deploy`
- 发布新版本 → `.github/skills/release`
- 新增数据源 → `.github/skills/add-datasource`
- 安全配置审查 → `.github/skills/security-review`
