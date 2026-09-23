"""Bounded typed graph-admission projection.

These tests lock the #11 contract. The rescue case is a public structural
analogue of the frozen RC1 graph-only leaf: absent from the fused cutoff,
present as depth-1 expanded output, admitted once. It is not the private
RC1 corpus.
"""

import hashlib
import inspect
import json as jsonlib

import pytest
from typer.testing import CliRunner

from mcp.shared.memory import create_connected_server_and_client_session

from mindgraph import cli, db, mcp_server
from mindgraph.graph_admission import (
    POLICY_VERSION,
    TOKEN_LIMIT,
    admission_id,
    identity_payload,
    measured_chunk_tokens,
    project_graph_admissions,
)
from mindgraph.models import QueryResult
from mindgraph.query import run_query
from tests.test_query import KeywordEmbedder


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _tokens(count: int) -> str:
    return " ".join(["tok"] * count)


def _base(doc_id, path, content_hash, **overrides) -> QueryResult:
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
        "chunk_text": "alpha",
        "expansion_depth": 0,
        "association_depth": 0,
    }
    fields.update(overrides)
    return QueryResult(**fields)


def _expanded(doc_id, path, content_hash, text="leaf body", **overrides) -> QueryResult:
    overrides.setdefault("signal", "expanded")
    overrides.setdefault("rrf_score", 0.0)
    overrides.setdefault("lexical_rank", None)
    overrides.setdefault("semantic_rank", None)
    overrides.setdefault("chunk_text", text)
    overrides.setdefault("expansion_depth", 1)
    return _base(doc_id, path, content_hash, **overrides)


def _open_db(tmp_path, edges):
    conn = db.init_db(str(tmp_path / "edges.sqlite"))
    documents = {
        "s1": "s1.md",
        "s2": "s2.md",
        "s3": "s3.md",
        "s4": "s4.md",
        "leaf": "leaf.md",
        "leaf2": "leaf2.md",
        "other": "other.md",
    }
    for doc_id, path in documents.items():
        conn.execute(
            """
            INSERT INTO documents (id, title, path, content_hash)
            VALUES (?, ?, ?, ?)
            """,
            (doc_id, doc_id, path, f"hash-{doc_id}"),
        )
    for source_id, target_id, relationship in edges:
        conn.execute(
            """
            INSERT INTO edges (source_id, target_id, relationship_type)
            VALUES (?, ?, ?)
            """,
            (source_id, target_id, relationship),
        )
    conn.commit()
    return conn


def _admit(conn, results, k=3, query="alpha", scope=None):
    before = [row.model_dump() for row in results]
    found = project_graph_admissions(
        conn, results, query_text=query, k=k, scope_index=scope
    )
    assert [row.model_dump() for row in results] == before
    return found


def _prefix_and_leaf():
    prefix = [
        _base("s1", "s1.md", "hash-s1"),
        _base("s2", "s2.md", "hash-s2", signal="lexical"),
        _base("s3", "s3.md", "hash-s3", signal="semantic"),
    ]
    leaf = _expanded("leaf", "leaf.md", "hash-leaf")
    return prefix, leaf


def test_measured_tokens_match_split():
    text = "one two\nthree"
    assert measured_chunk_tokens(text) == len(text.split()) == 3


def test_run_query_signature_has_no_admission_switch():
    assert "graph_admission" not in inspect.signature(run_query).parameters


def test_no_eligible_candidate(tmp_path):
    conn = _open_db(tmp_path, [])
    prefix, _leaf = _prefix_and_leaf()
    try:
        assert _admit(conn, prefix + [_expanded("leaf", "leaf.md", "hash-leaf")]) == []
    finally:
        conn.close()


