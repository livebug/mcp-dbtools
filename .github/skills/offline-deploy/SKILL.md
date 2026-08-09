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

## 前提
- 构建机（外网）与内网机均为 **Linux x86_64**，Python **主版本一致**（jpype1/pydantic-core 是 manylinux 二进制 wheel）
- 内网机需已装 **JRE/JDK 8+**（JPype 需要）与 **Node.js/pm2**

## 流程

### 1. 外网构建机：生成离线包
```bash
bash scripts/build_offline.sh
# 产物: mcp-dbtools-offline-YYYYMMDD.tar.gz（项目 wheel + 依赖 wheels + JDBC 驱动 + 配置 + 脚本）
```

构建脚本会自动：
- `pip wheel .` 打项目 wheel（dist/）
- `pip download -r requirements.txt --only-binary=:all:` 下载全部依赖（含 cryptography 密码加密依赖）
- 组装：`dist/` + `wheels/` + `drivers/` + `config/` + `scripts/sql` + `scripts/encrypt_password.py` + pm2 配置 + `install.sh`

### 2. 拷贝到内网并解压安装
```bash
tar xzf mcp-dbtools-offline-YYYYMMDD.tar.gz -C /opt/mcp-dbtools
cd /opt/mcp-dbtools && bash deploy/install_offline.sh
# 或包内的: bash mcp-dbtools-offline/install.sh
```

install 脚本会：建 `.venv` → `pip install --no-index --find-links=wheels dist/*.whl` → 生成 `.env` → `pm2 start`。

### 3. pm2 运维
```bash
pm2 start deploy/ecosystem.config.cjs   # 启动
pm2 save                                 # 保存进程列表（配合 pm2 startup 开机自启）
pm2 logs mcp-dbtools                     # 日志
pm2 reload mcp-dbtools                   # 重启（改配置后）
pm2 stop mcp-dbtools                     # 停止
```

## 关键坑（务必遵守）
- **`deploy/ecosystem.config.cjs` 是 JS 文件，注释必须用 `//`**，不能有 `#`（node require 会语法报错）。
- 离线包内 `dist/` 必须放子目录，install 脚本引用 `dist/*.whl`。
- 新增运行时依赖时，需确保 `pip download` 能拉到该 wheel 进 `wheels/`，否则内网 `--no-index` 装不上。
- 密码加密依赖 `cryptography` 必须在 requirements.txt 中（已含），否则 `{ENC:...}` 配置无法解密。

## 输出
- 离线包路径：`<项目根>/mcp-dbtools-offline-YYYYMMDD.tar.gz`
- 内网安装与 pm2 状态
