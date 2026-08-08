# mcp-dbtools

基于 **Python + JDBC** 的数据库 MCP（Model Context Protocol）服务，用于通过 LLM 客户端查询数据库。

支持数据源：

| 数据源 | 驱动 | JDBC URL 示例 |
| --- | --- | --- |
| GaussDB / openGauss | `org.opengauss.Driver` | `jdbc:opengauss://host:5432/db` |
| TDH Inceptor（星环） | `org.apache.hive.jdbc.HiveDriver` | `jdbc:hive2://host:10000/default` |
| 其他 Hive 兼容 | `org.apache.hive.jdbc.HiveDriver` | `jdbc:hive2://...` |
| 其他 PostgreSQL 兼容 | `org.postgresql.Driver` | `jdbc:postgresql://...` |

- 服务部署在服务器上，远程客户端通过 **HTTP（MCP streamable-http）** 调用，也支持 SSE 与本地 stdio。
- JDBC 桥接通过 `jaydebeapi`（JPype 加载 JVM）。

## 架构

```mermaid
flowchart LR
    C[远程 MCP 客户端<br/>Claude / VS Code / 自定义] -- "HTTP POST /mcp<br/>MCP streamable-http" --> S[mcp-dbtools<br/>uvicorn + FastMCP]
    S -- jaydebeapi / JPype --> J[JVM]
    J -- "JDBC (jdbc:opengauss://)" --> G[GaussDB / openGauss 容器]
    J -- "JDBC (jdbc:hive2://)" --> T[TDH Inceptor]
    S --> H[/health 健康检查/]
```

## 目录结构

```
mcp-dbtools/
├── src/mcp_dbtools/
│   ├── server.py        # MCP 服务入口（HTTP/SSE/stdio + 鉴权 + 健康检查）
│   ├── jdbc.py          # JDBC 连接管理、查询、元数据（按方言）
│   ├── tools.py         # MCP 工具定义
│   └── config.py        # 配置加载（datasources.json + 环境变量）
├── config/
│   ├── datasources.json          # 数据源配置（宿主机直连）
│   └── datasources.docker.json   # 数据源配置（Docker Compose 内部网络）
├── drivers/             # JDBC 驱动 jar（脚本下载 / 手工放置）
├── scripts/
│   ├── download_drivers.py   # 下载 openGauss 驱动
│   ├── mcp_client_demo.py    # HTTP 客户端演示
│   └── java/TdhTest.java     # TDH 连接自测工具
├── docker/              # Docker 部署与测试库
├── deploy/              # systemd 单元
└── tests/
```

## 环境要求

- Python 3.10+（已测 3.13）
- JRE/JDK 8+（JPype 需要，已测 OpenJDK 21）
- （部署）Docker 或 systemd Linux 服务器

## 快速开始

```bash
# 1. 安装依赖
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# 2. 生成配置
cp config/datasources.json.example config/datasources.json
cp .env.example .env                 # 按需修改密码

# 3. 下载 JDBC 驱动（openGauss/GaussDB）
python scripts/download_drivers.py

# 4. 启动 HTTP 服务
python -m mcp_dbtools --transport streamable-http
# 服务地址: http://<服务器IP>:8000/mcp

# 5. 验证
curl http://127.0.0.1:8000/health

# 6. 用演示客户端远程调用
python scripts/mcp_client_demo.py --url http://127.0.0.1:8000/mcp
```

### 对接已有 GaussDB 测试库

工作区已有的 `gaussdb` 容器（openGauss 5.0，端口 5432，库 `gaussdb`）已可直接使用。
注意：openGauss 容器默认 `pg_hba.conf` 只允许本机 `trust`，需要允许远程访问：

```bash
docker exec gaussdb bash -c \
  "echo 'host all all 0.0.0.0/0 sha256' >> /var/lib/opengauss/data/pg_hba.conf && kill -HUP 1"
```

## 配置说明（config/datasources.json）

```json
{
  "datasources": [
    {
      "name": "gaussdb_test",
      "type": "gaussdb",
      "description": "Docker 测试库",
      "driver_class": "org.opengauss.Driver",
      "jdbc_url": "jdbc:opengauss://127.0.0.1:5432/gaussdb",
      "username": "gaussdb",
      "password": "{ENV:GAUSS_PASSWORD}",
      "jars": ["opengauss-jdbc-5.0.0-og.jar"]
    }
  ]
}
```

- `type`: `gaussdb` / `opengauss` / `postgresql` 走 information_schema 方言；`tdh` / `hive` / `inceptor` 走 `SHOW DATABASES` 等 Hive 方言。
- `password` 支持 `{ENV:VAR}` 从环境变量注入，避免明文入库。
- 可用环境变量见 `.env.example`（`MCP_DBTOOLS_HOST/PORT/TRANSPORT/AUTH_TOKEN/CONFIG/DRIVERS_DIR/MAX_ROWS` 等）。