def test_one_eligible_candidate_is_graph_derived_without_fused_rank(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    prefix, leaf = _prefix_and_leaf()
    results = prefix + [leaf]
    try:
        found = _admit(conn, results)
    finally:
        conn.close()
    assert len(found) == 1
    admission = found[0]
    dumped = admission.model_dump()
    assert dumped["kind"] == "graph_derived"
    assert dumped["freshness"] == "UNKNOWN"
    assert dumped["raw_status"] is None
    assert dumped["result"] == leaf.model_dump()
    assert dumped["result"]["signal"] == "expanded"
    assert dumped["result"]["expansion_depth"] == 1
    assert dumped["result"]["lexical_rank"] is None
    assert dumped["result"]["semantic_rank"] is None
    assert dumped["result"]["rrf_score"] == 0.0
    assert "fused_rank" not in dumped
    assert "source_root" not in dumped["result"]
    assert dumped["seed_doc_id"] == "s1"
    assert dumped["seed_position"] == 1
    assert dumped["relationship_type"] == "authored"
    assert dumped["edge_source_path"] == "s1.md"
    assert dumped["edge_target_path"] == "leaf.md"
    assert dumped["token_limit"] == TOKEN_LIMIT
    assert admission.admission_id.startswith(f"{POLICY_VERSION}:")


def test_multiple_candidates_keep_first_passing_and_only_one(tmp_path):
    conn = _open_db(
        tmp_path,
        [("s1", "leaf", "authored"), ("s1", "leaf2", "later")],
    )
    prefix, leaf = _prefix_and_leaf()
    second = _expanded("leaf2", "leaf2.md", "hash-leaf2", text="second leaf")
    try:
        found = _admit(conn, prefix + [leaf, second])
    finally:
        conn.close()
    assert len(found) == 1
    assert found[0].result.doc_id == "leaf"


def test_over_budget_candidate_is_skipped_for_the_next_one(tmp_path):
    conn = _open_db(
        tmp_path,
        [("s1", "leaf", "authored"), ("s1", "leaf2", "later")],
    )
    prefix, _leaf = _prefix_and_leaf()
    too_long = _expanded("leaf", "leaf.md", "hash-leaf", text=_tokens(51))
    short = _expanded("leaf2", "leaf2.md", "hash-leaf2", text=_tokens(10))
    try:
        found = _admit(conn, prefix + [too_long, short])
    finally:
        conn.close()
    assert len(found) == 1
    assert found[0].result.doc_id == "leaf2"
    assert found[0].chunk_token_count == 10


def test_token_boundary(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", None)])
    prefix, _leaf = _prefix_and_leaf()
    try:
        accepted = _admit(
            conn,
            prefix + [_expanded("leaf", "leaf.md", "hash-leaf", text=_tokens(50))],
        )
        rejected = _admit(
            conn,
            prefix + [_expanded("leaf", "leaf.md", "hash-leaf", text=_tokens(51))],
        )
    finally:
        conn.close()
    assert len(accepted) == 1
    assert accepted[0].chunk_token_count == 50
    assert rejected == []


def test_short_output_at_or_below_k_is_not_admitted_again(tmp_path):
    """#9 rc14 shape: expanded row already inside the consumer prefix."""
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    rows = [
        _base("s1", "s1.md", "hash-s1"),
        _base("s2", "s2.md", "hash-s2"),
        _base("s3", "s3.md", "hash-s3"),
        _base("s4", "s4.md", "hash-s4"),
        _base("other", "other.md", "hash-other"),
        _expanded("leaf", "leaf.md", "hash-leaf"),
    ]
    try:
        assert len(rows) == 6
        assert _admit(conn, rows, k=10) == []
        assert _admit(conn, rows[:5], k=10) == []
    finally:
        conn.close()


def test_candidate_already_inside_prefix_is_not_duplicated(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored"), ("s1", "leaf2", None)])
    rows = [
        _base("s1", "s1.md", "hash-s1"),
        _expanded("leaf", "leaf.md", "hash-leaf"),
        _base("s2", "s2.md", "hash-s2"),
        _expanded("leaf2", "leaf2.md", "hash-leaf2", text="later leaf"),
    ]
    # K=2 keeps the first expanded row inside the prefix. leaf2 is beyond
    # that prefix, so the admission is leaf2 and the prefix row is not repeated.
    try:
        found = _admit(conn, rows, k=2)
    finally:
        conn.close()
    assert len(found) == 1
    assert found[0].result.doc_id == "leaf2"


def test_seed_outside_top_three_is_rejected(tmp_path):
    conn = _open_db(tmp_path, [("s4", "leaf", "authored")])
    rows = [
        _base("s1", "s1.md", "hash-s1"),
        _base("s2", "s2.md", "hash-s2"),
        _base("s3", "s3.md", "hash-s3"),
        _base("s4", "s4.md", "hash-s4"),
        _expanded("leaf", "leaf.md", "hash-leaf"),
    ]
    try:
        assert _admit(conn, rows, k=4) == []
    finally:
        conn.close()


def test_non_base_seed_signal_is_rejected(tmp_path):
    conn = _open_db(tmp_path, [("other", "leaf", "authored")])
    associated = _base(
        "other",
        "other.md",
        "hash-other",
        signal="associated",
        rrf_score=0.0,
        lexical_rank=None,
        semantic_rank=None,
        association_depth=1,
    )
    rows = [
        associated,
        _base("s1", "s1.md", "hash-s1"),
        _base("s2", "s2.md", "hash-s2"),
        _base("s3", "s3.md", "hash-s3"),
        _expanded("leaf", "leaf.md", "hash-leaf"),
    ]
    try:
        assert _admit(conn, rows, k=4) == []
    finally:
        conn.close()


def test_lexical_and_semantic_seeds_qualify_without_requiring_fused(tmp_path):
    conn = _open_db(tmp_path, [("s2", "leaf", "from-semantic")])
    prefix, leaf = _prefix_and_leaf()
    try:
        found = _admit(conn, prefix + [leaf])
    finally:
        conn.close()
    assert len(found) == 1
    assert found[0].seed_doc_id == "s2"
    assert found[0].seed_position == 2
    assert prefix[1].signal == "lexical" or prefix[0].signal == "fused"
    assert found[0].result.signal == "expanded"


def test_semantic_association_is_not_authored_provenance(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    prefix, _leaf = _prefix_and_leaf()
    associated = _expanded("leaf", "leaf.md", "hash-leaf")
    associated = associated.model_copy(
        update={"signal": "associated", "expansion_depth": 0, "association_depth": 1}
    )
    try:
        assert _admit(conn, prefix + [associated]) == []
        assert _admit(conn, prefix + [_expanded("leaf2", "leaf2.md", "hash-leaf2")]) == []
    finally:
        conn.close()


def test_missing_and_mismatched_provenance_fail_closed(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    prefix, leaf = _prefix_and_leaf()
    cases = [
        prefix + [leaf.model_copy(update={"content_hash": None})],
        prefix + [leaf.model_copy(update={"path": "wrong.md"})],
        prefix + [leaf.model_copy(update={"doc_id": "missing"})],
        [
            prefix[0].model_copy(update={"content_hash": None}),
            prefix[1],
            prefix[2],
            leaf,
        ],
        [
            prefix[0].model_copy(update={"path": "wrong.md"}),
            prefix[1],
            prefix[2],
            leaf,
        ],
        prefix + [leaf.model_copy(update={"doc_id": "other", "path": "other.md", "content_hash": "hash-other"})],
    ]
    try:
        for results in cases:
            assert _admit(conn, results) == []
    finally:
        conn.close()


def test_null_and_free_text_relationship_types_remain_valid(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", None), ("s1", "leaf2", "see also")])
    prefix, leaf = _prefix_and_leaf()
    try:
        null_edge = _admit(conn, prefix + [leaf])
        free_text = _admit(
            conn, prefix + [_expanded("leaf2", "leaf2.md", "hash-leaf2")]
        )
    finally:
        conn.close()
    assert len(null_edge) == 1
    assert null_edge[0].relationship_type is None
    encoded = jsonlib.dumps(
        identity_payload(
            query_text="alpha",
            scope_index=None,
            k=3,
            seed_doc_id=null_edge[0].seed_doc_id,
            seed_content_hash=null_edge[0].seed_content_hash,
            seed_position=null_edge[0].seed_position,
            candidate_doc_id=null_edge[0].result.doc_id,
            candidate_content_hash=null_edge[0].result.content_hash,
            edge_source_id=null_edge[0].edge_source_id,
            edge_target_id=null_edge[0].edge_target_id,
            edge_source_path=null_edge[0].edge_source_path,
            edge_target_path=null_edge[0].edge_target_path,
            relationship_type=None,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert '"relationship_type":null' in encoded
    assert len(free_text) == 1
    assert free_text[0].relationship_type == "see also"


def test_exact_duplicate_doc_id_and_hash_are_suppressed_but_near_hash_is_not(tmp_path):
    conn = _open_db(
        tmp_path,
        [("s2", "leaf", "authored"), ("s2", "leaf2", "authored")],
    )
    prefix, _leaf = _prefix_and_leaf()
    duplicate_id = _expanded("s1", "leaf.md", "hash-leaf")
    duplicate_hash = _expanded("leaf", "leaf.md", "hash-s1")
    near_hash = _expanded("leaf2", "leaf2.md", "hash-s1-near")
    try:
        assert _admit(conn, prefix + [duplicate_id]) == []
        assert _admit(conn, prefix + [duplicate_hash]) == []
        found = _admit(conn, prefix + [near_hash])
    finally:
        conn.close()
    assert len(found) == 1
    assert found[0].result.content_hash == "hash-s1-near"


def test_not_citable_seed_and_candidate_are_rejected(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored"), ("s2", "leaf2", "authored")])
    prefix, leaf = _prefix_and_leaf()
    blocked_seed = [
        prefix[0].model_copy(update={"citation_class": "not_citable"}),
        prefix[1],
        prefix[2],
        leaf,
    ]
    blocked_candidate = prefix + [
        leaf.model_copy(update={"citation_class": "not_citable", "status": "superseded"})
    ]
    try:
        assert _admit(conn, blocked_seed) == []
        assert _admit(conn, blocked_candidate) == []
        # The ineligible top seed does not consume the slot of a later eligible seed.
        rescued = _admit(
            conn,
            [
                prefix[0].model_copy(update={"citation_class": "not_citable"}),
                prefix[1],
                prefix[2],
                _expanded("leaf2", "leaf2.md", "hash-leaf2"),
            ],
        )
    finally:
        conn.close()
    assert len(rescued) == 1
    assert rescued[0].seed_doc_id == "s2"
    assert rescued[0].seed_position == 2


@pytest.mark.parametrize("status", ["current", "stale", "not-a-real-status", None])
def test_raw_status_stays_raw_and_freshness_stays_unknown(tmp_path, status):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    prefix, leaf = _prefix_and_leaf()
    try:
        found = _admit(conn, prefix + [leaf.model_copy(update={"status": status})])
    finally:
        conn.close()
    assert len(found) == 1
    assert found[0].freshness == "UNKNOWN"
    assert found[0].raw_status == status
    assert found[0].result.status == status


def test_unverified_candidate_preserves_warning_and_authority(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    prefix, leaf = _prefix_and_leaf()
    warning = (
        "UNVERIFIED — this capture is flagged needs-audit / full-text-pending. "
        "Treat as a nomination, not as evidence."
    )
    candidate = leaf.model_copy(
        update={
            "citation_class": "unverified",
            "provenance_warning": warning,
            "trust_profile": "durable_knowledge",
            "status": "needs-audit",
        }
    )
    try:
        found = _admit(conn, prefix + [candidate])
    finally:
        conn.close()
    assert len(found) == 1
    assert found[0].result.model_dump() == candidate.model_dump()
    assert found[0].result.citation_class == "unverified"
    assert found[0].result.provenance_warning == warning
    assert found[0].result.trust_profile == "durable_knowledge"
    assert found[0].freshness == "UNKNOWN"


def test_lowest_seed_and_neighbor_order_are_deterministic(tmp_path):
    conn = _open_db(
        tmp_path,
        [
            ("s1", "leaf", None),
            ("s1", "leaf", "zeta"),
            ("s2", "leaf", "from-two"),
        ],
    )
    prefix, leaf = _prefix_and_leaf()
    try:
        first = _admit(conn, prefix + [leaf])
        second = _admit(conn, prefix + [leaf], query="alpha")
    finally:
        conn.close()
    assert first[0].admission_id == second[0].admission_id
    assert first[0].seed_doc_id == "s1"
    assert first[0].seed_position == 1
    assert first[0].relationship_type is None


def test_admission_identity_is_canonical_and_has_no_clock(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", None)])
    prefix, leaf = _prefix_and_leaf()
    leaf = leaf.model_copy(update={"index_id": "mainframe-knowledge"})
    try:
        found = _admit(conn, prefix + [leaf], query="alpha rescue", k=3)
    finally:
        conn.close()
    admission = found[0]
    payload = {
        "policy_version": "ga1",
        "query_sha256": hashlib.sha256(b"alpha rescue").hexdigest(),
        "scope_index": "mainframe-knowledge",
        "k": 3,
        "seed_doc_id": "s1",
        "seed_content_hash": "hash-s1",
        "seed_position": 1,
        "candidate_doc_id": "leaf",
        "candidate_content_hash": "hash-leaf",
        "edge_source_id": "s1",
        "edge_target_id": "leaf",
        "edge_source_path": "s1.md",
        "edge_target_path": "leaf.md",
        "relationship_type": None,
    }
    assert identity_payload(
        query_text="alpha rescue",
        scope_index="mainframe-knowledge",
        k=3,
        seed_doc_id="s1",
        seed_content_hash="hash-s1",
        seed_position=1,
        candidate_doc_id="leaf",
        candidate_content_hash="hash-leaf",
        edge_source_id="s1",
        edge_target_id="leaf",
        edge_source_path="s1.md",
        edge_target_path="leaf.md",
        relationship_type=None,
    ) == payload
    assert admission.admission_id == admission_id(payload)
    assert set(payload) == {
        "policy_version",
        "query_sha256",
        "scope_index",
        "k",
        "seed_doc_id",
        "seed_content_hash",
        "seed_position",
        "candidate_doc_id",
        "candidate_content_hash",
        "edge_source_id",
        "edge_target_id",
        "edge_source_path",
        "edge_target_path",
        "relationship_type",
    }
    moved = dict(payload)
    moved["k"] = 4
    assert admission_id(moved) != admission.admission_id
    changed_query = dict(payload)
    changed_query["query_sha256"] = hashlib.sha256(b"other").hexdigest()
    assert admission_id(changed_query) != admission.admission_id


def test_explicit_scope_outranks_index_id(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    prefix, leaf = _prefix_and_leaf()
    leaf = leaf.model_copy(update={"index_id": "index-a"})
    try:
        found = _admit(conn, prefix + [leaf], scope="knowledge")
    finally:
        conn.close()
    payload = identity_payload(
        query_text="alpha",
        scope_index="knowledge",
        k=3,
        seed_doc_id="s1",
        seed_content_hash="hash-s1",
        seed_position=1,
        candidate_doc_id="leaf",
        candidate_content_hash="hash-leaf",
        edge_source_id="s1",
        edge_target_id="leaf",
        edge_source_path="s1.md",
        edge_target_path="leaf.md",
        relationship_type="authored",
    )
    assert found[0].admission_id == admission_id(payload)


def test_depth_two_row_does_not_block_a_later_depth_one_row(tmp_path):
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    prefix, leaf = _prefix_and_leaf()
    deeper = _expanded("leaf2", "leaf2.md", "hash-leaf2", expansion_depth=2)
    try:
        found = _admit(conn, prefix + [deeper, leaf])
    finally:
        conn.close()
    assert len(found) == 1
    assert found[0].result.doc_id == "leaf"


def _rescue_vault(tmp_path, monkeypatch):
    embedder = KeywordEmbedder({"alpharescue": 0})
    monkeypatch.setattr(cli, "_load_embedder", lambda *_args, **_kwargs: embedder)
    notes = tmp_path / "vault"
    notes.mkdir()
    # Hub is the only query hit. The leaf shares no query term, so fused
    # cutoff cannot retrieve it; the authored link is the only way in.
    (notes / "hub.md").write_text(
        "alpharescue hub note. See [[leaf]] (authored).\n",
        encoding="utf-8",
    )
    (notes / "leaf.md").write_text(
        "outbound nomination body with no query token.\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "rescue.sqlite")
    cli._ingest_directory(notes, db_path)
    return db_path, embedder


def test_run_query_order_is_unchanged_and_graph_only_leaf_is_rescued_once(
    tmp_path, monkeypatch
):
    db_path, embedder = _rescue_vault(tmp_path, monkeypatch)
    conn = db.get_db(db_path)
    try:
        fused = run_query(conn, "alpharescue", embedder, final_top_k=1)
        again = run_query(conn, "alpharescue", embedder, final_top_k=1)
        expanded = run_query(
            conn,
            "alpharescue",
            embedder,
            final_top_k=1,
            expand=True,
            expand_depth=1,
        )
        assert [row.doc_id for row in fused] == [row.doc_id for row in again]
        assert all(type(row).__name__ == "QueryResult" for row in expanded)
        assert expanded[0].path == "hub.md"
        assert all(row.path != "leaf.md" for row in fused)
        leaf_rows = [row for row in expanded if row.path == "leaf.md"]
        assert len(leaf_rows) == 1
        assert leaf_rows[0].signal == "expanded"
        assert leaf_rows[0].expansion_depth == 1
        assert leaf_rows[0].lexical_rank is None
        assert leaf_rows[0].semantic_rank is None
        before = [row.model_dump() for row in expanded]
        admitted = project_graph_admissions(
            conn, expanded, query_text="alpharescue", k=1
        )
        assert [row.model_dump() for row in expanded] == before
        assert len(admitted) == 1
        assert admitted[0].kind == "graph_derived"
        assert admitted[0].result.path == "leaf.md"
        assert admitted[0].result.model_dump() == leaf_rows[0].model_dump()
        assert admitted[0].seed_position == 1
        assert admitted[0].freshness == "UNKNOWN"
        assert project_graph_admissions(
            conn, expanded, query_text="alpharescue", k=10
        ) == []
        assert len(expanded) <= 10
    finally:
        conn.close()


def test_raw_stale_family_row_is_admitted_with_unknown_freshness(tmp_path):
    """#9 qualitative caveat: raw stale is not a normalized engine class.

    The projection admits it and reports freshness UNKNOWN. It does not invent
    a suppression rule the index cannot support. Superseded material is the
    case that citation_class already rejects.
    """
    conn = _open_db(tmp_path, [("s1", "leaf", "authored")])
    prefix, leaf = _prefix_and_leaf()
    try:
        stale = _admit(
            conn,
            prefix + [leaf.model_copy(update={"status": "stale"})],
        )
        superseded = _admit(
            conn,
            prefix
            + [
                leaf.model_copy(
                    update={"status": "superseded", "citation_class": "not_citable"}
                )
            ],
        )
    finally:
        conn.close()
    assert len(stale) == 1
    assert stale[0].raw_status == "stale"
    assert stale[0].freshness == "UNKNOWN"
    assert superseded == []


def _cli_json(args):
    result = CliRunner().invoke(cli.app, args)
    return result


def test_default_cli_list_and_envelope_omit_graph_admissions(tmp_path, monkeypatch):
    db_path, _embedder = _rescue_vault(tmp_path, monkeypatch)
    intent = str(tmp_path / "missing-intent.sqlite")
    listed = _cli_json(
        ["query", "alpharescue", "--db", db_path, "--json", "--top-k", "1", "--no-intent", "--intent-db", intent]
    )
    envelope = _cli_json(
        [
            "query",
            "alpharescue",
            "--db",
            db_path,
            "--json",
            "--envelope",
            "--top-k",
            "1",
            "--no-intent",
            "--intent-db",
            intent,
        ]
    )
    assert listed.exit_code == 0
    assert envelope.exit_code == 0
    rows = jsonlib.loads(listed.stdout)
    payload = jsonlib.loads(envelope.stdout)
    assert isinstance(rows, list)
    assert "graph_admissions" not in listed.stdout
    assert set(payload) == {
        "schema_version",
        "intent_resolution",
        "routing",
        "results",
        "not_citable",
        "citation_counts",
    }
    assert all(row["path"] != "leaf.md" for row in rows)
    conn = db.get_db(db_path)
    try:
        expected = jsonlib.loads(
            jsonlib.dumps(
                [row.model_dump() for row in run_query(conn, "alpharescue", KeywordEmbedder({"alpharescue": 0}), final_top_k=1)],
                default=str,
            )
        )
    finally:
        conn.close()
    assert rows == expected


def test_cli_graph_admission_requires_json_envelope(tmp_path, monkeypatch):
    db_path, _embedder = _rescue_vault(tmp_path, monkeypatch)
    bare = _cli_json(["query", "alpharescue", "--db", db_path, "--graph-admission"])
    json_only = _cli_json(
        ["query", "alpharescue", "--db", db_path, "--json", "--graph-admission"]
    )
    assert bare.exit_code == 1
    assert json_only.exit_code == 1
    assert "requires --json --envelope" in bare.stderr
    assert "requires --json --envelope" in json_only.stderr


def test_cli_opt_in_transport_admits_the_leaf(tmp_path, monkeypatch):
    db_path, _embedder = _rescue_vault(tmp_path, monkeypatch)
    intent = str(tmp_path / "missing-intent.sqlite")
    without_expand = _cli_json(
        [
            "query",
            "alpharescue",
            "--db",
            db_path,
            "--json",
            "--envelope",
            "--graph-admission",
            "--top-k",
            "1",
            "--no-intent",
            "--intent-db",
            intent,
        ]
    )
    opted = _cli_json(
        [
            "query",
            "alpharescue",
            "--db",
            db_path,
            "--json",
            "--envelope",
            "--graph-admission",
            "--expand",
            "--depth",
            "1",
            "--top-k",
            "1",
            "--no-intent",
            "--intent-db",
            intent,
        ]
    )
    assert without_expand.exit_code == 0
    assert opted.exit_code == 0
    quiet = jsonlib.loads(without_expand.stdout)
    payload = jsonlib.loads(opted.stdout)
    assert quiet["graph_admissions"] == []
    assert "graph_admissions" in payload
    assert len(payload["graph_admissions"]) == 1
    admission = payload["graph_admissions"][0]
    assert admission["kind"] == "graph_derived"
    assert admission["freshness"] == "UNKNOWN"
    assert admission["result"]["path"] == "leaf.md"
    assert admission["result"]["signal"] == "expanded"
    assert admission["result"]["lexical_rank"] is None
    assert admission["seed_position"] == 1
    assert "/Users/" not in opted.stdout
    assert "source_root" not in opted.stdout


def _tool_json(result):
    assert result.isError is False, getattr(result.content[0], "text", result)
    return jsonlib.loads(result.content[0].text)


@pytest.mark.anyio
async def test_default_single_mcp_array_and_envelope_are_unchanged(tmp_path, monkeypatch):
    db_path, embedder = _rescue_vault(tmp_path, monkeypatch)
    conn = mcp_server.open_database(db_path)
    server = mcp_server.create_server(conn, embedder)
    try:
        async with create_connected_server_and_client_session(server) as session:
            listed = _tool_json(
                await session.call_tool(
                    "query", {"question": "alpharescue", "final_top_k": 1, "semantic_top_k": 20}
                )
            )
            envelope = _tool_json(
                await session.call_tool(
                    "query",
                    {
                        "question": "alpharescue",
                        "final_top_k": 1,
                        "envelope": True,
                    },
                )
            )
        assert isinstance(listed, list)
        assert all("graph_admissions" not in row for row in listed)
        assert set(envelope) == {
            "schema_version",
            "intent_resolution",
            "routing",
            "results",
            "not_citable",
            "citation_counts",
        }
    finally:
        conn.close()


@pytest.mark.anyio
async def test_single_mcp_opt_in_requires_envelope_and_admits_leaf(tmp_path, monkeypatch):
    db_path, embedder = _rescue_vault(tmp_path, monkeypatch)
    conn = mcp_server.open_database(db_path)
    server = mcp_server.create_server(conn, embedder)
    try:
        async with create_connected_server_and_client_session(server) as session:
            rejected = await session.call_tool(
                "query",
                {
                    "question": "alpharescue",
                    "final_top_k": 1,
                    "graph_admission": True,
                },
            )
            payload = _tool_json(
                await session.call_tool(
                    "query",
                    {
                        "question": "alpharescue",
                        "final_top_k": 1,
                        "expand": True,
                        "expand_depth": 1,
                        "envelope": True,
                        "graph_admission": True,
                    },
                )
            )
        assert rejected.isError is True
        assert "graph_admission requires envelope=true" in rejected.content[0].text
        assert len(payload["graph_admissions"]) == 1
        assert payload["graph_admissions"][0]["kind"] == "graph_derived"
        assert payload["graph_admissions"][0]["result"]["path"] == "leaf.md"
        assert payload["graph_admissions"][0]["freshness"] == "UNKNOWN"
        assert set(payload) == {
            "schema_version",
            "intent_resolution",
            "routing",
            "results",
            "not_citable",
            "citation_counts",
            "graph_admissions",
        }
    finally:
        conn.close()


@pytest.mark.anyio
async def test_shared_mcp_default_unchanged_and_opt_in_admits_leaf(tmp_path, monkeypatch):
    db_path, embedder = _rescue_vault(tmp_path, monkeypatch)
    conn = mcp_server.open_database(db_path)
    server = mcp_server.create_shared_server(
        {"knowledge": (conn, "durable_knowledge")},
        embedder,
    )
    try:
        async with create_connected_server_and_client_session(server) as session:
            default = _tool_json(
                await session.call_tool(
                    "query",
                    {"question": "alpharescue", "scope": "knowledge", "final_top_k": 1},
                )
            )
            short = _tool_json(
                await session.call_tool(
                    "query",
                    {
                        "question": "alpharescue",
                        "scope": "knowledge",
                        "final_top_k": 10,
                        "graph_admission": True,
                    },
                )
            )
            opted = _tool_json(
                await session.call_tool(
                    "query",
                    {
                        "question": "alpharescue",
                        "scope": "knowledge",
                        "final_top_k": 1,
                        "graph_admission": True,
                    },
                )
            )
        assert set(default) == {"scope", "trust_profile", "results"}
        assert default["scope"] == "knowledge"
        assert default["trust_profile"] == "durable_knowledge"
        assert all(row["path"] != "leaf.md" for row in default["results"])
        direct = db.get_db(db_path)
        try:
            expected = [
                row.model_dump()
                for row in run_query(direct, "alpharescue", embedder, final_top_k=1)
            ]
        finally:
            direct.close()
        assert default["results"] == expected
        assert set(short) == {"scope", "trust_profile", "results", "graph_admissions"}
        assert short["graph_admissions"] == []
        assert any(row["path"] == "leaf.md" for row in short["results"])
        assert len(opted["graph_admissions"]) == 1
        admission = opted["graph_admissions"][0]
        assert admission["kind"] == "graph_derived"
        assert admission["result"]["path"] == "leaf.md"
        assert admission["result"]["signal"] == "expanded"
        assert admission["freshness"] == "UNKNOWN"
        assert opted["results"][0]["path"] == "hub.md"
        assert any(row["path"] == "leaf.md" and row["signal"] == "expanded" for row in opted["results"])
    finally:
        conn.close()
