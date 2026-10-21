# 技术选型分析：Node.js 还是 Python

> 结论先行：**保持 Python**。数据面（JDBC）已经通过 Java 桥与语言解耦，换 Node 不会让
> 依赖变少，只会把 4000 行已验证代码和 127 个测试重写一遍。下面给出依据和触发换栈的条件。

## 1. 现状基线

| 项 | 数值 |
| --- | --- |
| Python 代码 | ~4.1k 行（`src/mcp_dbtools/`，含 api/web/services 重构后） |
| 测试 | 127 passed / 28 skipped（集成测试按需 skip） |
| 运行时依赖 | 6 个（mcp、uvicorn、starlette、python-dotenv、pydantic、cryptography） |
| 数据面 | `bridge.jar`（Java 17），NDJSON-RPC over stdin/stdout 或 TCP |
| 部署形态 | venv + wheel、离线包（便携 Python + JRE）、Docker、systemd/pm2 |
| 前端 | 零构建静态单页，无 node 依赖 |

**关键事实**：真正访问数据库的是 JVM 里的 JDBC 驱动（GaussDB / Inceptor / DB2 等只有
Java 驱动）。Python 只是编排层——换 Node 也**必须**保留这个 Java 桥，否则就要用
`node-jdbc`（内嵌 JVM，等于退回被淘汰的 jaydebeapi 模式）或纯 JS 驱动（覆盖不了上述数据库）。

## 2. 评估维度与打分

权重按"对本项目的重要性"给，1–5 分制。

| 维度 | 权重 | Python | Node.js | 说明 |
| --- | --- | --- | --- | --- |
| 数据面 / JDBC 能力 | 30% | 5 | 2 | 两者都靠 Java 桥；Node 侧没有成熟的桥客户端，需重写 NDJSON-RPC 客户端与重连/熔断/租户逻辑 |
| 现有资产与迁移成本 | 20% | 5 | 1 | 4.3k 行 + 127 测试 + 20 个工具 + 部署脚本；重写约 2–3 人月且有回归风险 |
| 部署与离线交付 | 15% | 4 | 3 | Python 有 wheel 离线目录 + 便携解释器方案；Node 的 node_modules 体积大，但 `node --experimental-sea`/pkg 也可行 |
| MCP SDK 与协议 | 10% | 5 | 4 | 官方 Python SDK（FastMCP 参考实现）与 TS SDK 都支持 streamable-http；Python 侧已完成 1.x 锁定与踩坑 |
| 并发 / 性能 | 10% | 3 | 4 | DB 调用在桥/线程池，瓶颈在 JDBC 与数据库；大结果集 JSON 序列化 Node 略快，但行数据已在 Java 侧序列化 |
| 生态与团队维护 | 10% | 4 | 4 | 加密/审计/限流/调度两边都够用；取决于团队主语言 |
| 前端协同 | 5% | 2 | 5 | 目前前端零构建；若将来确定上 React/Vite，Node 可统一语言 |
| **加权总分** | 100% | **4.4** | **2.7** | |

## 3. 逐项拆解

### 3.1 数据面（决定性因素）

- GaussDB / openGauss / TDH Inceptor / DB2 / Hive 的官方驱动都是 Java jar；
- 本项目已把 JDBC 抽成独立进程（`bridge.jar`），Python 只发 NDJSON-RPC —— 这层**本来就与语言无关**；
- 换成 Node 后仍然是 `Node → NDJSON-RPC → JVM → JDBC`，链路一模一样，收益为零；
- 若为了"去 Java"改用纯 JS 驱动，则直接失去上述数据库支持，等于推翻项目定位。

### 3.2 迁移成本

需要重写：桥客户端（重连、超时、租户、token）、JDBC 编排层（熔断、元数据缓存、事务、
脚本执行、导出分页）、审计（JSONL 轮转 + SQLite）、限流中间件、配置加载与 `{ENC:...}`
解密、20 个 MCP 工具、REST API 七分组、控制台、离线部署脚本与测试。没有一个能靠"翻译"
零风险完成。

### 3.3 并发模型

- Python：`asyncio` + `asyncio.to_thread`，DB 慢调用不阻塞事件循环；GIL 对 I/O 型服务影响有限；
- Node：单线程事件循环，天然异步；但重 CPU 任务（大结果集转换、CSV 拼接）会阻塞，
  需要 worker_threads；
- 结论：本服务是 I/O 密集 + 外部 JVM 计算，两者差异在数据库响应时间面前可忽略。

### 3.4 部署与离线

- Python：`wheels/` 离线安装、便携解释器 + JRE 自包含包、Docker、systemd/pm2 都已跑通；
- Node：单文件打包（SEA/pkg）体验好，但 `node_modules` 原生模块、跨平台二进制、
  内网 npm 源都是新问题；JRE 仍然要带。
- 结论：Python 现有交付链路更成熟，Node 不会简化"离线内网"这个核心约束。

### 3.5 内存

- JVM 堆（默认可达数百 MB）才是内存大头，Python 进程 RSS 约 60–100MB，Node 约 50–80MB；
- 语言切换对总量影响 < 20%，不值得为此重写。

## 4. 什么情况下应该换 Node

满足以下任意两条再考虑：

1. 团队长期只有 TypeScript 维护能力，Python 无人能改；
2. 前端确定要上 React/Vite 并需要 SSR/BFF，Node 能显著减少一层；
3. 需要大量实时推送（WebSocket/SSE 高频）或与现有 Node 网关深度集成；
4. Java 桥被替换成纯 JS 驱动，且业务数据库范围收窄到 MySQL/PostgreSQL（此时 Node 生态可行）。

即使如此，也建议**先只重写传输层**（MCP + REST 网关），服务层继续调 Python 子进程，
用真实流量验证后再决定是否下沉。

## 5. 若将来迁移的推荐路线

```text
阶段 0  保持 Python（当前）
阶段 1  Node 实现 /mcp 与 /api/v1 网关，反代到 Python（验证 SDK、鉴权、限流）
阶段 2  把无状态端点（system/mcp/audit 查询）迁到 Node，读同一 SQLite/JSONL
阶段 3  查询/导出等有状态能力仍留 Python，直到桥客户端在 Node 侧稳定
阶段 4  评估是否整体替换
```

桥协议（NDJSON-RPC，方法名与帧格式）是两端唯一契约，**任何阶段都不要改**；
这样 Node 与 Python 可以长期共存、灰度切换。

## 6. 维护建议（与选型无关，但影响长期成本）

- 工具描述保持一句话，参数 schema 由 `/api/v1/mcp/tools` 自动暴露（本次已落地），
  避免"文档与代码双维护"；
- 新增数据库只加 `datasources.json` 与驱动 jar，不改核心代码（桥已做能力探测）；
- 每次发布带离线包与 CHANGELOG，降低内网升级成本。
