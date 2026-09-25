"""Stage 1A canonical nomination envelope + expansion handles.

Locks the issue #21 contract: additive compact nominations over ordinary
lexical / semantic / fused rows with deterministic identity, preserved
provenance, explicit UNKNOWNs, and fail-closed source-backed expansion.
Ranking, models, chunking, and the legacy list/envelope shapes are unchanged.
"""

import base64
import copy
import hashlib
import json as jsonlib

import pytest
from typer.testing import CliRunner

from mcp.shared.memory import create_connected_server_and_client_session

from mindgraph import cli, db, mcp_server, nominations
from mindgraph.exceptions import MindgraphError
from mindgraph.models import QueryResult
from mindgraph.nominations import (
    EXPANSION_VERSION,
    POLICY_VERSION,
    PREVIEW_CHARS,
    NominationError,
    encode_expansion_handle,
    parse_expansion_handle,
    project_nominations,
    resolve_expansion,
)
from mindgraph.query import run_query
from tests.test_query import KeywordEmbedder


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _tokens(count: int) -> str:
    return " ".join(["tok"] * count)


def _row(doc_id, path, content_hash, **overrides) -> QueryResult:
    signal = overrides.pop("signal", "fused")
    lexical = 1 if signal in {"lexical", "fused"} else None
    semantic = 1 if signal in {"semantic", "fused"} else None
    fields = {
        "doc_id": doc_id,
        "chunk_index": 0,
        "path": path,
        "title": doc_id,
        "status": None,
        "index_id": None,
        "trust_profile": "durable_knowledge",
        "content_hash": content_hash,
        "signal": signal,
        "rrf_score": 0.2,
        "lexical_rank": lexical,
        "semantic_rank": semantic,
        "citation_class": "citable",
        "provenance_warning": None,
        "chunk_text": "alpha beta gamma",
        "expansion_depth": 0,
        "association_depth": 0,
    }
    fields.update(overrides)
    return QueryResult(**fields)


def _open_nomination_db(tmp_path):
    conn = db.init_db(str(tmp_path / "nom.sqlite"))
    conn.execute(
        "INSERT INTO documents (id, title, path, content_hash) VALUES (?, ?, ?, ?)",
        ("d1", "Doc One", "d1.md", "hash-d1"),
    )
    conn.execute(
        "INSERT INTO documents (id, title, path, content_hash) VALUES (?, ?, ?, ?)",
        ("d2", "Doc Two", "d2.md", "hash-d2"),
    )
    conn.execute(
        "INSERT INTO chunks (doc_id, chunk_index, text) VALUES (?, ?, ?)",
        ("d1", 0, "first chunk body for doc one"),
    )
    conn.execute(
        "INSERT INTO chunks (doc_id, chunk_index, text) VALUES (?, ?, ?)",
        ("d2", 0, "second chunk body for doc two"),
    )
    conn.commit()
    return conn


def test_run_query_signature_has_no_nomination_switch():
    import inspect

    assert "nominations" not in inspect.signature(run_query).parameters


def test_projection_preserves_order_and_does_not_mutate():
    rows = [
        _row("d1", "d1.md", "hash-d1", signal="lexical"),
        _row("d2", "d2.md", "hash-d2", signal="semantic"),
        _row("d3", "d3.md", "hash-d3", signal="fused"),
    ]
    before = [r.model_dump() for r in rows]
    found = project_nominations(rows, query_text="alpha")
    assert [r.model_dump() for r in rows] == before
    assert [n.doc_id for n in found] == ["d1", "d2", "d3"]
    assert [n.signal for n in found] == ["lexical", "semantic", "fused"]


def test_nomination_ids_deterministic_and_handles_query_independent():
    rows = [_row("d1", "d1.md", "hash-d1")]
    first = project_nominations(rows, query_text="alpha")
    second = project_nominations(rows, query_text="alpha")
    other_query = project_nominations(rows, query_text="beta")
    assert first[0].nomination_id == second[0].nomination_id
    assert first[0].expansion_handle == second[0].expansion_handle
    assert first[0].nomination_id.startswith(f"{POLICY_VERSION}:")
    assert first[0].expansion_handle.startswith(f"{EXPANSION_VERSION}:")
    # Same source under a different query: new nomination, same expansion target.
    assert other_query[0].nomination_id != first[0].nomination_id
    assert other_query[0].expansion_handle == first[0].expansion_handle


