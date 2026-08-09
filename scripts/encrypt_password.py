#!/usr/bin/env python3
"""mcp-dbtools 数据源密码加密工具（AES-256-GCM）。

将明文密码加密为 {ENC:...} 串，复制到 config/datasources.json 的 password 字段，
运行时服务用 MCP_DBTOOLS_SECRET_KEY 自动解密 —— 配置文件不再保存明文密码。

用法:
    # 方式一：从标准输入读取（推荐，明文不落在 shell 历史/参数中）
    echo -n "Gauss@123" | python scripts/encrypt_password.py --stdin

    # 方式二：直接传明文参数（注意会出现在 shell 历史）
    python scripts/encrypt_password.py "Gauss@123"

密钥（MCP_DBTOOLS_SECRET_KEY）：
    - 可在 .env 中设置，或在此命令前以环境变量形式注入；
    - 服务端运行与加密时须使用同一个密钥；
    - 密钥请妥善保管（勿与配置文件一起明文提交到代码库）。

输出:
    {ENC:...}  可直接写入 datasources.json 的 "password": "{ENC:...}"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 保证未 pip install 时也能直接运行（把项目根加入 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_dbtools.crypto import CryptoError, encrypt_password  # noqa: E402

_SECRET_KEY_ENV = "MCP_DBTOOLS_SECRET_KEY"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成 mcp-dbtools 数据源密码的加密串（{ENC:...}）"
    )
    parser.add_argument("password", nargs="?", help="明文密码（与 --stdin 二选一）")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="从标准输入读取明文密码（推荐，避免明文进 shell 历史）",
    )
    args = parser.parse_args(argv)

    if args.stdin:
        plain = sys.stdin.read().rstrip("\n")
    elif args.password is not None:
        plain = args.password
    else:
        parser.error("请提供明文密码参数，或用 --stdin 从标准输入读取")

    secret = os.environ.get(_SECRET_KEY_ENV)
    if not secret:
        print(f"错误: 未设置 {_SECRET_KEY_ENV}（可写入 .env 或临时 export）", file=sys.stderr)
        return 2
    try:
        print(encrypt_password(plain, secret))
    except CryptoError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
