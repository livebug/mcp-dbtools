#!/usr/bin/env bash
# =============================================================
# mcp-dbtools 内网离线安装脚本（在【内网服务器】上运行）
#
# 用法:
#   tar xzf mcp-dbtools-offline-YYYYMMDD.tar.gz
#   bash mcp-dbtools-offline/install.sh [安装目录，默认 /opt/mcp-dbtools]
#
# 依赖:
#   - JRE 8+（jaydebeapi/JPype 需要 JVM）
#   - pm2（Node.js）
# =============================================================
set -euo pipefail

INSTALL_DIR="${1:-/opt/mcp-dbtools}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> 安装目录: $INSTALL_DIR"

# ---------- 前置检查 ----------
if ! command -v java >/dev/null 2>&1; then
  echo "[ERROR] 未检测到 Java 运行时（JRE 8+），JPype/jaydebeapi 需要 JVM。请先安装:"
  echo "  CentOS/RHEL : sudo yum install -y java-1.8.0-openjdk-headless"
  echo "  Ubuntu/Debian: sudo apt install -y default-jre-headless"
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
