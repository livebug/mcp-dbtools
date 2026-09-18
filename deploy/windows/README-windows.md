# mcp-dbtools Windows 部署说明

> 内网 Windows 机器离线部署（无外网）。本包已含全部依赖的 Windows wheel，无需联网。

## 两种包形态

| 形态 | 目标机前置 | 包体积 | 构建命令 / 产物名 |
| --- | --- | --- | --- |
| **轻量包**（默认） | 需 Python 3.13（64 位）+ JDK/JRE 11+ | ≈ 39 MB | `--platform windows` → `mcp-dbtools-offline-windows-YYYYMMDD.zip` |
| **自包含包** | **无需任何预装**（Python / JDK / Node 都不用） | ≈ 74 MB | `--platform windows --self-contained` → `mcp-dbtools-offline-windows-selfcontained-YYYYMMDD.zip` |

自包含包在包内直接展开好解压即用的运行时：

- `runtime\python` —— 便携 CPython（含 pip / venv，自带 vcruntime，**免安装、免管理员、不写 PATH/注册表**）
- `runtime\jre` —— Eclipse Temurin JRE 17（`mcp-dbtools-run.cmd` 会自动设为 `JAVA_HOME`）

安装脚本**优先用系统运行时，缺失或版本不符时才回退到包内运行时**，所以同一个包在两类机器上都能用；
服务托管同样自适应：**有 pm2 就用 pm2，没有就用 Windows 自带计划任务（不再强制 Node/pm2）**。

## 前置要求（轻量包）

| 组件 | 要求 |
| --- | --- |
| Python | **3.13（64 位）**，必须与离线包一致（包内依赖为 `cp313 win_amd64` 二进制 wheel，**不能跨版本**）；安装时勾选 "Add to PATH" |
| JDK | **JDK 11+**（JPype 需要 JVM，推荐 17/21）。`java -version` 验证 |
| 防火墙 | 放行 8000 端口（远程客户端连接时） |

> 包内 `python-version-required.txt` 记录要求的 Python 版本，`install_offline.bat` / `upgrade_offline.bat`
> 会据此校验，版本不符会**直接报错**（而不是装到一半失败）。

### 机器上只有 Python 3.8（或其它版本 / 什么都没装）怎么办？

本项目依赖 **`mcp` SDK（全部版本均要求 Python >= 3.10）**，`pyproject.toml` 也声明
`requires-python = ">=3.10"`，因此 **Python 3.8 环境无法安装、也无法运行**
（`pip` 连依赖都解析不出来：`No matching distribution found for mcp`）。两种处理办法：

**办法一：用自包含包（推荐，什么都不用装）**

```
bash scripts/build_offline.sh --platform windows --self-contained
```

目标机解压后直接双击 `install_offline.bat` —— 脚本自动改用包内便携 Python（3.13）建 venv，
用包内 JRE 跑 JDBC，不碰系统里那个 3.8。

**办法二：在目标机上并行安装 Python 3.13**（不影响系统自带的 3.8）

```
1. 离线包内已附带 CPython 官方安装器（python-3.13.x-amd64.exe；构建时 --with-python，默认开启）
2. 静默并行安装（不修改系统 Python）：
   python-3.13.x-amd64.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
3. 重新打开命令行窗口让 PATH 生效，再运行 install_offline.bat
```

也可以直接运行 `install_offline.bat`：检测到 Python 缺失或版本不符时，脚本会**提示并可代跑**
包内安装器（交互确认后执行）。

> Linux 内网机若为 Python 3.8，离线包无法代为升级，需自行准备 Python 3.13
> （系统包管理器、源码编译或 conda），再执行 `install.sh`。

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
1. 先停服务：pm2 stop mcp-dbtools
   （必须：运行中的进程会锁定 .venv 内的 .pyd（jpype 等），导致依赖文件覆盖失败）
2. 解压新版 mcp-dbtools-offline-windows-*.zip 到任意目录（如 D:\tmp\new）
3. 执行 D:\tmp\new\upgrade_offline.bat D:\mcp-dbtools
   （D:\mcp-dbtools 为旧安装目录；会自动备份配置与源码、更新代码与依赖、尝试重启服务）
4. 或把新版离线包直接解压覆盖到旧安装目录，然后运行该目录下的 upgrade_offline.bat
```

> 升级会**保留** `.env`、`config\datasources.json` 和自定义 JDBC 驱动 jar，并把 `source\`（可能含二次开发改动）
> 备份到 `backup-时间戳\source`。
> 项目 wheel 使用 `--force-reinstall` 强制重装——**新旧版本号相同时 pip 默认会跳过安装**，导致代码实际没更新；
> 升级结束会打印「包内 wheel 版本 | 已安装版本」，两者应一致才算成功。

## 二次开发（内网改源码 / 跑测试）

离线包内含**完整源码快照**（`source\`）与开发依赖 wheel（`offline_packages_win\` 里已含 pytest、setuptools 等）：

```bat
cd /d D:\mcp-dbtools\source
python -m venv .venv            :: 开发环境独立于生产的 ..\.venv，互不影响
.venv\Scripts\pip install --no-index --find-links=..\offline_packages_win -e .
.venv\Scripts\pip install --no-index --find-links=..\offline_packages_win -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

