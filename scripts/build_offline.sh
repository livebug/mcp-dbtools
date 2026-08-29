#!/usr/bin/env bash
# =============================================================
# 生成内网离线部署包（在【外网/可联网】构建机上运行）
#
# 用法:
#   bash scripts/build_offline.sh [--platform linux|windows|all]
#     --platform linux    生成 Linux 离线包（默认，tar.gz，manylinux wheel）
#     --platform windows  生成 Windows 离线包（zip，win_amd64 wheel）
#     --platform all      同时生成 Linux + Windows 两个包
#
# 产物（项目根目录下）:
#   mcp-dbtools-offline-linux-YYYYMMDD.tar.gz
#   mcp-dbtools-offline-windows-YYYYMMDD.zip
#
# 离线包内容:
#   项目 wheel + 运行时依赖 wheel + JDBC 驱动 + 配置示例 +
#   安装脚本(install.sh / install_offline.bat) + 升级脚本(upgrade.sh / upgrade_offline.bat)
# =============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
STAMP="$(date +%Y%m%d)"
PY_VER="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
ARCH="$(uname -m)"

# 兼容旧用法: build_offline.sh [linux|windows|all]
PLATFORM="${1:-linux}"
if [ "$PLATFORM" = "--platform" ]; then
  PLATFORM="${2:-all}"
fi
case "$PLATFORM" in
  linux|windows|all) ;;
  *) echo "用法: $0 [--platform linux|windows|all]"; exit 1 ;;
esac

echo "==> 构建平台: $PLATFORM | Python $PY_VER | 架构 $ARCH"

# ---------- [1] 构建项目 wheel（纯 Python，两平台通用） ----------
echo "==> [1/4] 构建项目 wheel"
rm -rf dist
"$PY" -m pip wheel . --no-deps -w dist/
echo "    wheel: $(ls dist/*.whl | head -1)"

# 通用文件组装（Linux/Windows 包都要）
assemble_common() {
  local PKG="$1"
  mkdir -p "$PKG/dist" "$PKG/drivers" "$PKG/config" "$PKG/scripts/sql" "$PKG/docs"
  cp dist/*.whl "$PKG/dist/"
  cp requirements.txt "$PKG/"
  cp .env.example "$PKG/.env.example"
  cp config/datasources.json.example "$PKG/config/datasources.json.example"
  cp -n drivers/*.jar "$PKG/drivers/" 2>/dev/null || true
  cp -r scripts/sql/. "$PKG/scripts/sql/" 2>/dev/null || true
  cp scripts/encrypt_password.py "$PKG/scripts/encrypt_password.py"
  cp -r docs/. "$PKG/docs/" 2>/dev/null || true
}

# ---------- Linux 包 ----------
build_linux() {
  local PKG="offline/mcp-dbtools-offline-linux"
  local TARBALL="mcp-dbtools-offline-linux-${STAMP}.tar.gz"
  echo "==> [2] 下载 Linux 依赖 wheel（manylinux2014_${ARCH}）"
  rm -rf wheels_linux "$PKG"
  # 锁定 manylinux2014（glibc 2.17+）平台标签，避免拉到 manylinux_2_28/2_34 等
  # 过新 wheel（如新版 cryptography），导致 CentOS 7/8 等内网机无法安装
  "$PY" -m pip download -r requirements.txt -d wheels_linux/ --only-binary=:all: \
      --platform "manylinux2014_${ARCH}" --python-version "$PY_VER"

  echo "==> [3] 组装 Linux 离线包目录"
  assemble_common "$PKG"
  mkdir -p "$PKG/wheels"
  cp wheels_linux/*.whl "$PKG/wheels/"
  cp deploy/ecosystem.config.cjs "$PKG/ecosystem.config.cjs"
  cp deploy/install_offline.sh "$PKG/install.sh"
  cp deploy/upgrade_offline.sh "$PKG/upgrade.sh"
  chmod +x "$PKG/install.sh" "$PKG/upgrade.sh"

  echo "==> [4] 打包"
  tar czf "$TARBALL" -C offline mcp-dbtools-offline-linux
  echo "✅ Linux 离线包: $(pwd)/$TARBALL"
}

# ---------- Windows 包 ----------
build_windows() {
  local PKG="offline/mcp-dbtools-offline-windows"
  local ZIP="mcp-dbtools-offline-windows-${STAMP}.zip"
  # Windows 目标平台: win_amd64 + CPython 3.13（与 README-windows.md 一致）
  local WIN_PY_VER="${WIN_PYTHON_VER:-3.13}"
  echo "==> [2] 下载 Windows 依赖 wheel（win_amd64, Python $WIN_PY_VER）"
  rm -rf wheels_windows "$PKG"
  "$PY" -m pip download -r requirements.txt -d wheels_windows/ --only-binary=:all: \
      --platform win_amd64 \
      --python-version "$WIN_PY_VER" --implementation cp --abi "cp${WIN_PY_VER/./}"

  echo "==> [3] 组装 Windows 离线包目录"
  assemble_common "$PKG"
  mkdir -p "$PKG/offline_packages_win"
  cp wheels_windows/*.whl "$PKG/offline_packages_win/"
  cp deploy/windows/ecosystem-windows.config.cjs "$PKG/ecosystem-windows.config.cjs"
  cp deploy/windows/install_offline.bat "$PKG/install_offline.bat"
  cp deploy/windows/upgrade_offline.bat "$PKG/upgrade_offline.bat"
  cp deploy/windows/README-windows.md "$PKG/README-windows.md"

  echo "==> [4] 打包 zip"
  "$PY" - "$ZIP" "$PKG" <<'PYEOF'
import shutil, sys
out, src = sys.argv[1], sys.argv[2]
shutil.make_archive(out[:-4], "zip", root_dir=src, base_dir=".")
print(f"    -> {out}")
PYEOF
  echo "✅ Windows 离线包: $(pwd)/$ZIP"
}

case "$PLATFORM" in
  linux)   build_linux ;;
  windows) build_windows ;;
  all)     build_linux; build_windows ;;
esac

echo ""
echo "完成。离线包已生成，拷贝到内网后解压执行:"
echo "  Linux  : tar xzf mcp-dbtools-offline-linux-${STAMP}.tar.gz && bash mcp-dbtools-offline-linux/install.sh"
echo "  Windows: 解压 mcp-dbtools-offline-windows-${STAMP}.zip 后双击 install_offline.bat"
echo "  （构建机与目标机需同为对应平台 + 相同 Python 主版本）"
