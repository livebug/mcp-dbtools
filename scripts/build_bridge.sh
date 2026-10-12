#!/usr/bin/env bash
# =============================================================
# 构建 Java 桥 bridge.jar（数据面），放到本仓库根目录。
#
# 说明:
#   mcp-dbtools 的 JDBC 访问走 open-dbclient 的零依赖 Java 桥子进程
#   （bridge.jar），该桥的源码在 open-dbclient 仓库。本脚本在 open-dbclient
#   里用裸 javac + jar 构建，并把产物拷贝到本仓库根目录 bridge.jar。
#
# 前置:
#   - node（运行 open-dbclient 的构建脚本）
#   - JDK 17+（桥目标 Java 17）
#   - open-dbclient 仓库：默认与本仓库并排（../open-dbclient），
#     或用环境变量 OPEN_DBCLIENT_DIR 指定其路径
#
# 用法:
#   bash scripts/build_bridge.sh
#   OPEN_DBCLIENT_DIR=/path/to/open-dbclient bash scripts/build_bridge.sh
# =============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRIDGE_DIR="${OPEN_DBCLIENT_DIR:-$ROOT/../open-dbclient}"

if [ ! -f "$BRIDGE_DIR/scripts/build-bridge.mjs" ]; then
  echo "[ERROR] 找不到 open-dbclient 仓库: $BRIDGE_DIR" >&2
  echo "        请设置 OPEN_DBCLIENT_DIR=/path/to/open-dbclient，或把它放到 $ROOT/../open-dbclient" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "[ERROR] 未找到 node，构建桥需要 node 运行 build-bridge.mjs" >&2
  exit 1
fi

echo "==> 在 $BRIDGE_DIR 构建 bridge.jar（目标 Java 17）"
( cd "$BRIDGE_DIR" && node scripts/build-bridge.mjs )

cp "$BRIDGE_DIR/resources/bridge.jar" "$ROOT/bridge.jar"
echo "==> bridge.jar -> $ROOT/bridge.jar ($(du -h "$ROOT/bridge.jar" | cut -f1))"
echo "    部署时通过环境变量 MCP_DBTOOLS_BRIDGE_JAR 指向它（默认即项目根 bridge.jar）"