## MCP 工具

| 工具 | 说明 |
| --- | --- |
| `list_datasources` | 列出已配置数据源（不含密码） |
| `test_connection` | 测试 JDBC 连接，返回产品与版本 |
| `execute_query` | 执行只读 SQL，返回列 + 行（限 `limit`） |
| `list_schemas` | 列出 schema / 数据库 |
| `list_tables` | 列出表（可按 schema、表名过滤） |
| `describe_table` | 查看表结构 |

> 安全：工具只能访问 `datasources.json` 中预配置的连接，无法通过参数注入任意 JDBC URL。

## 客户端接入

### 通用 MCP 客户端（HTTP）

将 MCP 端点配置为 `http://<服务器IP>:8000/mcp`。以 Claude Desktop 为例：

```json
{
  "mcpServers": {
    "dbtools": {
      "type": "http",
      "url": "http://192.168.1.10:8000/mcp"
    }
  }
}
```

启用鉴权后需带请求头 `Authorization: Bearer <MCP_DBTOOLS_AUTH_TOKEN>`。

### 本地 stdio（开发调试）

```bash
python -m mcp_dbtools --transport stdio
```

## 服务器部署

### 方式一：Docker Compose（推荐，需外网/内网镜像源）

```bash
cp .env.example .env
docker compose -f docker/compose.yml up -d --build
curl http://localhost:8000/health
```

### 方式二：systemd（裸机）

见 `deploy/mcp-dbtools.service`，安装到 `/etc/systemd/system/` 后：

```bash
systemctl daemon-reload
systemctl enable --now mcp-dbtools
```

### 方式三：内网离线 + pm2（生产推荐，无需外网）

内网环境无法访问 PyPI / Docker Hub 时，通过**离线 pip 包 + pm2** 部署。

#### ① 在【外网构建机】生成离线包

```bash
bash scripts/build_offline.sh
# 产物: mcp-dbtools-offline-YYYYMMDD.tar.gz
# 内含: 项目 wheel + 全部运行时依赖 wheel + JDBC 驱动 + pm2 配置 + 安装脚本
```

> 构建机与内网机需**同为 Linux x86_64 + 相同 Python 主版本**（依赖含 manylinux 二进制 wheel，如 `jpype1`/`pydantic-core`）。

#### ② 拷贝到内网服务器并安装

内网机需先具备：**JRE 8+**（JPype 需要 JVM）、**Node.js + pm2**。

```bash
# 上传离线包后
tar xzf mcp-dbtools-offline-YYYYMMDD.tar.gz
bash mcp-dbtools-offline/install.sh            # 默认安装到 /opt/mcp-dbtools
# 安装脚本会自动: 建 venv -> --no-index 离线安装 -> 生成 .env -> pm2 启动
```

#### ③ 修改内网配置并重启

```bash
vim /opt/mcp-dbtools/.env                            # 端口、鉴权 Token、密码
vim /opt/mcp-dbtools/config/datasources.json         # 数据源 JDBC URL（内网地址）
pm2 restart mcp-dbtools
curl http://127.0.0.1:8000/health
```

#### pm2 常用命令

```bash
pm2 start deploy/ecosystem.config.cjs   # 启动
pm2 logs mcp-dbtools                    # 日志
pm2 reload mcp-dbtools                  # 重启
pm2 save && pm2 startup                 # 开机自启
```

pm2 配置见 `deploy/ecosystem.config.cjs`（可调端口、鉴权 Token、日志路径）。

## 测试

```bash
# 单元测试（配置、方言）
.venv/bin/python -m pytest tests/test_config.py tests/test_dialects.py -q

# 集成测试（需要 GaussDB/openGauss 测试库在 127.0.0.1:5432）
.venv/bin/python -m pytest tests/test_jdbc_integration.py -q
```

## 常见问题

- **`Invalid username/password` / 连接被拒**：检查目标库 `pg_hba.conf` 是否允许你的来源 IP（openGauss 默认仅本机）。
- **`Task group is not initialized`**：需将 `mcp.streamable_http_app()` 作为顶层 ASGI app 运行（本实现已处理），不要二次挂载。
- **TDH 连接失败**：先确保 Inceptor 的 HiveServer2 已启动（TDH 数据栈需在 Transwarp Manager 控制台部署），并用 `scripts/java/TdhTest.java` 验证端口与账号。
- **SOCKS 代理导致 HTTP 客户端报错**：演示客户端已通过 `trust_env=False` 忽略环境代理；若自写客户端，可安装 `httpx[socks]` 或设置 `NO_PROXY`。