def test_provenance_and_reasons_survive_projection():
    rows = [
        _row(
            "d1",
            "d1.md",
            "hash-d1",
            signal="fused",
            lexical_rank=2,
            semantic_rank=4,
            rrf_score=0.031,
            semantic_distance=0.74,
            citation_class="unverified",
            provenance_warning="UNVERIFIED — treat as a nomination",
            trust_profile="durable_knowledge",
            index_id="idx",
            namespace="ns",
            doc_type="note",
            domain="knowledge-systems",
            status="queued",
            chunk_index=3,
            chunk_text="exact source chunk " + _tokens(10),
        )
    ]
    (nom,) = project_nominations(rows, query_text="q", scope_index="knowledge")
    assert nom.kind == "source_section"
    assert nom.doc_id == "d1"
    assert nom.path == "d1.md"
    assert nom.chunk_index == 3
    assert nom.content_hash == "hash-d1"
    assert nom.citation_class == "unverified"
    assert nom.provenance_warning == "UNVERIFIED — treat as a nomination"
    assert nom.trust_profile == "durable_knowledge"
    assert nom.index_id == "idx"
    assert nom.namespace == "ns"
    assert nom.doc_type == "note"
    assert nom.domain == "knowledge-systems"
    assert nom.freshness == "UNKNOWN"
    assert nom.raw_status == "queued"
    assert nom.retrieval_reasons == ["lexical_match", "semantic_match", "fused_rank"]
    assert nom.lexical_rank == 2
    assert nom.semantic_rank == 4
    assert nom.rrf_score == 0.031
    assert nom.semantic_distance == 0.74
    assert nom.scope_index == "knowledge"
    assert nom.authority_note == "nomination_only"
    assert "source_root" not in nom.model_dump()


def test_multi_signal_reasons_not_flattened():
    lexical = project_nominations([_row("a", "a.md", "h-a", signal="lexical")], query_text="q")[0]
    semantic = project_nominations(
        [_row("b", "b.md", "h-b", signal="semantic", semantic_rank=1)], query_text="q"
    )[0]
    weak = project_nominations(
        [_row("c", "c.md", "h-c", signal="semantic", semantic_rank=1, weak_fit=True)],
        query_text="q",
    )[0]
    expanded = project_nominations(
        [_row("d", "d.md", "h-d", signal="expanded", lexical_rank=None,
              semantic_rank=None, rrf_score=0.0, expansion_depth=1)],
        query_text="q",
    )[0]
    associated = project_nominations(
        [_row("e", "e.md", "h-e", signal="associated", lexical_rank=None,
              semantic_rank=None, rrf_score=0.0, association_depth=1)],
        query_text="q",
    )[0]
    assert lexical.retrieval_reasons == ["lexical_match"]
    assert semantic.retrieval_reasons == ["semantic_match"]
    assert weak.retrieval_reasons == ["semantic_match", "weak_fit"]
    assert expanded.retrieval_reasons == ["graph_expansion"]
    assert associated.retrieval_reasons == ["semantic_association"]


def test_unknowns_stay_unknown():
    rows = [_row("d1", "d1.md", None, index_id=None, status=None)]
    (nom,) = project_nominations(rows, query_text="q")
    assert nom.content_hash is None
    assert nom.scope_index is None
    assert nom.freshness == "UNKNOWN"
    assert nom.raw_status is None
    parsed = parse_expansion_handle(nom.expansion_handle)
    assert parsed["content_hash"] is None
    assert parsed["scope"] is None


