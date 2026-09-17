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
#   项目 wheel + 运行时依赖 wheel + 开发依赖 wheel（pytest/setuptools 等）+
#   **完整源码快照 source/**（供内网二次开发）+ JDBC 驱动 + 配置示例 +
#   安装脚本(install.sh / install_offline.bat) + 升级脚本(upgrade.sh / upgrade_offline.bat) +
#   二次开发指南(README-二次开发.md)
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
  # 二次开发指南（同时放在包根方便内网直接看到）
  cp docs/二次开发指南.md "$PKG/README-二次开发.md"

  # ---------- 完整源码快照（供内网二次开发） ----------
  echo "==> 组装源码快照 source/（内网二次开发用）"
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "    [WARN] 当前目录不是 git 仓库，跳过 source/ 快照（内网将无法二次开发）"
  else
    if [ -n "$(git status --porcelain)" ]; then
      echo "    [WARN] 工作区存在未提交改动！source/ 快照只包含已提交内容（git HEAD）"
    fi
    rm -rf "$PKG/source"
    mkdir -p "$PKG/source"
    git archive HEAD | tar x -C "$PKG/source"
    # 运行期产物不是源码，剔除
    rm -rf "$PKG/source/exports"
    echo "    source/ 文件数: $(find "$PKG/source" -type f | wc -l)"
  fi
}

# 下载离线 wheel：运行时依赖 + 开发依赖 + 构建后端（setuptools/wheel）
# 用途：内网 `pip install --no-index -e .` / `pytest` 都能离线完成
download_wheels() {
  local DEST="$1" PLATFORM_TAG="$2" PYVER="$3" ABI="$4"
  local -a plat_args=(--platform "$PLATFORM_TAG" --python-version "$PYVER")
  if [ -n "$ABI" ]; then
    plat_args+=(--implementation cp --abi "$ABI")
  fi
  mkdir -p "$DEST"
  "$PY" -m pip download -r requirements.txt -d "$DEST" --only-binary=:all: "${plat_args[@]}"
  "$PY" -m pip download -r requirements-dev.txt -d "$DEST" --only-binary=:all: "${plat_args[@]}"
  "$PY" -m pip download setuptools wheel -d "$DEST" --only-binary=:all: "${plat_args[@]}"
}

# ---------- Linux 包 ----------
build_linux() {
  local PKG="offline/mcp-dbtools-offline-linux"
  local TARBALL="mcp-dbtools-offline-linux-${STAMP}.tar.gz"
  echo "==> [2] 下载 Linux 依赖 wheel（manylinux2014_${ARCH}）"
  rm -rf wheels_linux "$PKG"
  # 锁定 manylinux2014（glibc 2.17+）平台标签，避免拉到 manylinux_2_28/2_34 等
  # 过新 wheel（如新版 cryptography），导致 CentOS 7/8 等内网机无法安装
  # 下载内容：运行时依赖 + 开发依赖 + 构建后端（内网可离线 pip install -e . 与 pytest）
  download_wheels wheels_linux/ "manylinux2014_${ARCH}" "$PY_VER" ""

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
  # 运行时 + 开发依赖 + 构建后端（内网可离线 pip install -e . 与 pytest）
  download_wheels wheels_windows/ win_amd64 "$WIN_PY_VER" "cp${WIN_PY_VER/./}"

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
echo "  二次开发: 源码在包内 source/，见 README-二次开发.md（内网可 pip install -e .）"
echo "  （构建机与目标机需同为对应平台 + 相同 Python 主版本）"
