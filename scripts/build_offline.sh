#!/usr/bin/env bash
# =============================================================
# 生成内网离线部署包（在【外网/可联网】构建机上运行）
#
# 用法:
#   bash scripts/build_offline.sh [--platform linux|windows|all] [--with-python|--no-python]
#     --platform linux    生成 Linux 离线包（默认，tar.gz，manylinux wheel）
#     --platform windows  生成 Windows 离线包（zip，win_amd64 wheel）
#     --platform all      同时生成 Linux + Windows 两个包
#     --with-python       Windows 包内附带 CPython 官方安装器（默认开启）
#                         内网存在低于目标版本的 Python（如系统自带 3.8）时，
#                         可在内网离线安装 3.13（并行安装，不影响系统 Python）
#     --no-python         不附带 Python 安装器（包体积减小约 29 MB）
#     --self-contained    【裸机模式】包内直接展开便携运行时（Python + JRE 17），
#                         目标机无需预装 Python / JDK / Node（pm2 也不再必需），
#                         解压即用、免管理员、不写 PATH/注册表。
#                         包体积约 74 MB（自动跳过官方安装器，避免重复）
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
PLATFORM="linux"
WITH_PYTHON="${WITH_PYTHON_INSTALLER:-1}"
SELF_CONTAINED="${SELF_CONTAINED:-0}"
while [ $# -gt 0 ]; do
  case "$1" in
    --platform) PLATFORM="${2:-}"; shift 2 ;;
    linux|windows|all) PLATFORM="$1"; shift ;;
    --with-python) WITH_PYTHON=1; shift ;;
    --no-python)   WITH_PYTHON=0; shift ;;
    --self-contained) SELF_CONTAINED=1; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "未知参数: $1"; echo "用法: $0 [--platform linux|windows|all] [--with-python|--no-python] [--self-contained]"; exit 1 ;;
  esac
done
case "$PLATFORM" in
  linux|windows|all) ;;
  *) echo "用法: $0 [--platform linux|windows|all]"; exit 1 ;;
esac
if [ "$SELF_CONTAINED" = "1" ] && [ "$PLATFORM" = "linux" ]; then
  echo "[WARN] --self-contained 目前仅支持 Windows 包，已忽略该选项"
  SELF_CONTAINED=0
fi

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

