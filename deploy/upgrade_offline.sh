#!/usr/bin/env bash
# =============================================================
# mcp-dbtools 离线升级脚本（在已安装旧版本的内网服务器上运行）
#
# 用法:
#   tar xzf mcp-dbtools-offline-linux-YYYYMMDD.tar.gz
#   bash mcp-dbtools-offline-linux/upgrade.sh [安装目录，默认 /opt/mcp-dbtools]
#
# 功能:
#   - 备份用户配置（.env、config/datasources.json）到 backup-时间戳/
#   - 更新代码与依赖（dist/、wheels/、drivers/ 增量合并、scripts/、配置示例）
#   - 保留用户文件：.env、config/datasources.json、自定义 JDBC 驱动 jar、logs
#   - 离线升级虚拟环境依赖（--no-index --upgrade）
#   - 自动重启服务（优先 pm2，其次 systemd）
#
# 依赖:
#   - 旧版本已按 install.sh 部署在目标目录
#   - JRE/JDK 11+、pm2 或 systemd
# =============================================================
set -euo pipefail

INSTALL_DIR="${1:-/opt/mcp-dbtools}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> 升级目标目录: $INSTALL_DIR"

# ---------- 前置检查 ----------
if [ ! -f "$INSTALL_DIR/.venv/bin/python" ]; then
  echo "[ERROR] 未找到 $INSTALL_DIR/.venv/bin/python，请确认旧版本已安装（先跑 install.sh）"
  exit 1
fi
if [ ! -f "$HERE/dist/"*.whl ]; then
  echo "[ERROR] 离线包缺少项目 wheel（dist/），请确认解压完整"
  exit 1
fi

# ---------- 1. 备份用户配置 ----------
BACKUP="$INSTALL_DIR/backup-$(date +%Y%m%d%H%M%S)"
echo "==> 备份用户配置 -> $BACKUP"
mkdir -p "$BACKUP/config"
cp "$INSTALL_DIR/.env" "$BACKUP/" 2>/dev/null || echo "  (无 .env，跳过)"
cp "$INSTALL_DIR/config/datasources.json" "$BACKUP/config/" 2>/dev/null || echo "  (无 datasources.json，跳过)"

# ---------- 2. 更新代码与依赖（保留用户文件） ----------
echo "==> 更新代码与依赖"
# 项目 wheel + 依赖 wheels（整体覆盖，新版本以新包为准）
rm -rf "$INSTALL_DIR/dist"
cp -r "$HERE/dist" "$INSTALL_DIR/dist"
rm -rf "$INSTALL_DIR/wheels"
cp -r "$HERE/wheels" "$INSTALL_DIR/wheels"
# JDBC 驱动增量合并：保留用户已有 jar，补充新包内的 jar
mkdir -p "$INSTALL_DIR/drivers"
cp -n "$HERE"/drivers/*.jar "$INSTALL_DIR/drivers/" 2>/dev/null || true
# 脚本目录（sql 脚本 + 密码工具）
mkdir -p "$INSTALL_DIR/scripts"
cp -r "$HERE"/scripts/sql/. "$INSTALL_DIR/scripts/sql/" 2>/dev/null || true
cp -n "$HERE"/scripts/encrypt_password.py "$INSTALL_DIR/scripts/" 2>/dev/null || true
# 配置文件示例（不覆盖用户已存在的 datasources.json）
cp "$HERE/requirements.txt" "$INSTALL_DIR/requirements.txt"
cp "$HERE/.env.example" "$INSTALL_DIR/.env.example"
mkdir -p "$INSTALL_DIR/config"
cp "$HERE/config/datasources.json.example" "$INSTALL_DIR/config/datasources.json.example"
# pm2 配置（若是自定义安装目录，替换路径）
cp "$HERE/ecosystem.config.cjs" "$INSTALL_DIR/ecosystem.config.cjs"
sed -i "s#/opt/mcp-dbtools#$INSTALL_DIR#g" "$INSTALL_DIR/ecosystem.config.cjs"

# ---------- 3. 离线升级虚拟环境依赖 ----------
echo "==> 离线升级依赖（--no-index）"
"$INSTALL_DIR/.venv/bin/pip" install --no-index \
    --find-links="$INSTALL_DIR/wheels" \
    --upgrade "$INSTALL_DIR/dist/"*.whl

# ---------- 4. 重启服务 ----------
echo "==> 重启服务"
if command -v pm2 >/dev/null 2>&1 && pm2 describe mcp-dbtools >/dev/null 2>&1; then
  pm2 restart mcp-dbtools --update-env || pm2 start "$INSTALL_DIR/ecosystem.config.cjs"
  pm2 save || true
  echo "  已通过 pm2 重启"
elif command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q mcp-dbtools; then
  systemctl restart mcp-dbtools
  echo "  已通过 systemd 重启"
else
  echo "  [提示] 未检测到 pm2/systemd 服务，请手动重启: $INSTALL_DIR/.venv/bin/python -m mcp_dbtools --transport streamable-http"
fi

echo ""
echo "✅ 升级完成"
echo "   版本: 请运行 $INSTALL_DIR/.venv/bin/python -c 'import mcp_dbtools; print(mcp_dbtools.__version__)' 确认"
echo "   备份: $BACKUP（确认无误后可删除）"
echo "   健康检查: curl http://127.0.0.1:8000/health"
