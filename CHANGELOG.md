# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 与
[Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [1.1.0] - 2026-09-17

### 新增

- **DB2 数据源支持**：新增 `db2` 方言（SYSCAT 目录元数据、`SELECT 1 FROM SYSIBM.SYSDUMMY1`
  探测），新增 DB2 集成测试 `tests/test_db2_integration.py` 与 `db2_test` 数据源配置
  （`config/datasources.json` / `.env.example`），文档补充 DB2 支持说明。
- **MySQL 数据源支持**：新增 `mysql` 方言（information_schema 元数据，排除系统库，
  列注释读取），新增单元测试 `tests/test_mysql.py` 与集成测试 `tests/test_mysql_integration.py`、
  `mysql_test` 数据源配置；JDBC URL 需带 `characterEncoding=UTF-8` 以正确读取中文。
  集成测试依赖 Docker `mysql:8` 测试库（`mysql-test` 容器）。
- **离线部署双平台与升级**：`scripts/build_offline.sh` 支持 `--platform linux|windows|all`
  生成 Linux（tar.gz）与 Windows（zip）离线包；新增 Linux 升级脚本
  `deploy/upgrade_offline.sh`（包内 `upgrade.sh`）与 Windows 升级脚本
  `deploy/windows/upgrade_offline.bat`，升级时自动备份并保留 `.env`、`config/datasources.json`
  与自定义 JDBC 驱动 jar；重写 Windows 安装脚本 `install_offline.bat` 适配离线包结构。
- **离线包支持内网二次开发**：`scripts/build_offline.sh` 在离线包内新增**完整源码快照 `source/`**
  （由构建机 `git archive HEAD` 生成，不含 `.git`；有未提交改动时打印 `[WARN]`）与**开发依赖 wheel**
  （`requirements-dev.txt` + `setuptools`/`wheel`），内网可离线执行 `pip install --no-index -e source`
  与 `python -m pytest`；新增 `docs/二次开发指南.md`（包内 `README-二次开发.md`）；
  Linux `install_offline.sh` 与 Linux/Windows 升级脚本同步复制并**备份** `source/`（含二次开发改动）。
- **GitHub Actions 自动发布**：新增 `.github/workflows/release.yml`，推送 `v*` tag 时自动
  跑测试、校验 tag 与 `pyproject.toml` / `__init__.py` 版本号一致（不一致则中断）、构建
  Linux/Windows 离线包、生成 `SHA256SUMS.txt`，并创建 GitHub Release 附上项目 wheel 与两个
  离线包；支持手动触发（仅上传 Artifacts，不发 Release）。

### 修复

- **同版本号离线升级不生效**：内网已安装版本与新离线包版本号相同时，`pip install --upgrade <wheel>`
  会跳过安装（`already installed with the same version as the provided wheel`），只更新依赖而**代码未更新**，
  升级脚本却仍提示"升级完成"。`deploy/upgrade_offline.sh` 与 `deploy/windows/upgrade_offline.bat`
  改为对项目 wheel 使用 `--force-reinstall --no-deps` 强制重装、依赖单独按 `requirements.txt` 升级，
  并在结尾打印「包内 wheel 版本 | 已安装版本」供核对；Windows 升级脚本新增"服务仍在运行"的停服提醒
  （运行中的进程会锁定 `.venv` 内 `.pyd` 导致覆盖失败）。

- **DB2 结果集抓取兼容**：`execute_query` / `execute_in_transaction` 对无结果集语句
  （INSERT/DELETE 等）跳过抓取；`fetchmany` 取完最后一行后再抓取抛
  "result set is closed" 时按正常结束处理（DB2 驱动行为，`fetchall` 不受影响）。
- **JVM 已启动时驱动补齐**：JVM 已由其他实例启动时用 `jpype.addClassPath` 运行时补齐
  缺失的驱动 jar，修复多数据源/多测试实例场景下驱动类找不到的问题。

### 文档

- **文档与配置同步**：pm2 配置（Linux/Windows）补充 DB2/MySQL 数据源密码与
  `MCP_DBTOOLS_SECRET_KEY`；README 版本号更正为 v1.0.3、补充 DB2/MySQL 数据源与架构图；
  AGENTS.md 数据源列表与工具数（19 个）更正；使用说明环境变量表补充数据源密码变量、
  架构图与驱动下载说明。
- **配置示例补全**：`config/datasources.json.example` 补充 `mysql_test` 示例（原先仅
  `config/datasources.json` 内含 MySQL 配置，而离线包只分发 `.example`，导致内网拿不到
  MySQL 数据源示例）；README 与使用说明版本标识更新为 v1.1.0。

## [1.0.3] - 2026-08-10

### 修复

- **离线包 wheel 平台兼容**：`build_offline.sh` 下载依赖时锁定 manylinux2014
  （glibc 2.17+）平台标签，避免拉到 manylinux_2_28/2_34 过新 wheel（如新版
  cryptography），导致 CentOS 7/8 等内网机无法安装、`{ENC:...}` 密码解密报
  `_HAS_CRYPTO=False`。
- **多数据源 JDBC 驱动加载**：JVM 启动前合并全部数据源的驱动 jar 到 classpath
  （`_ensure_jvm`），修复只对首个连接的数据源生效、其余数据源报驱动找不到的问题。
- **Java 版本要求更正**：JPype 1.5+ 不支持 Java 8，文档与离线安装脚本统一改为
  JRE/JDK 11+（推荐 17/21），安装脚本增加 Java 版本预检。

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