改动 `source\src\` 下的代码会立即生效（`-e` 可编辑安装）。用源码方式启动服务调试：

```bat
set MCP_DBTOOLS_DRIVERS_DIR=..\drivers
set MCP_DBTOOLS_CONFIG=%CD%\config\datasources.json
.venv\Scripts\python -m mcp_dbtools --transport streamable-http
```

完整说明见包内 `README-二次开发.md`（= `source\docs\二次开发指南.md`）。

## 服务管理（计划任务 / pm2）

安装完成后用 `mcp-dbtools-service.cmd` 管理服务（**先 cd 到安装目录**）：

```bat
mcp-dbtools-service.cmd install    :: 创建自启任务：管理员=开机自启(SYSTEM)；普通用户=登录自启
mcp-dbtools-service.cmd start      :: 启动
mcp-dbtools-service.cmd stop       :: 停止
mcp-dbtools-service.cmd restart    :: 重启
mcp-dbtools-service.cmd status     :: 任务状态 + 8000 端口监听情况
mcp-dbtools-service.cmd logs       :: 跟踪 logs\app.log（Ctrl+C 退出）
mcp-dbtools-service.cmd remove     :: 删除计划任务（不删安装目录与配置）
```

- 前台调试：直接运行 `mcp-dbtools-run.cmd`（会设置 `JAVA_HOME` 与工作目录后用 `python` 启动，日志输出到控制台）
- **pm2 兼容**：若目标机已装 pm2 且由 pm2 管理着 `mcp-dbtools`，上述命令会自动委托给 pm2，
  与原部署方式无缝共存（`pm2 logs mcp-dbtools` 等命令照旧可用）
- 无管理员权限时只能创建“登录自启”任务；开机自启需以管理员运行 `install`

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
├── install_offline.bat        # 一键离线安装（建 venv + 装依赖 + 生成配置 + 注册服务）
├── upgrade_offline.bat        # 离线升级（保留用户配置，重启服务）
├── mcp-dbtools-run.cmd        # 服务启动器（设 JAVA_HOME / 工作目录，用 pythonw 后台运行）
├── mcp-dbtools-service.cmd    # 服务管理（install/start/stop/restart/status/logs/remove）
├── requirements.txt
├── .env.example               # 环境变量示例（安装时生成 .env）
├── python-version-required.txt    # 要求的 Python 版本（安装/升级脚本据此校验）
├── python-3.13.x-amd64.exe    # CPython 官方安装器（轻量包附，供低版本机器离线安装）
├── runtime\                   # 【自包含包才有】便携运行时，解压即用免安装
│   ├── python\               #   便携 CPython（含 pip / venv）
│   └── jre\                  #   Temurin JRE 17（JDBC 需要）
├── README-windows.md          # 本说明
├── README-二次开发.md          # 内网二次开发指南
├── dist/                      # 项目 wheel
├── offline_packages_win/      # 依赖 wheel（运行时 + 开发依赖 pytest 等 + setuptools/wheel）
├── source/                    # 完整源码快照（src/ tests/ scripts/ deploy/ pyproject.toml 等，可二次开发）
├── ecosystem-windows.config.cjs   # pm2 配置（Windows 版）
├── config/
│   └── datasources.json.example   # 数据源配置示例（安装时生成 datasources.json）
├── scripts/
│   ├── encrypt_password.py    # 密码加密工具
│   └── sql/                   # SQL 脚本
├── drivers/                   # 放 JDBC 驱动 jar（如 db2jcc4.jar）
└── docs/
```

## 注意事项

- 离线包对应 **Python 3.13**：目标机 Python 版本必须一致（3.13.x 均可）
- DB2 驱动 `db2jcc4.jar` 受 IBM 许可需自行获取放入 `drivers\`
- 服务地址为 `http://<本机IP>:8000/mcp`，供 MCP 客户端（Claude/VS Code/dsh）连接

## 重新生成 Windows 离线包（联网机器上执行）

```bash
# 轻量包（要求目标机有 Python 3.13 + JDK/JRE 11+；含官方 Python 安装器）
bash scripts/build_offline.sh --platform windows

# 自包含包（目标机什么都不用装：包内带便携 Python + JRE 17）
bash scripts/build_offline.sh --platform windows --self-contained

# 不带 Python 安装器（轻量包减约 29 MB）
bash scripts/build_offline.sh --platform windows --no-python

# 或同时生成 Linux + Windows 两个包
bash scripts/build_offline.sh --platform all
```

> 换 Python 版本时用环境变量指定，如 `WIN_PYTHON_VER=3.12 bash scripts/build_offline.sh --platform windows`
> （此时包内 wheel 与 `python-version-required.txt` 都会变成 3.12）。
> 指定安装器补丁版本用 `WIN_PY_FULL_VER=3.13.15`；构建机无法访问 python.org 时安装器会自动跳过。