# 下载 Windows 版 CPython 官方安装器放进离线包（供内网低版本机器离线安装）
# 依赖构建机能访问 python.org；失败仅告警不中断构建
fetch_python_installer() {
  local PKG="$1" PYVER="$2"
  local FULLVER="${WIN_PY_FULL_VER:-}"
  if [ -z "$FULLVER" ]; then
    FULLVER="$(curl -s --max-time 20 https://www.python.org/ftp/python/ \
        | grep -oE "${PYVER//./\\.}\.[0-9]+" | sort -V -u | tail -1 || true)"
  fi
  if [ -z "$FULLVER" ]; then
    echo "    [WARN] 无法确定 Python ${PYVER} 最新补丁号，跳过安装器"
    echo "           （可显式指定: WIN_PY_FULL_VER=${PYVER}.x bash $0 --platform windows）"
    return 0
  fi
  local URL="https://www.python.org/ftp/python/${FULLVER}/python-${FULLVER}-amd64.exe"
  local OUT="$PKG/python-${FULLVER}-amd64.exe"
  echo "==> 下载 CPython 安装器（内网 Python 版本过低的机器用）"
  if curl -fsSL --max-time 600 -o "$OUT" "$URL"; then
    echo "    python-${FULLVER}-amd64.exe $(du -h "$OUT" | cut -f1)"
  else
    echo "    [WARN] 下载失败（构建机能否访问 python.org?），跳过安装器: $URL"
    rm -f "$OUT"
  fi
}

# 下载并【展开】Windows 自包含运行时：便携 CPython + Temurin JRE
# 直接展开为 runtime/python、runtime/jre，目标机无需解压工具、无需安装、免管理员
fetch_runtime_windows() {
  local PKG="$1" PYVER="$2"
  local RT="$PKG/runtime"
  rm -rf "$RT"; mkdir -p "$RT"
  local MANIFEST="$RT/runtime-manifest.txt"
  {
    echo "mcp-dbtools 自包含运行时清单（随包分发，供内网裸机使用）"
    echo "生成时间: $(date -Iseconds)"
  } > "$MANIFEST"

  # ---------- 1) 便携 CPython（install_only_stripped：解压即用、含 pip/venv、自带 vcruntime） ----------
  echo "==> 下载便携 CPython ${PYVER}（Windows x64，stripped）"
  local PY_URL
  PY_URL="$("$PY" - "$PYVER" <<'PYEOF'
import json, re, sys, urllib.request

ver = sys.argv[1]
api = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
req = urllib.request.Request(api, headers={"User-Agent": "mcp-dbtools-build"})
with urllib.request.urlopen(req, timeout=60) as resp:
    data = json.load(resp)
pat = re.compile(
    rf"cpython-{re.escape(ver)}\.\d+\+.*-x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$"
)
for asset in data.get("assets", []):
    if pat.search(asset["name"]):
        print(asset["browser_download_url"])
        break
PYEOF
)"
  if [ -z "$PY_URL" ]; then
    echo "    [WARN] 未找到便携 CPython ${PYVER}，跳过（自包含包将不完整）"
  elif curl -fsSL --max-time 900 -o "$RT/python.tar.gz" "$PY_URL"; then
    mkdir -p "$RT/python"
    tar xzf "$RT/python.tar.gz" -C "$RT/python" --strip-components=1
    rm -f "$RT/python.tar.gz"
    if [ -x "$RT/python/python.exe" ] || [ -f "$RT/python/python.exe" ]; then
      echo "    便携 Python: $(du -sm "$RT/python" | cut -f1) MB（含 python.exe / pythonw.exe / pip / venv）"
      echo "portable-python: $(basename "$PY_URL")" >> "$MANIFEST"
    else
      echo "    [WARN] 便携 Python 解压后缺少 python.exe，已清理"
      rm -rf "$RT/python"
    fi
  else
    echo "    [WARN] 便携 CPython 下载失败: $PY_URL"
    rm -rf "$RT/python.tar.gz"
  fi

  # ---------- 2) Temurin JRE（JDBC 需要 JVM；解压即用，设 JAVA_HOME 即可） ----------
  echo "==> 下载 Eclipse Temurin JRE ${JRE_VERSION:-17}（Windows x64）"
  local JRE_URL="https://api.adoptium.net/v3/binary/latest/${JRE_VERSION:-17}/ga/windows/x64/jre/hotspot/normal/eclipse?project=jdk"
  if curl -fsSL --max-time 900 -o "$RT/jre.zip" "$JRE_URL"; then
    "$PY" -m zipfile -e "$RT/jre.zip" "$RT/jre-tmp"
    local JRE_TOP
    JRE_TOP="$(find "$RT/jre-tmp" -maxdepth 1 -mindepth 1 -type d | head -1)"
    if [ -n "$JRE_TOP" ] && [ -f "$JRE_TOP/bin/server/jvm.dll" ]; then
      mv "$JRE_TOP" "$RT/jre"
      rm -rf "$RT/jre-tmp" "$RT/jre.zip"
      echo "    JRE: $(du -sm "$RT/jre" | cut -f1) MB（$(basename "$JRE_TOP")）"
      echo "temurin-jre: $(basename "$JRE_TOP")" >> "$MANIFEST"
    else
      echo "    [WARN] JRE 解压结果异常（缺 bin/server/jvm.dll），已清理"
      rm -rf "$RT/jre-tmp" "$RT/jre.zip"
    fi
  else
    echo "    [WARN] JRE 下载失败，跳过（目标机需自行准备 JDK/JRE 11+）"
    rm -f "$RT/jre.zip"
  fi

  echo "    运行时清单: $MANIFEST"
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
  # 目标机 Python 版本要求（install 脚本据此校验，版本不符直接报错而非安装失败）
  echo "$PY_VER" > "$PKG/python-version-required.txt"
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
  cp deploy/windows/mcp-dbtools-run.cmd "$PKG/mcp-dbtools-run.cmd"
  cp deploy/windows/mcp-dbtools-service.cmd "$PKG/mcp-dbtools-service.cmd"
  cp deploy/windows/README-windows.md "$PKG/README-windows.md"
  # 目标机 Python 版本要求（install/upgrade 脚本据此校验）
  echo "$WIN_PY_VER" > "$PKG/python-version-required.txt"
  rm -f "$PKG"/python-*-amd64.exe
  if [ "$SELF_CONTAINED" = "1" ]; then
    echo "    [自包含模式] 携带便携运行时（Python + JRE），目标机无需任何预装"
    fetch_runtime_windows "$PKG" "$WIN_PY_VER"
  elif [ "$WITH_PYTHON" = "1" ]; then
    fetch_python_installer "$PKG" "$WIN_PY_VER"
  else
    echo "    [跳过] 未附带 CPython 安装器（--with-python 可开启）"
  fi

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
if [ "$SELF_CONTAINED" = "1" ]; then
  echo "  自包含包: 目标机无需 Python/JDK/Node，解压即用（运行 runtime/ 内便携 Python 与 JRE）"
fi
echo "  （构建机与目标机需同为对应平台 + 相同 Python 主版本）"
