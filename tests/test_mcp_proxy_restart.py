"""A running `mindgraph mcp-proxy` must survive a restart of the shared daemon.

The daemon keeps MCP sessions in memory. After a restart it answers the
proxy's old session id with HTTP 404, which the MCP client reports as
"Session terminated". Before the fix every later call through that proxy
failed the same way until the proxy process was restarted.

These tests restart a real Streamable HTTP daemon on a loopback port under a
real `mindgraph mcp-proxy` process. A keyword embedder stands in for the
sentence-transformer model, so no model download is needed.
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from contextlib import asynccontextmanager

import anyio
import pytest
import uvicorn
from mcp import ClientSession, McpError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ErrorData

from mindgraph import cli, mcp_proxy, mcp_server
from tests.test_query import KeywordEmbedder

QUERY = {"question": "restartprobe", "scope": "knowledge"}


def _payload(result):
    return json.loads(result.content[0].text)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_health(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"daemon on port {port} never became healthy")


class Daemon:
    """One lifetime of the shared daemon, served from a background thread.

    Each `start()` builds a new server, so sessions from an earlier lifetime
    are unknown to it, exactly as after a real process restart.
    """

    def __init__(self, db_path, port: int):
        self.db_path, self.port = db_path, port
        self._server = None
        self._thread = None

    def start(self) -> "Daemon":
        created = threading.Event()

        def serve():
            conn = mcp_server.open_database_readonly(str(self.db_path))
            try:
                server = mcp_server.create_shared_server(
                    {"knowledge": (conn, "durable_knowledge")},
                    KeywordEmbedder({"restartprobe": 0}),
                    port=self.port,
                )
                self._server = uvicorn.Server(uvicorn.Config(
                    server.streamable_http_app(), host="127.0.0.1", port=self.port,
                    log_level="warning", timeout_graceful_shutdown=1,
                ))
                created.set()
                self._server.run()
            finally:
                conn.close()

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()
        assert created.wait(10), "daemon thread did not start"
        _wait_for_health(self.port)
        return self

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.should_exit = True
        self._thread.join(15)
        assert not self._thread.is_alive(), "daemon did not stop"
        self._thread = None


@pytest.fixture
def knowledge_db(tmp_path, monkeypatch):
    embedder = KeywordEmbedder({"restartprobe": 0})
    monkeypatch.setattr(cli, "_load_embedder", lambda *_a, **_k: embedder)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "restart-note.md").write_text(
        "restartprobe note that the proxy should still find\n", encoding="utf-8"
    )
    db_path = tmp_path / "knowledge.sqlite"
    cli._ingest_directory(vault, str(db_path))
    return db_path


def _proxy_params(port: int, tmp_path) -> StdioServerParameters:
    # The default stdio environment drops PYTHONPATH; pass the test's own
    # environment so the proxy runs the same mindgraph as the test.
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mindgraph.cli", "mcp-proxy",
              "--url", f"http://127.0.0.1:{port}/mcp",
              "--no-auto-start", "--state-dir", str(tmp_path / "proxy-state")],
        env=dict(os.environ),
        cwd=str(tmp_path),
    )


@pytest.mark.anyio
async def test_running_proxy_survives_daemon_restart(knowledge_db, tmp_path):
    port = _free_port()
    daemon = Daemon(knowledge_db, port).start()
    try:
        async with stdio_client(_proxy_params(port, tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                before = await session.call_tool("query", QUERY)
                assert not before.isError, before.content[0].text
                assert [r["path"] for r in _payload(before)["results"]] == ["restart-note.md"]

                daemon.stop()
                daemon = Daemon(knowledge_db, port).start()
                after_call = await session.call_tool("query", QUERY)
                assert not after_call.isError, after_call.content[0].text
                assert _payload(after_call)["results"] == _payload(before)["results"]

                # A second restart, this time noticed first by list_tools.
                daemon.stop()
                daemon = Daemon(knowledge_db, port).start()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {"query", "graph_neighbors"}
                again = await session.call_tool("query", QUERY)
                assert not again.isError, again.content[0].text
    finally:
        daemon.stop()


@pytest.mark.anyio
async def test_proxy_reports_daemon_downtime_and_recovers(knowledge_db, tmp_path):
    port = _free_port()
    daemon = Daemon(knowledge_db, port).start()
    try:
        async with stdio_client(_proxy_params(port, tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert not (await session.call_tool("query", QUERY)).isError

                daemon.stop()
                # Before the fix this call never returned: the request failed
                # inside the SDK transport and took the proxy process down.
                with anyio.fail_after(10):
                    down = await session.call_tool("query", QUERY)
                assert down.isError
                assert "not reachable" in down.content[0].text

                daemon = Daemon(knowledge_db, port).start()
                back = await session.call_tool("query", QUERY)
                assert not back.isError, back.content[0].text
    finally:
        daemon.stop()


# --- Upstream rules, with scripted sessions instead of a daemon ------------

TERMINATED = McpError(ErrorData(code=32600, message="Session terminated"))


class FakeSession:
    """Each call takes the next scripted outcome: a value, an exception, or a coroutine function."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def call_tool(self, name, arguments):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return await outcome()
        return outcome

    async def list_tools(self):
        return await self.call_tool("list_tools", None)


