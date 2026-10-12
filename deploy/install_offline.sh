#!/usr/bin/env bash
# =============================================================
# mcp-dbtools 内网离线安装脚本（在【内网服务器】上运行）
#
# 用法:
#   tar xzf mcp-dbtools-offline-YYYYMMDD.tar.gz
#   bash mcp-dbtools-offline/install.sh [安装目录，默认 /opt/mcp-dbtools]
#
# 依赖:
#   - JRE/JDK 17+（Java 桥 bridge.jar 需要）
#   - pm2（Node.js）
# =============================================================
set -euo pipefail

INSTALL_DIR="${1:-/opt/mcp-dbtools}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> 安装目录: $INSTALL_DIR"

# ---------- 前置检查 ----------
# Python 版本必须与离线包构建时的版本一致：依赖含 cp3xx 二进制 wheel，不跨版本
REQ_PY="$(cat "$HERE/python-version-required.txt" 2>/dev/null | tr -d '[:space:]')"
REQ_PY="${REQ_PY:-3.13}"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 python3。本离线包需要 Python ${REQ_PY}.x（64 位）"
  exit 1
fi
if ! python3 -c "import sys; raise SystemExit(0 if '.'.join(map(str,sys.version_info[:2]))=='${REQ_PY}' else 1)"; then
  echo "[ERROR] 当前 python3 版本为 $(python3 -V 2>&1 | awk '{print $2}')，本离线包需要 Python ${REQ_PY}.x"
  echo "        包内依赖 wheel 为 cp${REQ_PY/./} 平台标签，不能跨 Python 版本使用。"
  echo "        请安装 Python ${REQ_PY}（或改用与该 Python 版本匹配的离线包）后重新执行本脚本。"
  exit 1
fi
python3 -V

if ! command -v java >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 Java 运行时（JRE/JDK 17+），Java 桥 bridge.jar 需要。请先安装:"
  echo "  CentOS/RHEL : sudo yum install -y java-17-openjdk-headless"
  echo "  Ubuntu/Debian: sudo apt install -y openjdk-17-jre-headless"
  exit 1
fi
# Java 桥目标 Java 17（Java 8 的 java -version 输出 "1.8.0_xxx"，会被提取为 "1"）
JAVA_MAJOR="$(java -version 2>&1 | head -1 | sed -E 's/.*version "([0-9]+).*/\1/')"
if [ -n "$JAVA_MAJOR" ] && [ "$JAVA_MAJOR" -lt 17 ] 2>/dev/null; then
  echo "[ERROR] 检测到 Java $JAVA_MAJOR，Java 桥需要 JRE/JDK 17+。请升级:"
  echo "  CentOS/RHEL : sudo yum install -y java-17-openjdk-headless"
  echo "  Ubuntu/Debian: sudo apt install -y openjdk-17-jre-headless"
  exit 1
fi
if ! command -v pm2 >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 pm2。请先安装 Node.js 后: sudo npm i -g pm2"
  exit 1
fi
java -version 2>&1 | head -1

# ---------- 拷贝文件 ----------
echo "==> 拷贝离线包文件"
mkdir -p "$INSTALL_DIR"
cp -r "$HERE"/wheels "$HERE"/dist "$HERE"/drivers "$HERE"/config "$INSTALL_DIR/"
cp "$HERE"/bridge.jar "$INSTALL_DIR/" 2>/dev/null || echo "  [WARN] 离线包内缺少 bridge.jar（Java 桥），请用 MCP_DBTOOLS_BRIDGE_JAR 指向桥，或重新构建离线包"
mkdir -p "$INSTALL_DIR/scripts"
cp -r "$HERE"/scripts/sql "$INSTALL_DIR/scripts/" 2>/dev/null || true
cp "$HERE"/requirements.txt "$INSTALL_DIR/"
cp "$HERE"/.env.example "$INSTALL_DIR/"
cp "$HERE"/ecosystem.config.cjs "$INSTALL_DIR/"
# 源码快照与文档（内网二次开发用，见 README-二次开发.md）
cp -r "$HERE"/source "$INSTALL_DIR/" 2>/dev/null || echo "  [WARN] 离线包内缺少 source/（内网无法二次开发）"
cp -r "$HERE"/docs "$INSTALL_DIR/" 2>/dev/null || true
cp "$HERE"/README-二次开发.md "$INSTALL_DIR/" 2>/dev/null || true

# ---------- 创建虚拟环境并离线安装 ----------
cd "$INSTALL_DIR"
echo "==> 创建虚拟环境 (.venv)"
python3 -m venv .venv
echo "==> 离线安装依赖与项目（--no-index）"
./.venv/bin/pip install --no-index --find-links=wheels dist/*.whl

# ---------- 初始化配置 ----------
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp .env.example .env
  echo "==> 已生成 .env，请修改其中的密码/端口/鉴权 Token"
else
  echo "==> 已存在 .env，跳过"
fi
if [ ! -f "$INSTALL_DIR/config/datasources.json" ]; then
  cp "$INSTALL_DIR/config/datasources.json.example" "$INSTALL_DIR/config/datasources.json"
fi
mkdir -p logs

# ---------- 调整 pm2 配置中的路径 ----------
if [ "$INSTALL_DIR" != "/opt/mcp-dbtools" ]; then
  sed -i "s#/opt/mcp-dbtools#$INSTALL_DIR#g" ecosystem.config.cjs
fi

# ---------- 启动 ----------
echo "==> pm2 启动 mcp-dbtools"
pm2 start ecosystem.config.cjs || pm2 restart ecosystem.config.cjs
pm2 save

echo ""
echo "✅ 部署完成"
echo "   服务地址 : http://<内网IP>:8000/mcp"
echo "   健康检查 : curl http://127.0.0.1:8000/health"
echo "   查看日志 : pm2 logs mcp-dbtools"
echo "   修改配置 : $INSTALL_DIR/.env 与 $INSTALL_DIR/config/datasources.json 后 pm2 restart mcp-dbtools"
echo "   二次开发 : 源码在 $INSTALL_DIR/source，开发依赖 wheel 在 $INSTALL_DIR/wheels"
echo "              详见 $INSTALL_DIR/README-二次开发.md"
