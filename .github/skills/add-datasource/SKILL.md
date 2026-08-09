---
name: add-datasource
description: '为 mcp-dbtools 新增一个数据库数据源。Use when: 添加数据源、新数据库接入、datasources.json、JDBC 驱动配置、生成密码加密串、encrypt_password、配置数据源、接入新库。'
argument-hint: '新增一个数据库数据源'
user-invocable: true
---

# 新增数据源

为 mcp-dbtools 接入一个新的数据库连接，使其可通过 MCP 工具查询。

## 何时使用
- 需要让 LLM 客户端查询一个新数据库（GaussDB / openGauss / TDH / Hive / PostgreSQL 兼容）
- 现有数据源连接信息变更（主机、密码、驱动）

## 流程

### 1. 确认驱动 jar
- 驱动 jar 必须放在 `drivers/` 目录（如 `opengauss-jdbc-5.0.0-og.jar`、`inceptor-jdbc.jar`）。
- 缺少时用 `python scripts/download_drivers.py`（openGauss）或从官方/容器提取。
- 确认 `driver_class` 全名（如 `org.opengauss.Driver`、`org.apache.hive.jdbc.HiveDriver`）。

### 2. 编辑 `config/datasources.json`
在 `datasources` 数组中新增一个对象：

```json
{
  "name": "my_db",
  "type": "gaussdb",
  "description": "说明",
  "driver_class": "org.opengauss.Driver",
  "jdbc_url": "jdbc:opengauss://host:5432/db",
  "username": "user",
  "password": "{ENC:...}",
  "jars": ["opengauss-jdbc-5.0.0-og.jar"]
}
```

字段：
- `name`：唯一标识，MCP 工具用 `datasource` 参数引用
- `type`：`gaussdb`/`opengauss`/`postgresql`（information_schema 方言）或 `tdh`/`hive`/`inceptor`（Hive 方言）
- `password`：**禁止明文**，用 `{ENC:...}` 加密串

### 3. 生成加密密码（不要写明文）
```bash
# 需要 .env 里已配置 MCP_DBTOOLS_SECRET_KEY
echo -n "真实密码" | python scripts/encrypt_password.py --stdin
# 输出 {ENC:...}，填入 password 字段
```

### 4. 验证
```bash
# 启动后测试
. .venv/bin/activate && python -m mcp_dbtools --transport streamable-http
curl http://127.0.0.1:8000/health?deep=1          # 该数据源应 ok:true
# 或 MCP 工具
python scripts/mcp_client_demo.py --url http://127.0.0.1:8000/mcp
```
也可用 `test_connection` / `list_tables` 工具确认元数据正常。

### 5. 收尾
- 若数据源有固定 schema 习惯，可在 `docs/使用说明.md` 数据源章节补充示例。
- 检查 `safe_dict`（`list_datasources`）输出不含密码。
- 提交时确认 `datasources.json`（若含真实配置）**不被提交**——示例放 `config/datasources.json.example`。

## 输出
- 新数据源在 `list_datasources` 可见
- `test_connection` / `execute_query` 可用
- 密码为 `{ENC:...}` 密文，无明文泄露

## 注意
- 禁止在代码/文档/示例中放真实密码。
- 连接失败先查：驱动 jar 是否存在、`driver_class` 是否正确、内网端口可达、`pg_hba.conf` 是否开放（openGauss 默认仅本机）。
