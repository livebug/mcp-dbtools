# mcp-dbtools 项目指引（AI Rules）

> 本文件为在此仓库工作的 AI 助手（Copilot / Claude 等）提供项目级规则。
> 详细操作手册见 `docs/使用说明.md`；版本历史见 `CHANGELOG.md`。

## 项目是什么

基于 **Python + JDBC** 的数据库 MCP 服务，通过 HTTP（streamable-http）让 LLM 客户端查询
GaussDB / openGauss / TDH Inceptor / Hive / PostgreSQL 等数据库。JVM 桥接用 jaydebeapi。

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
| `src/mcp_dbtools/server.py` | 服务入口：HTTP 路由、鉴权、限流、健康检查、审计/日志页面 |
| `src/mcp_dbtools/jdbc.py` | JDBC 连接池、查询、元数据缓存、熔断、显式事务、健康探测 |
| `src/mcp_dbtools/tools.py` | 20 个 MCP 工具定义 |
| `src/mcp_dbtools/config.py` | 配置加载（datasources.json + `MCP_DBTOOLS_*` 环境变量 + `{ENC:...}` 密码解密） |
| `src/mcp_dbtools/monitor.py` | 审计（JSONL 轮转 + SQLite）、执行历史、指标 |
| `src/mcp_dbtools/export.py` | 异步大数据导出 + 过期文件清理 |
| `src/mcp_dbtools/audit_page.py` / `logs_page.py` | 审计 / 日志 HTML 页面 |
| `src/mcp_dbtools/crypto.py` | 密码 AES-256-GCM 加密（`{ENC:...}`） |
| `src/mcp_dbtools/ratelimit.py` | 令牌桶限流中间件 |

## 代码约定

- Python 3.10+，每个模块 `from __future__ import annotations`；注释用**中文**。
- MCP 工具参数用**标量 + `Annotated[str, Field(...)]`**，不要用单个 pydantic model 参数（跨版本行为不一致）。
- FastMCP 的 `streamable_http_app()` 自带 lifespan，**必须作为顶层 ASGI app**，不能二次 mount。
- **`jdbc.py` 里 `import time as _time`**——`from datetime import ... time` 会遮蔽 `time` 模块。
- JPype 返回的 Java 数值是 `int/float` 子类，判断用 `isinstance` 而非 `type` 相等；`BigDecimal` 走 `scale()` 判断。
- 新增配置项必须同时更新：`config.py`（Settings 字段 + 环境变量）、`.env.example`、`docs/使用说明.md`。
- 新增 MCP 工具必须 `@wrap_tool(monitor, "工具名")` 包装以记录审计。

## 安全红线（不可违反）

- **密码不得明文入库/提交**：一律用 `{ENC:...}`（`scripts/encrypt_password.py` 生成，密钥 `MCP_DBTOOLS_SECRET_KEY`）。
- **写操作默认拦截**：DELETE/UPDATE/INSERT/DROP 等必须 `confirm=true` 才放行并记审计。
- 工具只能连 `datasources.json` 预配置的连接，禁止接受任意 JDBC URL 参数。
- 审计/监控对外输出不得包含密码（`safe_dict` 已脱敏，新加输出注意同样处理）。

## 提交要求

- 改动需配套：代码 + `tests/` 测试（全绿）+ 文档（README / docs / .env.example）+ 必要时 CHANGELOG。
- 提交信息用中文，`feat:` / `fix:` / `docs:` / `release:` 前缀。
- 发布新版本：遵循 `.github/skills/release` 技能流程。

## 专项流程（Skills）

- 离线部署打包 / 内网安装 → `.github/skills/offline-deploy`
- 发布新版本 → `.github/skills/release`
- 新增数据源 → `.github/skills/add-datasource`
- 安全配置审查 → `.github/skills/security-review`
