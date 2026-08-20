import json
import os
import signal
import sqlite3
import threading
import time
from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from httpx import ASGITransport, AsyncClient

from mindgraph import cli, daemon, idle_lifecycle, mcp_proxy, mcp_server
from mindgraph.exceptions import MindgraphError
from tests.test_mcp import mcp_db, mcp_embedder
from tests.test_query import KeywordEmbedder


def _payload(result):
    return json.loads(result.content[0].text)


def test_streamable_http_defaults_and_configuration(monkeypatch):
    conn = sqlite3.connect(":memory:")
    server = mcp_server.create_shared_server(
        {"knowledge": (conn, "durable_knowledge")}, KeywordEmbedder({})
    )
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 8000
    assert server.settings.streamable_http_path == "/mcp"
    called = []
    monkeypatch.setattr(server, "run", lambda transport: called.append(transport))
    mcp_server.run_streamable_http(server)
    assert called == ["streamable-http"]


def test_shared_server_rejects_non_loopback():
    with pytest.raises(mcp_server.MCPServerStartupError, match="loopback"):
        mcp_server.create_shared_server({}, KeywordEmbedder({}), host="0.0.0.0")


@pytest.mark.anyio
async def test_scopes_are_explicit_and_not_blended(tmp_path, monkeypatch):
    embedder = KeywordEmbedder({"knowledgeonly": 0, "projectonly": 1})
    monkeypatch.setattr(cli, "_load_embedder", lambda *_a, **_k: embedder)
    knowledge_vault = tmp_path / "knowledge-vault"
    projects_vault = tmp_path / "projects-vault"
    knowledge_vault.mkdir(); projects_vault.mkdir()
    (knowledge_vault / "durable-only.md").write_text(
        "knowledgeonly durable architecture note\n", encoding="utf-8"
    )
    (projects_vault / "project-only.md").write_text(
        "projectonly active project status\n", encoding="utf-8"
    )
    knowledge_db = tmp_path / "knowledge.sqlite"
    projects_db = tmp_path / "projects.sqlite"
    cli._ingest_directory(knowledge_vault, str(knowledge_db))
    cli._ingest_directory(projects_vault, str(projects_db))
    first = mcp_server.open_database(str(knowledge_db))
    second = mcp_server.open_database(str(projects_db))
    server = mcp_server.create_shared_server(
        {"knowledge": (first, "durable_knowledge"),
         "projects": (second, "project_status")}, embedder,
    )
    try:
        async with create_connected_server_and_client_session(server) as session:
            knowledge = _payload(await session.call_tool(
                "query", {"question": "knowledgeonly", "scope": "knowledge"}))
            projects = _payload(await session.call_tool(
                "query", {"question": "projectonly", "scope": "projects"}))
            invalid = await session.call_tool(
                "query", {"question": "anything", "scope": "both"})
        assert knowledge["scope"] == "knowledge"
        assert knowledge["trust_profile"] == "durable_knowledge"
        assert projects["scope"] == "projects"
        assert projects["trust_profile"] == "project_status"
        assert {row["path"] for row in knowledge["results"]} == {"durable-only.md"}
        assert {row["path"] for row in projects["results"]} == {"project-only.md"}
        assert invalid.isError is True
    finally:
        first.close(); second.close()


def test_open_database_readonly_rejects_writes(mcp_db):
    conn = mcp_server.open_database_readonly(mcp_db)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM documents")
    finally:
        conn.close()


@pytest.mark.anyio
async def test_proxy_forwards_list_and_call():
    remote = AsyncMock()
    remote.list_tools.return_value.tools = []
    remote.call_tool.return_value = {"ok": True}
    proxy = mcp_proxy.create_proxy_server(remote)
    async with create_connected_server_and_client_session(proxy) as session:
        assert (await session.list_tools()).tools == []
        await session.call_tool("query", {"scope": "knowledge", "question": "x"})
    remote.call_tool.assert_awaited_once_with(
        "query", {"scope": "knowledge", "question": "x"})


