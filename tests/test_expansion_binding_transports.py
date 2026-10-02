"""Real CLI and MCP collision regressions. No model downloads or benchmark edits."""
import hashlib
import json
import subprocess
import sys

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mindgraph import db, mcp_server
from tests.test_query import KeywordEmbedder


@pytest.fixture
def anyio_backend():
    return "asyncio"


def seed(path, index_id, trust="same-trust"):
    conn = db.init_db(str(path))
    text = "alpha binding fixture " * 60
    # Same identifiers, chunk bytes and digest across both indexes are intentional.
    # Index identity must discriminate even when every content key collides.
    conn.execute("INSERT INTO documents(id,title,path,content_hash,index_id,namespace,trust_profile,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                 ("same-doc", "Binding", "same.md", hashlib.sha256(text.encode()).hexdigest(), index_id, "notes", trust, "{}"))
    conn.execute("INSERT INTO chunks(doc_id,chunk_index,text) VALUES(?,?,?)", ("same-doc", 0, text))
    conn.execute("INSERT INTO documents_fts(id,title,content) VALUES(?,?,?)", ("same-doc", "Binding", text))
    conn.commit()
    return conn


def cli(*args):
    return subprocess.run([sys.executable, "-c", "from mindgraph.cli import app; app()", *map(str,args)],
                          capture_output=True, text=True, timeout=45)


def test_real_cli_rejects_cross_index_collision(tmp_path):
    left, right = tmp_path / "left.sqlite", tmp_path / "right.sqlite"
    seed(left, "index-a").close(); seed(right, "index-b").close()
    query = cli("query", "alpha", "--db", left, "--json", "--envelope", "--nominations", "--nomination-scope", "knowledge", "--lexical-only", "--no-intent")
    assert query.returncode == 0, query.stderr
    payload = json.loads(query.stdout)
    assert "results" not in payload and "not_citable" not in payload
    assert '"chunk_text"' not in query.stdout
    handle = payload["nominations"][0]["expansion_handle"]
    good = cli("expand-nomination", handle, "--db", left, "--json", "--scope", "knowledge")
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["index_id"] == "index-a"
    for target, scope in [(right, "knowledge"), (right, "projects"), (left, "projects")]:
        bad = cli("expand-nomination", handle, "--db", target, "--json", "--scope", scope)
        assert bad.returncode != 0
        assert '"chunk_text"' not in bad.stdout
        assert "alpha binding fixture alpha" not in bad.stdout + bad.stderr
    # Index binding is checked even when --scope is omitted at the CLI boundary.
    bad = cli("expand-nomination", handle, "--db", right, "--json")
    assert bad.returncode != 0


@pytest.mark.anyio
async def test_shared_registered_alias_is_not_an_index_id(tmp_path):
    left = seed(tmp_path / "left.sqlite", "index-a")
    right = seed(tmp_path / "right.sqlite", "index-b")
    try:
        server = mcp_server.create_shared_server({"alias-one": (left, "same-trust"), "alias-two": (right, "same-trust")}, KeywordEmbedder({"alpha": 0}))
        async with create_connected_server_and_client_session(server) as session:
            found = await session.call_tool("query", {"question": "alpha", "scope": "alias-one", "nominations": True})
            assert not found.isError
            handle = json.loads(found.content[0].text)["nominations"][0]["expansion_handle"]
            good = await session.call_tool("expand_nomination", {"expansion_handle": handle, "scope": "alias-one"})
            assert not good.isError
            assert json.loads(good.content[0].text)["index_id"] == "index-a"
            bad = await session.call_tool("expand_nomination", {"expansion_handle": handle, "scope": "alias-two"})
            assert bad.isError
    finally:
        left.close(); right.close()


@pytest.mark.anyio
async def test_single_mcp_cannot_redeem_other_index_handle(tmp_path):
    left = seed(tmp_path / "left.sqlite", "index-a")
    right = seed(tmp_path / "right.sqlite", "index-b")
    try:
        async with create_connected_server_and_client_session(mcp_server.create_server(left, KeywordEmbedder({"alpha": 0}))) as session:
            result = await session.call_tool("query", {"question": "alpha", "semantic_top_k": 0, "envelope": True, "nominations": True})
            assert not result.isError
            handle = json.loads(result.content[0].text)["nominations"][0]["expansion_handle"]
        async with create_connected_server_and_client_session(mcp_server.create_server(right, KeywordEmbedder({"alpha": 0}))) as session:
            bad = await session.call_tool("expand_nomination", {"expansion_handle": handle})
            assert bad.isError
    finally:
        left.close(); right.close()
