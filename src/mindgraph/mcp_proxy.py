"""Official-SDK stdio server proxying to a Streamable HTTP MCP server."""

from contextlib import asynccontextmanager

import anyio
from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server import Server
from mcp.server.stdio import stdio_server


def create_proxy_server(remote: ClientSession) -> Server:
    server = Server("mindgraph-mcp-proxy")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return (await remote.list_tools()).tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None):
        return await remote.call_tool(name, arguments)

    return server


@asynccontextmanager
async def remote_session(url: str):
    async with streamablehttp_client(url) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def run_proxy(url: str) -> None:
    async with remote_session(url) as remote:
        proxy = create_proxy_server(remote)
        async with stdio_server() as (read, write):
            await proxy.run(read, write, proxy.create_initialization_options())


def run_proxy_sync(url: str) -> None:
    anyio.run(run_proxy, url)