@pytest.mark.anyio
async def test_remote_session_initializes_and_propagates_error(monkeypatch):
    initialized = AsyncMock(side_effect=RuntimeError("init failed"))
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        initialize = initialized
    class Transport:
        async def __aenter__(self): return (object(), object(), lambda: None)
        async def __aexit__(self, *args): return False
    monkeypatch.setattr(mcp_proxy, "streamablehttp_client", lambda _url: Transport())
    monkeypatch.setattr(mcp_proxy, "ClientSession", lambda *_args: FakeClient())
    with pytest.raises(RuntimeError, match="init failed"):
        async with mcp_proxy.remote_session("http://127.0.0.1:9/mcp"):
            pass


def test_daemon_status_stop_and_health_use_isolated_state(tmp_path, monkeypatch):
    assert daemon.status(tmp_path) == {"status": "stopped", "pid": None}
    daemon.paths(tmp_path)[0].write_text("99999999\n")
    assert daemon.stop(tmp_path)["status"] == "stopped"
    monkeypatch.setattr(daemon.urllib.request, "urlopen",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline")))
    assert daemon.health("http://127.0.0.1:9/health")["status"] == "unhealthy"


def test_daemon_start_and_stop_tracks_real_child(tmp_path):
    result = daemon.start(
        tmp_path, [os.sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert daemon.status(tmp_path)["status"] == "running"
        assert daemon.stop(tmp_path, timeout=2)["status"] == "stopped"
    finally:
        if daemon.alive(result["pid"]):
            os.kill(result["pid"], signal.SIGKILL)


def test_daemon_status_reports_healthy_external_supervisor(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "health", lambda *_a, **_k: {
        "status": "ok", "pid": 4242, "scopes": [],
    })
    result = daemon.status(tmp_path, "http://127.0.0.1:8000/health")
    assert result["status"] == "running"
    assert result["supervision"] == "external"
    assert result["pid"] == 4242


def test_daemon_stop_refuses_pid_mismatch(tmp_path, monkeypatch):
    daemon.paths(tmp_path)[0].write_text("111\n")
    monkeypatch.setattr(daemon, "health", lambda *_a, **_k: {
        "status": "ok", "pid": 222, "scopes": [],
    })
    assert daemon.stop(tmp_path, health_url="http://127.0.0.1:1/health") == {
        "status": "refused_pid_mismatch", "pid": 111, "observed_pid": 222,
    }


def test_daemon_stop_refuses_healthy_listener_without_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "health", lambda *_a, **_k: {
        "status": "ok", "scopes": [],
    })
    assert daemon.stop(tmp_path, health_url="http://127.0.0.1:1/health") == {
        "status": "refused_unverified_listener", "pid": None,
    }


def test_daemon_stop_refuses_live_pid_without_start_identity(tmp_path, monkeypatch):
    daemon.paths(tmp_path)[0].write_text(f"{os.getpid()}\n")
    monkeypatch.setattr(daemon, "health", lambda *_a, **_k: {"status": "unhealthy"})
    assert daemon.stop(tmp_path, health_url="http://127.0.0.1:1/health") == {
        "status": "refused_unverified_pid", "pid": os.getpid(),
    }


def test_idle_lifecycle_never_exits_in_flight_and_honors_lease():
    now = [0.0]
    stopped = []
    lifecycle = idle_lifecycle.IdleLifecycle(
        60, lease_ttl=30, clock=lambda: now[0], request_shutdown=lambda: stopped.append(True)
    )
    with lifecycle.request():
        now[0] = 120
        assert lifecycle.should_shutdown() is False
    token = lifecycle.acquire_lease()
    now[0] = 140
    assert lifecycle.should_shutdown() is False
    lifecycle.release_lease(token)
    now[0] = 201
    assert lifecycle.should_shutdown() is True


def test_idle_lifecycle_monitor_requests_shutdown_only_after_grace():
    stopped = threading.Event()
    lifecycle = idle_lifecycle.IdleLifecycle(0.15, request_shutdown=stopped.set)
    lifecycle.start()
    try:
        time.sleep(0.05)
        assert not stopped.is_set()
        assert stopped.wait(1)
    finally:
        lifecycle.stop()


@pytest.mark.anyio
async def test_shared_server_lease_endpoint_tracks_and_releases():
    lifecycle = idle_lifecycle.IdleLifecycle(60)
    server = mcp_server.create_shared_server(
        {"knowledge": (sqlite3.connect(":memory:"), "durable_knowledge")},
        KeywordEmbedder({}), lifecycle=lifecycle,
    )
    async with AsyncClient(
        transport=ASGITransport(app=server.streamable_http_app()),
        base_url="http://test",
    ) as client:
        acquired = (await client.post("/lifecycle/lease")).json()
        assert lifecycle.snapshot()["active_leases"] == 1
        renewed = (await client.post(
            "/lifecycle/lease", headers={"X-MindGraph-Lease": acquired["lease"]}
        )).json()
        assert renewed["lease"] == acquired["lease"]
        assert (await client.delete(
            "/lifecycle/lease", headers={"X-MindGraph-Lease": acquired["lease"]}
        )).json()["status"] == "released"
        assert lifecycle.snapshot()["active_leases"] == 0


def test_daemon_start_serializes_concurrent_callers(tmp_path):
    command = [os.sys.executable, "-c", "import time; time.sleep(30)"]
    barrier = threading.Barrier(3)
    results = []
    def launch():
        barrier.wait()
        results.append(daemon.start(tmp_path, command))
    threads = [threading.Thread(target=launch) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    try:
        assert {result["status"] for result in results} == {"started", "running"}
        assert len({result["pid"] for result in results}) == 1
    finally:
        assert daemon.stop(tmp_path, timeout=2)["status"] == "stopped"


def test_daemon_endpoint_args_rejects_non_loopback():
    assert daemon.daemon_endpoint_args("http://127.0.0.1:8123/custom") == (
        "127.0.0.1", 8123, "/custom"
    )
    with pytest.raises(ValueError, match="loopback"):
        daemon.daemon_endpoint_args("https://example.com/mcp")
    assert daemon.health_url("::1", 8123) == "http://[::1]:8123/health"


def test_idle_opt_in_requires_explicit_valid_marker(tmp_path):
    assert daemon.idle_opt_in(tmp_path) is None
    (tmp_path / "idle-lifecycle.enabled").write_text("900\n")
    assert daemon.idle_opt_in(tmp_path) == 900
    (tmp_path / "idle-lifecycle.enabled").write_text("30\n")
    assert daemon.idle_opt_in(tmp_path) is None


def test_parse_scope_specs_name_only_defaults_trust_to_name():
    assert cli.parse_scope_specs(["notes=/tmp/notes.sqlite"]) == {
        "notes": ("/tmp/notes.sqlite", "notes")
    }


def test_parse_scope_specs_explicit_trust_profile():
    assert cli.parse_scope_specs(["notes:durable_knowledge=/tmp/n.sqlite"]) == {
        "notes": ("/tmp/n.sqlite", "durable_knowledge")
    }


def test_parse_scope_specs_allows_equals_and_colon_in_path():
    parsed = cli.parse_scope_specs(["a=/tmp/x:y=z.sqlite"])
    assert parsed == {"a": ("/tmp/x:y=z.sqlite", "a")}


def test_parse_scope_specs_multiple_scopes():
    parsed = cli.parse_scope_specs(["a=/tmp/a.sqlite", "b:vol=/tmp/b.sqlite"])
    assert parsed == {"a": ("/tmp/a.sqlite", "a"), "b": ("/tmp/b.sqlite", "vol")}


def test_parse_scope_specs_empty_is_empty_dict():
    assert cli.parse_scope_specs(None) == {}
    assert cli.parse_scope_specs([]) == {}


@pytest.mark.parametrize("bad", ["notes", "=/tmp/a.sqlite", "notes=", ":t=/tmp/a.sqlite"])
def test_parse_scope_specs_rejects_malformed(bad):
    with pytest.raises(MindgraphError):
        cli.parse_scope_specs([bad])


def test_parse_scope_specs_rejects_duplicate_names():
    with pytest.raises(MindgraphError):
        cli.parse_scope_specs(["a=/tmp/1.sqlite", "a=/tmp/2.sqlite"])
