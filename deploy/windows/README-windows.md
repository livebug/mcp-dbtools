# mcp-dbtools Windows 部署说明

> 内网 Windows 机器离线部署（无外网）。本包已含全部依赖的 Windows wheel，无需联网。

## 前置要求

| 组件 | 要求 |
| --- | --- |
| Python | **3.13（64 位）**，与离线包对应；安装时勾选 "Add to PATH" |
| JDK | **JDK 11+**（JPype 需要 JVM，推荐 17/21）。`java -version` 验证 |
| 防火墙 | 放行 8000 端口（远程客户端访问时） |

## 部署步骤

```
1. 解压 mcp-dbtools-offline-windows-*.zip 到目标目录（如 D:\mcp-dbtools）
2. 双击 install_offline.bat（自动建 venv + 离线装依赖 + 生成 .env/datasources.json）
3. 修改 .env（端口/鉴权 Token）与 config\datasources.json（数据源）
4. 确认 JDBC 驱动 jar 已在 drivers\ 目录
5. 启动：.venv\Scripts\python -m mcp_dbtools --transport streamable-http
6. 验证：浏览器打开 http://127.0.0.1:8000/mcp 应返回 MCP 协议信息
```

## 升级（已有旧版本安装）

```
1. 解压新版 mcp-dbtools-offline-windows-*.zip 到任意目录（如 D:\tmp\new）
2. 执行 D:\tmp\new\upgrade_offline.bat D:\mcp-dbtools
   （D:\mcp-dbtools 为旧安装目录；会自动备份 .env/datasources.json、更新代码与依赖、重启服务）
3. 或把新版离线包直接解压覆盖到旧安装目录，然后运行该目录下的 upgrade_offline.bat
```

> 升级会**保留**你的 `.env`、`config\datasources.json` 和自定义 JDBC 驱动 jar，不会丢失配置。

## 数据源配置（config/datasources.json）

```json
{
  "datasources": [
    {
      "name": "db2_test",
      "type": "db2",
      "driver_class": "com.ibm.db2.jcc.DB2Driver",
      "jdbc_url": "jdbc:db2://127.0.0.1:50000/sample",
      "username": "db2inst1",
      "password": "{ENV:DB2_PASSWORD}",
      "jars": ["db2jcc4.jar"]
    }
  ]
}
```

密码支持 `{ENC:...}` 加密存储（首次启动 `--init-password` 生成）或 `{ENV:变量名}` 引用环境变量。

## 常用命令

```bat
:: 启动（HTTP 服务，默认 8000）
.venv\Scripts\python -m mcp_dbtools --transport streamable-http

:: 指定端口
.venv\Scripts\python -m mcp_dbtools --transport streamable-http --port 9000

:: 本地 stdio（供 Claude Desktop 等客户端）
.venv\Scripts\python -m mcp_dbtools --transport stdio
```

## 常驻运行（pm2，推荐）

```bat
:: 1. 安装 pm2（需 Node.js）
npm install -g pm2
npm install -g pm2-windows-startup   :: Windows 开机自启支持

:: 2. 编辑 ecosystem-windows.config.cjs，把 D:/mcp-dbtools 改成你的部署目录
:: 3. 启动
pm2 start ecosystem-windows.config.cjs
pm2 save
pm2-startup install                   :: 注册开机自启

:: 常用
pm2 logs mcp-dbtools     :: 日志
pm2 reload mcp-dbtools   :: 重启
pm2 stop mcp-dbtools
```

## 常驻运行（备选：nssm / 任务计划）

- 用 **nssm** 注册为 Windows 服务，或
- 任务计划程序：新建任务 → 程序 `.venv\Scripts\python.exe` → 参数 `-m mcp_dbtools --transport streamable-http`

## 目录结构

```
mcp-dbtools-offline-windows-*.zip 解压后:
├── install_offline.bat        # 一键离线安装（建 venv + 装依赖 + 生成配置）
├── upgrade_offline.bat        # 离线升级（保留用户配置，重启服务）
├── requirements.txt
├── .env.example               # 环境变量示例（安装时生成 .env）
├── README-windows.md          # 本说明
├── dist/                      # 项目 wheel
├── offline_packages_win/      # Windows 依赖 wheel（32 个包）
├── ecosystem-windows.config.cjs   # pm2 配置（Windows 版）
├── config/
│   └── datasources.json.example   # 数据源配置示例（安装时生成 datasources.json）
├── scripts/
│   └── encrypt_password.py    # 密码加密工具
├── drivers/                   # 放 JDBC 驱动 jar（如 db2jcc4.jar）
└── docs/
```

## 注意事项

- 离线包对应 **Python 3.13**：目标机 Python 版本必须一致（3.13.x 均可）
- DB2 驱动 `db2jcc4.jar` 受 IBM 许可需自行获取放入 `drivers\`
- 服务地址为 `http://<本机IP>:8000/mcp`，供 MCP 客户端（Claude/VS Code/dsh）连接

## 重新生成 Windows 离线包（联网机器上执行）

```bash
# 一键生成 Windows 离线包（zip，win_amd64 + Python 3.13 wheel）
bash scripts/build_offline.sh --platform windows

# 或同时生成 Linux + Windows 两个包
bash scripts/build_offline.sh --platform all
```

> 换 Python 版本时用环境变量指定，如 `WIN_PYTHON_VER=3.12 bash scripts/build_offline.sh --platform windows`。
