#!/usr/bin/env bash
# =============================================================
# 生成内网离线部署包（在【外网/可联网】构建机上运行）
#
# 用法:
#   bash scripts/build_offline.sh
#
# 产物:
#   mcp-dbtools-offline-YYYYMMDD.tar.gz
#     内含: 项目 wheel + 全部运行时依赖 wheel + JDBC 驱动 +
#           pm2 配置 + 内网安装脚本 install.sh
# =============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
PKG="offline/mcp-dbtools-offline"
STAMP="$(date +%Y%m%d)"
TARBALL="mcp-dbtools-offline-${STAMP}.tar.gz"

echo "==> 清理旧产物"
rm -rf dist wheels offline "$TARBALL"

echo "==> [1/4] 构建项目 wheel"
"$PY" -m pip wheel . --no-deps -w dist/

echo "==> [2/4] 下载运行时依赖 wheel（仅二进制，保证内网可用）"
"$PY" -m pip download -r requirements.txt -d wheels/ --only-binary=:all:

echo "==> [3/4] 组装离线包目录"
mkdir -p "$PKG/dist" "$PKG/wheels"
cp dist/*.whl "$PKG/dist/"
cp wheels/*.whl "$PKG/wheels/"
cp requirements.txt "$PKG/"
cp -r drivers "$PKG/drivers"
cp -r config "$PKG/config"
mkdir -p "$PKG/scripts"
cp -r scripts/sql "$PKG/scripts/sql"
cp scripts/encrypt_password.py "$PKG/scripts/encrypt_password.py"
cp deploy/ecosystem.config.cjs "$PKG/"
cp deploy/install_offline.sh "$PKG/install.sh"
cp .env.example "$PKG/.env.example"
cp -r docs "$PKG/docs"
chmod +x "$PKG/install.sh"

echo "==> [4/4] 打包"
tar czf "$TARBALL" -C offline mcp-dbtools-offline
echo ""
echo "✅ 生成离线包: $(pwd)/$TARBALL"
echo "   拷贝到内网服务器后执行: tar xzf $TARBALL && bash mcp-dbtools-offline/install.sh"
echo "   （构建机与内网机需同为 Linux x86_64 + 相同 Python 主版本）"
