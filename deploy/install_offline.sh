#!/usr/bin/env bash
# =============================================================
# mcp-dbtools 内网离线安装脚本（在【内网服务器】上运行）
#
# 用法:
#   tar xzf mcp-dbtools-offline-YYYYMMDD.tar.gz
#   bash mcp-dbtools-offline/install.sh [安装目录，默认 /opt/mcp-dbtools]
#
# 依赖:
#   - JRE/JDK 11+（jaydebeapi/JPype 需要 JVM；JPype 1.5+ 不支持 Java 8）
#   - pm2（Node.js）
# =============================================================
set -euo pipefail

INSTALL_DIR="${1:-/opt/mcp-dbtools}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> 安装目录: $INSTALL_DIR"

# ---------- 前置检查 ----------
if ! command -v java >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 Java 运行时（JRE/JDK 11+），JPype/jaydebeapi 需要 JVM。请先安装:"
  echo "  CentOS/RHEL : sudo yum install -y java-17-openjdk-headless"
  echo "  Ubuntu/Debian: sudo apt install -y openjdk-17-jre-headless"
  exit 1
fi
# JPype 1.5+ 要求 Java 11+（Java 8 会报 JavaVersion 错误），安装前做版本校验
JAVA_MAJOR="$(java -version 2>&1 | head -1 | sed -E 's/.*version "([0-9]+).*/\1/')"
if [ "$JAVA_MAJOR" = "1" ]; then
  echo "[ERROR] 检测到 Java 8 及以下版本，JPype 1.5+ 需要 JDK 11+。请升级:"
  echo "  CentOS/RHEL : sudo yum install -y java-17-openjdk-headless"
  echo "  Ubuntu/Debian: sudo apt install -y openjdk-17-jre-headless"
  exit 1
fi
if [ -n "$JAVA_MAJOR" ] && [ "$JAVA_MAJOR" -lt 11 ] 2>/dev/null; then
  echo "[ERROR] 检测到 Java $JAVA_MAJOR，JPype 1.5+ 需要 JDK 11+（推荐 17/21）。请升级后重试"
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
mkdir -p "$INSTALL_DIR/scripts"
cp -r "$HERE"/scripts/sql "$INSTALL_DIR/scripts/" 2>/dev/null || true
cp "$HERE"/requirements.txt "$INSTALL_DIR/"
cp "$HERE"/.env.example "$INSTALL_DIR/"
cp "$HERE"/ecosystem.config.cjs "$INSTALL_DIR/"

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
