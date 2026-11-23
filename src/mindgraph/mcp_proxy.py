"""Official-SDK stdio server proxying to a Streamable HTTP MCP server."""

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TypeVar
import anyio
import json
import sys
import urllib.parse
import urllib.request
from mcp import ClientSession, McpError, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server import Server
from mcp.server.stdio import stdio_server

if sys.version_info >= (3, 11):
    _ExceptionGroup = BaseExceptionGroup
else:  # anyio depends on the exceptiongroup backport on Python 3.10
    from exceptiongroup import BaseExceptionGroup as _ExceptionGroup

T = TypeVar("T")

# The SDK client reports the daemon's HTTP 404 for an unknown session id as a
# JSON-RPC error with this message. A restarted daemon has no record of any
# earlier session, so it answers every request from a running proxy this way.
SESSION_TERMINATED = "Session terminated"


class UpstreamUnavailable(RuntimeError):
    """The proxy has no working session with the daemon for this request."""


class _SessionLost(UpstreamUnavailable):
    """The session ended while a request was waiting for its answer."""


def session_terminated(exc: BaseException) -> bool:
    return isinstance(exc, McpError) and exc.error.message == SESSION_TERMINATED


def _describe(exc: BaseException | None) -> str:
    """Name the underlying failure rather than the task group that carried it."""
    while isinstance(exc, _ExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return "unknown error" if exc is None else f"{type(exc).__name__}: {exc}"


def create_proxy_server(remote: "ClientSession | Upstream") -> Server:
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


@dataclass
class _Round:
    """One session with the daemon, from opening until it closes or fails."""

    generation: int
    session: ClientSession | None
    error: BaseException | None = None
    inflight: set[anyio.CancelScope] = field(default_factory=set)


class Upstream:
    """The proxy's session with the shared daemon, replaced after a restart.

    The SDK transport keeps an anyio task group open for the life of a session,
    so a single task must open and close it: `run()`. The stdio server handles
    each request in its own task. A request that fails with `Session
    terminated`, or whose session ends before it is answered, waits for `run()`
    to open a new session and is retried there once. Every other error reaches
    the client unchanged, and nothing loops.
    """

    def __init__(self, url: str, connect=remote_session):
        self._url = url
        self._connect = connect
        self._round = _Round(generation=0, session=None)
        self._started = False
        self._closing = False
        self._wanted = anyio.Event()
        self._changed = anyio.Event()
        self._finished = anyio.Event()

    async def run(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        """Hold one session open; open a new one when it ends or a request asks."""
        try:
            while not self._closing:
                wanted = self._wanted = anyio.Event()
                current = None
                try:
                    async with self._connect(self._url) as session:
                        current = self._publish(session, None)
                        if not self._started:
                            self._started = True
                            task_status.started()
                        try:
                            await wanted.wait()
                        finally:
                            # Also runs when the transport fails underneath us.
                            self._end(current)
                except Exception as exc:
                    if not self._started:
                        raise
                    if current is None:
                        # The daemon is not reachable. Tell whoever is waiting,
                        # and try again only when a request asks.
                        self._publish(None, exc)
                        await wanted.wait()
                    # Otherwise the old session failed while closing, which is
                    # expected once the daemon has forgotten it.
        finally:
            self._closing = True
            self._end(self._round)
            self._changed.set()
            self._finished.set()

    async def aclose(self) -> None:
        """Close the current session normally, as the proxy does on exit."""
        self._closing = True
        self._wanted.set()
        await self._finished.wait()

    async def list_tools(self) -> types.ListToolsResult:
        return await self._call(lambda session: session.list_tools())

    async def call_tool(self, name: str, arguments: dict | None) -> types.CallToolResult:
        return await self._call(lambda session: session.call_tool(name, arguments))

    def _publish(self, session: ClientSession | None, error: BaseException | None) -> _Round:
        current = self._round = _Round(self._round.generation + 1, session, error)
        changed, self._changed = self._changed, anyio.Event()
        changed.set()
        return current

    @staticmethod
    def _end(current: _Round) -> None:
        current.session = None
        for scope in list(current.inflight):
            scope.cancel()

    async def _call(self, request: Callable[[ClientSession], Awaitable[T]]) -> T:
        current = self._round
        if current.session is not None:
            try:
                return await self._attempt(request, current)
            except McpError as exc:
                if not session_terminated(exc):
                    raise
            except _SessionLost:
                pass
        # The session is gone, or the last attempt to reach the daemon failed:
        # one new session, one more attempt.
        return await self._attempt(request, await self._replace(current.generation))

    async def _attempt(self, request: Callable[[ClientSession], Awaitable[T]], current: _Round) -> T:
        """Send one request on one session, giving up if that session ends."""
        if current.session is None:
            raise _SessionLost(f"the session with the MindGraph daemon at {self._url} ended")
        with anyio.CancelScope() as scope:
            current.inflight.add(scope)
            try:
                return await request(current.session)
            finally:
                current.inflight.discard(scope)
        raise _SessionLost(
            f"the MindGraph daemon at {self._url} closed the session before answering"
        )

    async def _replace(self, seen: int) -> _Round:
        """Return a round newer than generation `seen`, asking for one if needed.

        Concurrent callers that saw the same generation share one reconnect.
        """
        if self._round.generation == seen and not self._closing:
            changed = self._changed
            self._wanted.set()
            await changed.wait()
        current = self._round
        if current.session is None:
            reason = "the proxy is shutting down" if self._closing else _describe(current.error)
            raise UpstreamUnavailable(
                f"MindGraph daemon at {self._url} is not reachable ({reason})"
            ) from current.error
        return current


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
            upstream = Upstream(url)
            await tasks.start(upstream.run)
            try:
                proxy = create_proxy_server(upstream)
                async with stdio_server() as (read, write):
                    await proxy.run(read, write, proxy.create_initialization_options())
            finally:
                await upstream.aclose()
            tasks.cancel_scope.cancel()
    finally:
        if token:
            try:
                await anyio.to_thread.run_sync(_lease_request, url, token, "DELETE")
            except Exception:
                pass


def run_proxy_sync(url: str, *, lease: bool = False) -> None:
    anyio.run(lambda: run_proxy(url, lease=lease))