def test_preview_is_exact_extract_within_budget():
    long_text = "word " * 200
    rows = [_row("d1", "d1.md", "hash-d1", chunk_text=long_text)]
    (nom,) = project_nominations(rows, query_text="q")
    assert len(nom.preview) <= PREVIEW_CHARS
    assert nom.preview_truncated is True
    assert nom.preview_chars == len(nom.preview)
    assert "chunk_text" not in nom.model_dump()
    flat = long_text.strip().replace("\n", " ")
    assert nom.preview == flat[: PREVIEW_CHARS - 3] + "..."
    short = project_nominations(
        [_row("d1", "d1.md", "hash-d1", chunk_text="tiny")], query_text="q"
    )[0]
    assert short.preview == "tiny"
    assert short.preview_truncated is False


def test_expansion_resolves_to_intended_source(tmp_path):
    conn = _open_nomination_db(tmp_path)
    try:
        rows = [_row("d1", "d1.md", "hash-d1", chunk_text="ignored projection text")]
        (nom,) = project_nominations(rows, query_text="q")
        expanded = resolve_expansion(conn, nom.expansion_handle)
        assert expanded.doc_id == "d1"
        assert expanded.path == "d1.md"
        assert expanded.title == "Doc One"
        assert expanded.chunk_index == 0
        assert expanded.chunk_text == "first chunk body for doc one"
        assert expanded.content_hash == "hash-d1"
        assert expanded.content_hash_match is True
        assert expanded.freshness == "UNKNOWN"
        assert expanded.authority_note == "expansion_adds_context_not_authority"
    finally:
        conn.close()


def test_stale_handle_fails_closed_with_no_text(tmp_path):
    conn = _open_nomination_db(tmp_path)
    try:
        stale = encode_expansion_handle(
            scope_index=None, doc_id="d1", chunk_index=0, content_hash="different-hash"
        )
        with pytest.raises(NominationError, match="stale"):
            resolve_expansion(conn, stale)
    finally:
        conn.close()


def test_invalid_handles_fail_visibly(tmp_path):
    conn = _open_nomination_db(tmp_path)
    try:
        with pytest.raises(NominationError, match="invalid expansion handle"):
            resolve_expansion(conn, "not-a-handle")
        with pytest.raises(NominationError, match="invalid expansion handle"):
            resolve_expansion(conn, "exp1:!!!not-base64!!!")
        missing_doc = encode_expansion_handle(
            scope_index=None, doc_id="ghost", chunk_index=0, content_hash="hash-ghost"
        )
        with pytest.raises(NominationError, match="missing"):
            resolve_expansion(conn, missing_doc)
        missing_chunk = encode_expansion_handle(
            scope_index=None, doc_id="d1", chunk_index=9, content_hash="hash-d1"
        )
        with pytest.raises(NominationError, match="missing"):
            resolve_expansion(conn, missing_chunk)
    finally:
        conn.close()


def test_scope_mismatch_fails_closed(tmp_path):
    conn = _open_nomination_db(tmp_path)
    try:
        handle = encode_expansion_handle(
            scope_index="knowledge", doc_id="d1", chunk_index=0, content_hash="hash-d1"
        )
        with pytest.raises(NominationError, match="scope"):
            resolve_expansion(conn, handle, scope_index="projects")
        ok = resolve_expansion(conn, handle, scope_index="knowledge")
        assert ok.doc_id == "d1"
    finally:
        conn.close()


def test_handle_round_trip_is_deterministic():
    first = encode_expansion_handle(
        scope_index="knowledge", doc_id="d1", chunk_index=2, content_hash="hash-d1"
    )
    second = encode_expansion_handle(
        scope_index="knowledge", doc_id="d1", chunk_index=2, content_hash="hash-d1"
    )
    assert first == second
    parsed = parse_expansion_handle(first)
    assert parsed == {
        "v": EXPANSION_VERSION,
        "scope": "knowledge",
        "doc_id": "d1",
        "chunk_index": 2,
        "content_hash": "hash-d1",
    }


