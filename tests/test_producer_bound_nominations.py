"""Composition regressions over genuinely ingested SQLite/FTS fixtures.

The shared protocol test uses an unused test embedder; real CPU process
qualification is separately required for runtime claims.
"""
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mindgraph import db, index_identity, mcp_server, routing
from mindgraph.cli import app
from tests.test_index_identity import produce, runner
from tests.test_query import KeywordEmbedder


@pytest.fixture
def anyio_backend():
    return "asyncio"


def compact(path, question="compass", top_k=3):
    result = runner.invoke(app, ["query", question, "--db", str(path),
        "--lexical-only", "--no-intent", "--json", "--envelope",
        "--nominations", "--identity-envelope", "--top-k", str(top_k),
        "--nomination-scope", "ops-reader"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("question,top_k,hits", [
    ("compass", 3, True), ("absentqwertybinding", 3, False),
    ("compass", 0, False),
])
def test_compact_keeps_independent_producer_identity_even_without_rows(tmp_path, question, top_k, hits):
    path, bound = produce(tmp_path)
    payload = compact(path, question, top_k)
    assert payload["schema_version"] == routing.SCHEMA_VERSION
    assert payload["database_identity"] == bound
    assert bool(payload["nominations"]) is hits
    assert "results" not in payload and "not_citable" not in payload
    assert "chunk_text" not in json.dumps(payload)
    if hits:
        nomination = payload["nominations"][0]
        assert nomination["scope_index"] == "ops-reader"
        assert nomination["index_id"] == bound["index_id"]
        expanded = runner.invoke(app, ["expand-nomination", nomination["expansion_handle"],
            "--db", str(path), "--scope", "ops-reader", "--json", "--identity-envelope"])
        assert expanded.exit_code == 0, expanded.output
        result = json.loads(expanded.stdout)
        assert result["database_identity"] == bound
        connection = db.get_db(str(path), read_only=True)
        try:
            stored = connection.execute(
                "SELECT text FROM chunks WHERE doc_id=? AND chunk_index=?",
                (result["doc_id"], result["chunk_index"]),
            ).fetchone()[0]
            assert result["chunk_text"] == stored
        finally:
            connection.close()


def test_identity_only_transport_retains_its_exact_three_key_shape(tmp_path):
    path, bound = produce(tmp_path)
    result = runner.invoke(app, ["query", "compass", "--db", str(path),
        "--lexical-only", "--no-intent", "--json", "--identity-envelope"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload) == {"schema_version", "database_identity", "results"}
    assert payload["schema_version"] == index_identity.QUERY_SCHEMA
    assert payload["database_identity"] == bound
    assert payload["results"][0]["chunk_text"]


def corrupt(path, bound, mutation):
    conn = db.get_db(str(path))
    if mutation == "missing":
        conn.execute("DELETE FROM index_meta WHERE key='index_identity'")
    else:
        value = dict(bound)
        value["source_document_map_sha256"] = "a" * 64
        text = json.dumps(value)
        if mutation == "duplicate":
            text = text[:-1] + ',"source_document_map_sha256":' + json.dumps(bound["source_document_map_sha256"]) + '}'
        conn.execute("UPDATE index_meta SET value=? WHERE key='index_identity'", (text,))
    conn.commit()
    conn.close()


@pytest.mark.parametrize("mutation", ["missing", "wrong-map", "duplicate"])
def test_selected_cli_expansion_revalidates_binding_before_returning_text(tmp_path, mutation):
    path, bound = produce(tmp_path)
    handle = compact(path)["nominations"][0]["expansion_handle"]
    corrupt(path, bound, mutation)
    result = runner.invoke(app, ["expand-nomination", handle, "--db", str(path),
        "--scope", "ops-reader", "--json", "--identity-envelope"])
    assert result.exit_code != 0
    assert "chunk_text" not in result.stdout


@pytest.mark.anyio
async def test_shared_selected_expansion_uses_required_snapshot_and_keeps_trust_axes(tmp_path):
    path, bound = produce(tmp_path)
    handle = compact(path)["nominations"][0]["expansion_handle"]
    conn = db.get_db(str(path), read_only=True)
    server = mcp_server.create_shared_server(
        {"ops-reader": (conn, "registration-label")}, KeywordEmbedder({"compass": 0}),
        required_identities={"ops-reader": {
            key: bound[key] for key in ("index_id", "trust_profile", "retrieval_scope", "lifecycle_root")}},
    )
    try:
        async with create_connected_server_and_client_session(server) as client:
            valid = await client.call_tool("expand_nomination", {
                "expansion_handle": handle, "scope": "ops-reader"})
            assert not valid.isError
            payload = json.loads(valid.content[0].text)
            assert payload["database_identity"] == bound
            assert payload["scope"] == payload["scope_index"] == "ops-reader"
            assert payload["index_id"] == bound["index_id"]
            assert payload["trust_profile"] == "operations_status"
            assert payload["scope_trust_profile"] == "registration-label"
            corrupt(path, bound, "wrong-map")
            for tool, arguments in [
                ("expand_nomination", {"expansion_handle": handle, "scope": "ops-reader"}),
                ("query", {"question": "compass", "scope": "ops-reader", "nominations": True, "final_top_k": 0}),
            ]:
                rejected = await client.call_tool(tool, arguments)
                assert rejected.isError
                assert "producer source document map differs" in rejected.content[0].text
                assert "chunk_text" not in rejected.content[0].text
    finally:
        conn.close()
