---
name: offline-deploy
description: '构建 mcp-dbtools 内网离线部署包并在内网服务器安装。Use when: 离线部署、打包离线包、build_offline.sh、install_offline.sh、pm2 部署、内网安装、生成离线包、无法联网的服务器。'
argument-hint: '外网构建离线包并部署到内网'
user-invocable: true
---

# 内网离线部署（pm2）

将 mcp-dbtools 打包成离线 tar.gz，拷贝到无法访问外网的内网服务器安装，用 pm2 托管。

## 何时使用
- 内网服务器无法访问外网、无法使用 Docker 时
- 需要生成可分发/可存档的部署包时

> 常规发布**不用手工构建**：推送 `v*` tag 后 `.github/workflows/release.yml` 会自动跑测试、
> 校验版本号、构建 Linux/Windows 两个离线包并发布到 GitHub Release。
> 下面流程用于本地验证、紧急构建或 CI 不可用时。

## 前提
- 构建机（外网）与内网机均为 **Linux x86_64**（或 Windows 目标机），Python **版本必须与包内
  `python-version-required.txt` 一致**（pydantic-core 等是 cp3xx 二进制 wheel，不跨版本）
- **Python 3.8/3.9 不可用**：`mcp` SDK 全版本要求 `>=3.10`（`pip download` 直接报
  `No matching distribution found for mcp`）。Windows 包会附带 CPython 官方安装器供低版本机并行安装
- 内网机需已装 **JRE/JDK 17+**（运行 Java 桥）与 **Node.js/pm2**

## 流程

### 1. 外网构建机：生成离线包
```bash
bash scripts/build_offline.sh --platform linux     # 或 windows / all
bash scripts/build_offline.sh --platform windows --no-python     # 不附带 Python 安装器（省 ~29MB）
bash scripts/build_offline.sh --platform windows --self-contained # 裸机：包内带便携 Python + JRE 17
# 产物: mcp-dbtools-offline-linux-YYYYMMDD.tar.gz / mcp-dbtools-offline-windows-YYYYMMDD.zip
```

构建脚本会自动：
- `pip wheel .` 打项目 wheel（dist/）
- `pip download -r requirements.txt` + `-r requirements-dev.txt` + `setuptools wheel`（`--only-binary`，锁定平台标签）
  → 运行时依赖 + **开发依赖**（pytest 等）+ **构建后端**（内网可离线 `pip install -e .`）
- `git archive HEAD` → **完整源码快照 `source/`**（供内网二次开发；不含 `.git`，构建时有未提交改动会 `[WARN]`）
- 写 `python-version-required.txt`（目标机 Python 版本要求，安装/升级脚本据此校验并快速失败）
- Windows 包（默认 `--with-python`）下载 **CPython 官方安装器**放进包内，供 `Python <3.10` 的内网机
  离线并行安装（`WIN_PY_FULL_VER=3.13.x` 可指定补丁版本；构建机连不上 python.org 时自动跳过）
- **`--self-contained`（Windows 裸机）**：把便携 CPython（python-build-standalone 的
  `install_only_stripped`，**必须 stripped 版**，体积 20.9MB vs 44.9MB）+ Temurin JRE 17
  **解压展开**到 `runtime/python`、`runtime/jre`（目标机无需解压工具），并写
  `runtime/runtime-manifest.txt` 记录版本来源；此时自动跳过官方安装器避免重复。
  产物名带 `-selfcontained`，**不要和轻量包同名**（早期版本会互相覆盖）
- Windows 包还带 `mcp-dbtools-run.cmd`（启动器）+ `mcp-dbtools-service.cmd`（服务管理，
  pm2 优先、否则计划任务）——**批处理必须 CRLF**（见 .gitattributes）
- **便携 Python 下载地址解析失败会直接报错退出**（不静默产出残缺包）：解析顺序为
  显式 `PBS_RELEASE_TAG` + `PORTABLE_PY_FULL_VER`（不走 API，可复现）→ 已登录 `gh` CLI →
  带 `GITHUB_TOKEN` 的 API。**匿名 api.github.com 限流 60 次/小时，连续构建会 403**；
  CI 已在构建步骤传入 `secrets.GITHUB_TOKEN`
