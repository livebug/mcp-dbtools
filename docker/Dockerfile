# =============================================================
# mcp-dbtools 镜像构建
# 运行环境: Python + OpenJDK（JDBC 走 Java 桥 bridge.jar，需 JRE 17+）
# =============================================================
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MCP_DBTOOLS_BRIDGE_JAR=/app/bridge.jar

# JRE 用于运行 Java 桥 bridge.jar（数据面，由 MCP 服务 spawn）
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先安装依赖，利用 Docker 层缓存
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Java 桥（构建前先 bash scripts/build_bridge.sh 生成项目根 bridge.jar）
COPY bridge.jar ./

# 配置、驱动、脚本
COPY config ./config
COPY drivers ./drivers
COPY scripts ./scripts
# 镜像内尝试下载 openGauss 驱动（网络受限时可在运行时挂载 drivers 卷覆盖）
RUN python scripts/download_drivers.py --skip-gauss || true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fs http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["python", "-m", "mcp_dbtools"]
CMD ["--transport", "streamable-http"]
