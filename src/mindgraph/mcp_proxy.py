"""Official-SDK stdio server proxying to a Streamable HTTP MCP server."""

from contextlib import asynccontextmanager
import anyio
import json
import urllib.parse
import urllib.request
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


def _lease_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/lifecycle/lease", "", ""))


def _lease_request(url: str, token: str = "", method: str = "POST") -> dict:
    request = urllib.request.Request(
        _lease_url(url), method=method,
        headers={"X-MindGraph-Lease": token} if token else {},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read())


async def _renew_lease(url: str, token: str, interval: float) -> None:
    while True:
        await anyio.sleep(interval)
        await anyio.to_thread.run_sync(_lease_request, url, token)


async def run_proxy(url: str, *, lease: bool = False) -> None:
    token = ""
    if lease:
        response = await anyio.to_thread.run_sync(_lease_request, url)
        token = response["lease"]
        interval = max(1.0, float(response["ttl_seconds"]) / 3)
    try:
        async with anyio.create_task_group() as tasks:
            if token:
                tasks.start_soon(_renew_lease, url, token, interval)
            async with remote_session(url) as remote:
                proxy = create_proxy_server(remote)
                async with stdio_server() as (read, write):
                    await proxy.run(read, write, proxy.create_initialization_options())
            tasks.cancel_scope.cancel()
    finally:
        if token:
            try:
                await anyio.to_thread.run_sync(_lease_request, url, token, "DELETE")
            except Exception:
                pass


def run_proxy_sync(url: str, *, lease: bool = False) -> None:
    anyio.run(lambda: run_proxy(url, lease=lease))