- 组装：`dist/` + `wheels/` + `drivers/` + `config/` + `scripts/sql` + `scripts/encrypt_password.py`
  + `docs/` + `README-二次开发.md` + pm2 配置 + `install.sh` / `upgrade.sh`

### 2. 拷贝到内网并解压安装
```bash
tar xzf mcp-dbtools-offline-linux-YYYYMMDD.tar.gz
bash mcp-dbtools-offline-linux/install.sh /opt/mcp-dbtools
```

install 脚本会：建 `.venv` → `pip install --no-index --find-links=wheels dist/*.whl` → 生成 `.env` → `pm2 start`，
并把 `source/`、`docs/`、`README-二次开发.md` 复制到安装目录。

### 3. 内网二次开发（改源码 / 跑测试）
```bash
cd /opt/mcp-dbtools/source
python3 -m venv .venv
.venv/bin/pip install --no-index --find-links=../wheels -e .        # -e：改代码立即生效
.venv/bin/pip install --no-index --find-links=../wheels -r requirements-dev.txt
.venv/bin/python -m pytest -q
```
详见 `docs/二次开发指南.md`（包内 `README-二次开发.md`）。

### 4. pm2 运维

```bash
pm2 start deploy/ecosystem.config.cjs   # 启动
pm2 save                                 # 保存进程列表（配合 pm2 startup 开机自启）
pm2 logs mcp-dbtools                     # 日志
pm2 reload mcp-dbtools                   # 重启（改配置后）
pm2 stop mcp-dbtools                     # 停止
```

## 关键坑（务必遵守）
- **Python 版本必须与包一致，且 3.8/3.9 完全不可用**：`mcp` SDK 全版本要求 `>=3.10`，
  `pip download --python-version 3.8` 直接失败；包内 wheel 是 cp3xx 标签，装到别的版本会
  报 `No matching distribution found`。构建/安装脚本用 `python-version-required.txt` 做前置校验。
- **`deploy/ecosystem.config.cjs` 是 JS 文件，注释必须用 `//`**，不能有 `#`（node require 会语法报错）。
- 离线包内 `dist/` 必须放子目录，install 脚本引用 `dist/*.whl`。
- **同版本 wheel 会被 pip 静默跳过**：内网已安装版本与新包版本号相同时
  （例如都是 `1.1.0`），`pip install --upgrade dist/mcp_dbtools-1.1.0-*.whl`
  只提示 `already installed with the same version as the provided wheel`，**代码不会更新但脚本仍报"升级完成"**。
  升级脚本已用 `--force-reinstall --no-deps` 修掉（依赖另用 `-r requirements.txt` 升级）；
  同时**打包前应 bump 版本号**，否则内网无法用 `__version__` 判断新旧。
- **Windows 升级前必须 `pm2 stop mcp-dbtools`**：运行中的进程锁定 venv 内编译产物 `.pyd`，pip 覆盖会失败。
- Windows 升级脚本**不拷贝 `ecosystem-windows.config.cjs`**；改其 `env` 后需 `pm2 delete` + `pm2 start`
  （`pm2 restart --update-env` 不会重读 cjs）。
- 升级脚本只更新 `*.example`，**不会合并用户的 `.env` 与 `config/datasources.json`**，新增配置项要手工补。
- 新增运行时依赖时，需确保 `pip download` 能拉到该 wheel 进 `wheels/`，否则内网 `--no-index` 装不上。
- 密码加密依赖 `cryptography` 必须在 requirements.txt 中（已含），否则 `{ENC:...}` 配置无法解密。
- 源码快照来自 `git archive HEAD`，**未提交改动不会进包**（构建会 `[WARN]`）→ 打包前先提交。

## 输出
- 离线包路径：`<项目根>/mcp-dbtools-offline-linux-YYYYMMDD.tar.gz` / `...-windows-YYYYMMDD.zip`
- 内网安装与 pm2 状态；二次开发环境（`source/` + `-e` 安装）是否可用
