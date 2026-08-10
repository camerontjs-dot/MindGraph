import json
import os
import signal
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mindgraph import cli, daemon, mcp_proxy, mcp_server
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
    knowledge_vault.mkdir()
    projects_vault.mkdir()
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
        {"knowledge": (first, "durable_knowledge"), "projects": (second, "project_status")},
        embedder,
    )
    try:
        async with create_connected_server_and_client_session(server) as session:
            knowledge = _payload(await session.call_tool("query", {"question": "knowledgeonly", "scope": "knowledge"}))
            projects = _payload(await session.call_tool("query", {"question": "projectonly", "scope": "projects"}))
            invalid = await session.call_tool("query", {"question": "anything", "scope": "both"})
        assert knowledge["scope"] == "knowledge"
        assert knowledge["trust_profile"] == "durable_knowledge"
        assert projects["scope"] == "projects"
        assert projects["trust_profile"] == "project_status"
        assert {row["path"] for row in knowledge["results"]} == {"durable-only.md"}
        assert {row["path"] for row in projects["results"]} == {"project-only.md"}
        assert all(row["path"] != "project-only.md" for row in knowledge["results"])
        assert all(row["path"] != "durable-only.md" for row in projects["results"])
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
    remote.call_tool.assert_awaited_once_with("query", {"scope": "knowledge", "question": "x"})


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
    pid_file, _ = daemon.paths(tmp_path)
    pid_file.write_text("99999999\n")
    assert daemon.stop(tmp_path)["status"] == "stopped"
    monkeypatch.setattr(daemon.urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline")))
    result = daemon.health("http://127.0.0.1:9/health")
    assert result["status"] == "unhealthy"
    assert "offline" in result["error"]


def test_daemon_start_and_stop_tracks_real_child(tmp_path):
    result = daemon.start(tmp_path, [os.environ.get("PYTHON", os.sys.executable), "-c", "import time; time.sleep(30)"])
    try:
        assert result["status"] == "started"
        assert daemon.status(tmp_path)["status"] == "running"
        assert daemon.stop(tmp_path, timeout=2)["status"] == "stopped"
    finally:
        if daemon.alive(result["pid"]):
            os.kill(result["pid"], signal.SIGKILL)
