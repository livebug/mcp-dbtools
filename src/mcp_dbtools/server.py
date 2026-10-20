"""MCP 服务命令行入口。

职责只剩"解析参数 + 初始化日志 + 选择传输方式"，应用装配见 ``app.py``。

支持三种传输方式（由 ``MCP_DBTOOLS_TRANSPORT`` 控制）：
- streamable-http（默认）：HTTP 对外提供 MCP + REST API + Web 控制台；
- sse           ：SSE 传输（兼容旧客户端）；
- stdio         ：本地开发/测试（无 HTTP，也就没有控制台）。

HTTP 模式下的 URL 一览：``/mcp``（MCP）、``/api/v1/*``（REST）、``/ui/``（控制台）、
``/health``、``/metrics``。
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from .app import build_app, run_http, run_stdio  # noqa: F401  build_app 供外部嵌入与测试使用
from .config import ConfigError, load_settings

logger = logging.getLogger(__name__)


def _setup_logging(settings) -> None:
    """配置日志：输出到控制台，并可选落盘（按大小轮转，便于服务器上查看）。"""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        try:
            Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                settings.log_file,
                maxBytes=max(1024, settings.log_max_bytes),
                backupCount=max(1, settings.log_backup_count),
                encoding="utf-8",
            )
            handlers.append(fh)
        except OSError as exc:
            logger.warning("日志文件不可写，仅输出控制台: %s", exc)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mcp-dbtools：数据库 MCP 服务 + Web 控制台")
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "sse", "stdio"],
        default=None,
        help="覆盖 MCP_DBTOOLS_TRANSPORT",
    )
    parser.add_argument("--host", default=None, help="覆盖监听地址")
    parser.add_argument("--port", type=int, default=None, help="覆盖监听端口")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        logger.error("配置错误: %s", exc)
        return 1

    _setup_logging(settings)

    transport = args.transport or settings.transport
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port

    if transport == "stdio":
        run_stdio(settings)
    else:
        settings.transport = transport
        run_http(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