def _seed_cli_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_load_embedder", lambda *_a, **_k: KeywordEmbedder({"alpha": 0}))
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "alpha-one.md").write_text("alpha one body text here\n")
    (vault / "alpha-two.md").write_text("alpha two body text here\n")
    db_path = str(tmp_path / "cli.sqlite")
    db.init_db(db_path).close()
    cli._ingest_directory(vault, db_path, embedder="minilm")
    return db_path


def test_cli_legacy_list_unchanged_and_envelope_opt_in(tmp_path, monkeypatch):
    db_path = _seed_cli_db(tmp_path, monkeypatch)
    runner = CliRunner()
    legacy = runner.invoke(
        cli.app, ["query", "alpha", "--db", db_path, "--json", "--lexical-only"]
    )
    assert legacy.exit_code == 0, legacy.output
    legacy_rows = jsonlib.loads(legacy.output)
    assert isinstance(legacy_rows, list)

    default_env = runner.invoke(
        cli.app, ["query", "alpha", "--db", db_path, "--json", "--envelope", "--lexical-only"]
    )
    assert default_env.exit_code == 0, default_env.output
    default_payload = jsonlib.loads(default_env.output)
    assert "nominations" not in default_payload
    assert [r["doc_id"] for r in default_payload["results"]] == [
        r["doc_id"] for r in legacy_rows
    ]

    with_nom = runner.invoke(
        cli.app,
        ["query", "alpha", "--db", db_path, "--json", "--envelope",
         "--nominations", "--lexical-only"],
    )
    assert with_nom.exit_code == 0, with_nom.output
    payload = jsonlib.loads(with_nom.output)
    assert "results" not in payload
    assert "not_citable" not in payload
    assert len(payload["nominations"]) == len(legacy_rows)
    assert all("chunk_text" not in item for item in payload["nominations"])
    assert '"chunk_text"' not in jsonlib.dumps(payload)
    assert payload["nominations"][0]["expansion_handle"].startswith("exp1:")
    assert payload["nominations"][0]["nomination_id"].startswith("nom1:")

    gated = runner.invoke(cli.app, ["query", "alpha", "--db", db_path, "--json", "--nominations"])
    assert gated.exit_code == 1


def test_cli_expand_nomination_round_trip_and_stale(tmp_path, monkeypatch):
    db_path = _seed_cli_db(tmp_path, monkeypatch)
    runner = CliRunner()
    out = runner.invoke(
        cli.app,
        ["query", "alpha", "--db", db_path, "--json", "--envelope",
         "--nominations", "--lexical-only"],
    )
    assert out.exit_code == 0, out.output
    handle = jsonlib.loads(out.output)["nominations"][0]["expansion_handle"]

    expanded = runner.invoke(
        cli.app, ["expand-nomination", handle, "--db", db_path, "--json"]
    )
    assert expanded.exit_code == 0, expanded.output
    body = jsonlib.loads(expanded.output)
    assert body["chunk_text"]
    assert body["freshness"] == "UNKNOWN"

    stale = encode_expansion_handle(
        scope_index=None, doc_id=body["doc_id"], chunk_index=body["chunk_index"],
        content_hash="0" * 64,
    )
    failed = runner.invoke(cli.app, ["expand-nomination", stale, "--db", db_path, "--json"])
    assert failed.exit_code == 1


