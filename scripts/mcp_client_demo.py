#!/usr/bin/env python3
"""MCP HTTP 客户端演示：通过 streamable HTTP 调用远程 mcp-dbtools 服务。

用法（先启动服务）:
    MCP_DBTOOLS_TRANSPORT=streamable-http .venv/bin/python -m mcp_dbtools
    .venv/bin/python scripts/mcp_client_demo.py --url http://127.0.0.1:8000/mcp

可指定 token:
    .venv/bin/python scripts/mcp_client_demo.py --token <MCP_DBTOOLS_AUTH_TOKEN>
"""

from __future__ import annotations

import argparse
import asyncio

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _no_proxy_client_factory(**kwargs) -> httpx.AsyncClient:
    """忽略环境代理变量（本机/内网直连调试时避免 SOCKS 依赖）。"""
    kwargs.setdefault("trust_env", False)
    return httpx.AsyncClient(**kwargs)


async def run(url: str, token: str | None, datasource: str, sql: str) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with streamablehttp_client(
        url, headers=headers, httpx_client_factory=_no_proxy_client_factory
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("== 服务器信息 ==")
            si = init.serverInfo
            print(
                f"  name={si.name} version={getattr(si, 'version', 'n/a')}"
                f" protocol={getattr(init, 'protocolVersion', 'n/a')}"
            )

            print("\n== 可用工具 ==")
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"  - {t.name}: {t.description.splitlines()[0]}")

            print("\n== 列出数据源 ==")
            r = await session.call_tool(
                "list_datasources", {}
            )
            print(r.content[0].text)

            print(f"\n== 测试连接: {datasource} ==")
            r = await session.call_tool("test_connection", {"datasource": datasource})
            print(r.content[0].text)

            print(f"\n== 执行查询: {sql} ==")
            r = await session.call_tool(
                "execute_query", {"datasource": datasource, "sql": sql}
            )
            print(r.content[0].text)

            print("\n== 列出表 ==")
            r = await session.call_tool("list_tables", {"datasource": datasource})
            print(r.content[0].text)


def main() -> None:
    parser = argparse.ArgumentParser(description="mcp-dbtools HTTP 客户端演示")
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000/mcp", help="MCP HTTP 端点"
    )
    parser.add_argument("--token", default=None, help="Bearer Token（可选）")
    parser.add_argument("--datasource", default="gaussdb_test")
    parser.add_argument("--sql", default="SELECT * FROM employees ORDER BY id")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.token, args.datasource, args.sql))


if __name__ == "__main__":
    main()