class FakeDaemon:
    """Hands out scripted sessions in order; an exception entry fails that connect."""

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def connect(self, _url):
        item = self.rounds.pop(0)
        if isinstance(item, BaseException):
            raise item
        self.opened += 1
        try:
            yield item
        finally:
            self.closed += 1


@asynccontextmanager
async def running(daemon):
    upstream = mcp_proxy.Upstream("http://127.0.0.1:9/mcp", connect=daemon.connect)
    async with anyio.create_task_group() as tg:
        await tg.start(upstream.run)
        try:
            yield upstream
        finally:
            await upstream.aclose()


@pytest.mark.anyio
async def test_session_terminated_reconnects_and_retries_once():
    first, second = FakeSession(TERMINATED), FakeSession("answer")
    daemon = FakeDaemon(first, second)
    async with running(daemon) as upstream:
        assert await upstream.call_tool("query", {}) == "answer"
    assert (first.calls, daemon.opened, daemon.closed) == (1, 2, 2)


@pytest.mark.anyio
async def test_list_tools_reconnects_the_same_way():
    daemon = FakeDaemon(FakeSession(TERMINATED), FakeSession("tools"))
    async with running(daemon) as upstream:
        assert await upstream.list_tools() == "tools"
    assert daemon.opened == 2


@pytest.mark.anyio
async def test_other_errors_reach_the_client_without_a_retry():
    boom = McpError(ErrorData(code=-32603, message="boom"))
    daemon = FakeDaemon(FakeSession(boom))
    async with running(daemon) as upstream:
        with pytest.raises(McpError, match="boom"):
            await upstream.call_tool("query", {})
    assert daemon.opened == 1


@pytest.mark.anyio
async def test_a_request_is_retried_at_most_once():
    daemon = FakeDaemon(FakeSession(TERMINATED), FakeSession(TERMINATED))
    async with running(daemon) as upstream:
        with pytest.raises(McpError, match="Session terminated"):
            await upstream.call_tool("query", {})
    assert daemon.opened == 2


@pytest.mark.anyio
async def test_unreachable_daemon_fails_that_call_and_the_next_call_tries_again():
    daemon = FakeDaemon(FakeSession(TERMINATED), OSError("connection refused"), FakeSession("answer"))
    async with running(daemon) as upstream:
        with pytest.raises(mcp_proxy.UpstreamUnavailable, match="OSError: connection refused"):
            await upstream.call_tool("query", {})
        assert await upstream.call_tool("query", {}) == "answer"
    assert daemon.opened == 2


@pytest.mark.anyio
async def test_concurrent_calls_share_one_reconnect():
    daemon = FakeDaemon(FakeSession(TERMINATED, TERMINATED), FakeSession("a", "b"))
    results = []

    async def ask(upstream):
        results.append(await upstream.call_tool("query", {}))

    async with running(daemon) as upstream:
        async with anyio.create_task_group() as tg:
            tg.start_soon(ask, upstream)
            tg.start_soon(ask, upstream)
    assert sorted(results) == ["a", "b"]
    assert daemon.opened == 2


@pytest.mark.anyio
async def test_request_in_flight_when_its_session_ends_is_retried():
    never = anyio.Event()

    async def wait_forever():
        await never.wait()

    first = FakeSession(wait_forever, TERMINATED)
    second = FakeSession("answer", "answer")
    daemon = FakeDaemon(first, second)
    results = []

    async def ask(upstream):
        results.append(await upstream.call_tool("query", {}))

    async with running(daemon) as upstream:
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(ask, upstream)   # waits on the first session
                await anyio.sleep(0)
                tg.start_soon(ask, upstream)   # finds that session terminated
    assert results == ["answer", "answer"]
    assert (first.calls, second.calls, daemon.opened) == (2, 2, 2)


@pytest.mark.anyio
async def test_closing_ends_the_session_and_refuses_later_calls():
    daemon = FakeDaemon(FakeSession())
    async with running(daemon) as upstream:
        pass
    assert daemon.closed == 1
    with pytest.raises(mcp_proxy.UpstreamUnavailable, match="shutting down"):
        await upstream.call_tool("query", {})


@pytest.mark.anyio
async def test_a_daemon_unreachable_at_startup_still_fails_startup():
    daemon = FakeDaemon(OSError("connection refused"))
    upstream = mcp_proxy.Upstream("http://127.0.0.1:9/mcp", connect=daemon.connect)
    with pytest.raises(Exception) as info:
        async with anyio.create_task_group() as tg:
            await tg.start(upstream.run)
    assert mcp_proxy._describe(info.value) == "OSError: connection refused"