@pytest.mark.anyio
async def test_mcp_defaults_omit_nominations_and_opt_in_adds(tmp_path, monkeypatch):
    db_path = _seed_cli_db(tmp_path, monkeypatch)
    conn = mcp_server.open_database(db_path)
    try:
        server = mcp_server.create_server(conn, KeywordEmbedder({"alpha": 0}))
        async with create_connected_server_and_client_session(server) as session:
            default = await session.call_tool(
                "query", {"question": "alpha", "lexical_top_k": 5, "semantic_top_k": 0}
            )
            assert default.isError is False
            assert isinstance(jsonlib.loads(default.content[0].text), list)

            default_env = await session.call_tool(
                "query",
                {"question": "alpha", "lexical_top_k": 5, "semantic_top_k": 0,
                 "envelope": True},
            )
            assert default_env.isError is False
            default_env_payload = jsonlib.loads(default_env.content[0].text)
            assert "nominations" not in default_env_payload

            gated = await session.call_tool(
                "query",
                {"question": "alpha", "lexical_top_k": 5, "semantic_top_k": 0,
                 "nominations": True},
            )
            assert gated.isError is True

            with_nom = await session.call_tool(
                "query",
                {"question": "alpha", "lexical_top_k": 5, "semantic_top_k": 0,
                 "envelope": True, "nominations": True},
            )
            assert with_nom.isError is False
            payload = jsonlib.loads(with_nom.content[0].text)
            assert "results" not in payload
            assert "not_citable" not in payload
            assert len(payload["nominations"]) == len(default_env_payload["results"]) + len(
                default_env_payload.get("not_citable", [])
            )
            assert all("chunk_text" not in item for item in payload["nominations"])
            assert '"chunk_text"' not in jsonlib.dumps(payload)

            handle = payload["nominations"][0]["expansion_handle"]
            expanded = await session.call_tool(
                "expand_nomination", {"expansion_handle": handle}
            )
            assert expanded.isError is False
            body = jsonlib.loads(expanded.content[0].text)
            assert body["chunk_text"]
            assert body["freshness"] == "UNKNOWN"

            stale_handle = encode_expansion_handle(
                scope_index=None,
                doc_id=body["doc_id"],
                chunk_index=body["chunk_index"],
                content_hash="0" * 64,
            )
            stale = await session.call_tool(
                "expand_nomination", {"expansion_handle": stale_handle}
            )
            assert stale.isError is True

            stale = await session.call_tool(
                "expand_nomination", {"expansion_handle": "exp1:bm90LWpzb24="}
            )
            assert stale.isError is True
    finally:
        conn.close()


@pytest.mark.anyio
async def test_shared_mcp_nominations_and_scoped_expansion(tmp_path, monkeypatch):
    db_path = _seed_cli_db(tmp_path, monkeypatch)
    conn = mcp_server.open_database(db_path)
    try:
        server = mcp_server.create_shared_server(
            {
                "knowledge": (conn, "durable_knowledge"),
                "projects": (conn, "project_status"),
            },
            KeywordEmbedder({"alpha": 0}),
        )
        async with create_connected_server_and_client_session(server) as session:
            default = await session.call_tool(
                "query", {"question": "alpha", "scope": "knowledge"}
            )
            assert default.isError is False
            default_payload = jsonlib.loads(default.content[0].text)
            assert "nominations" not in default_payload
            assert "results" in default_payload

            with_nom = await session.call_tool(
                "query",
                {"question": "alpha", "scope": "knowledge", "nominations": True},
            )
            assert with_nom.isError is False
            payload = jsonlib.loads(with_nom.content[0].text)
            assert payload["scope"] == "knowledge"
            assert "results" not in payload
            assert len(payload["nominations"]) == len(default_payload["results"])
            assert all("chunk_text" not in item for item in payload["nominations"])
            assert '"chunk_text"' not in jsonlib.dumps(payload)

            handle = payload["nominations"][0]["expansion_handle"]
            expanded = await session.call_tool(
                "expand_nomination",
                {"expansion_handle": handle, "scope": "knowledge"},
            )
            assert expanded.isError is False
            body = jsonlib.loads(expanded.content[0].text)
            assert body["chunk_text"]
            assert body["scope"] == "knowledge"

            wrong_scope = await session.call_tool(
                "expand_nomination",
                {"expansion_handle": handle, "scope": "projects"},
            )
            assert wrong_scope.isError is True
    finally:
        conn.close()


def test_nomination_error_is_mindgraph_error():
    assert issubclass(NominationError, MindgraphError)


def test_projection_never_embeds_full_chunk_text():
    rows = [_row("d1", "d1.md", "hash-d1", chunk_text="full " * 100)]
    (nom,) = project_nominations(rows, query_text="q")
    dumped = jsonlib.dumps(nom.model_dump())
    assert ("full " * 100).strip() not in dumped
    assert nom.preview in dumped
