# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 与
[Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.0.2] - 2026-08-10

### 修复

- 离线部署包遗漏 `docs/使用说明.md`，已补入打包流程。
- 修正使用手册 4 处错误：版本号、工具数量、离线部署路径、pm2 启动路径。

## [1.0.1] - 2026-08-10

### 文档

- 新增 AI 协作定制化：`AGENTS.md` 项目规则、4 个 Skills（offline-deploy / release /
  add-datasource / security-review）、`db-ops` 专用代理。
- README 增加「AI 协作」章节。

## [1.0.0] - 2026-08-09

首个正式稳定版发布。自 0.1.0 以来累计完成了连接池/并发、安全加固、可观测性、
运维与部署等全链路能力，达到生产可用状态。

### 新增（核心查询）

- 多数据源 JDBC 接入：GaussDB / openGauss、TDH Inceptor、Hive、PostgreSQL 兼容库
  （按 `type` 自动切换 information_schema / Hive 方言）。
- `execute_query` 查询执行（列名 + 数据行 + 耗时返回）。
- `execute_script` 服务器端 SQL 脚本执行，支持 `${VAR}` 占位符、只读模式、防目录穿越。
- `list_schemas` / `list_tables` / `describe_table` 元数据查询。

### 新增（并发与性能）

- **连接池**：每数据源 `pool_size` 个 JDBC 连接，多客户并发复用。
- 工具异步化（`asyncio.to_thread`），不阻塞事件循环。
- **元数据 TTL 缓存**：`list_*` / `describe_*` 结果缓存（默认 60s）。
- **大数据量异步导出**：`start_export` / `get_export_status` / `list_exports`，
  后台逐批写文本、自定义分隔符、只导出数据，`/export/{id}/download` 下载。

### 新增（安全）

- **写操作二次确认**：`DELETE/UPDATE/INSERT/DROP` 等默认拦截，需 `confirm=true` 并记审计。
- **Bearer Token 鉴权**（`MCP_DBTOOLS_AUTH_TOKEN`）。
- **密码加密存储**：`{ENC:...}` AES-256-GCM（`scripts/encrypt_password.py` 生成，
  密钥 `MCP_DBTOOLS_SECRET_KEY`），配置不再保存明文密码。
- **熔断**：连续失败阈值触发熔断 + 冷却 + 半开恢复。
- **按客户端 IP 限流**：令牌桶 QPS 限流，超限 429。
- 查询行数上限（默认 300 / 上限 10000）防 OOM。

### 新增（可观测性与运维）

- **审计**：JSONL 落盘（按大小轮转）+ SQLite 持久化，记录调用方 IP/UA/SQL/耗时。
- **人工审计页面** `/audit`：检索、详情、统计、CSV 导出。
- **监控**：`get_status` 工具 + `/metrics`（数据源状态 / JVM 与进程内存 / 工具统计 /
  缓存命中 / 事务状态）。
- **健康检查**：`/health`（`?deep=1` 真实 `SELECT 1`）+ 后台探活 / 自动重连。
- **服务日志**：落盘 `logs/app.log`（按大小轮转）+ 网页查看 `/logs`。
- **显式事务**：`begin_transaction` / `execute_in_transaction` / `commit_transaction` /
  `rollback_transaction` / `get_transaction_status`，超时自动回滚。
- 导出文件自动清理（超龄 / 超量）。

### 部署

- 内网 **pm2 离线部署**：`scripts/build_offline.sh` 生成离线包 +
  `deploy/install_offline.sh` 安装，`deploy/ecosystem.config.cjs` 进程托管。
- systemd 单元 `deploy/mcp-dbtools.service`。

### 文档

- 新增完整使用操作说明 `docs/使用说明.md`（部署 / 配置 / 工具 / 安全 / 监控 / FAQ）。

[0.1.0]: 首个初始版本（基础查询 + 脚本执行 + 基础审计）。
