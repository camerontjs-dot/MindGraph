"""Phase 3 — graph traversal expansion tests.

Covers the seven ADR exit-measurement scenarios in DECISIONS.md
§ 2026-05-20 — Phase 3 graph expansion. Fixture topology:

    A.md  → [[B]] (cites), [[E]] (kind), [[missing-thing]] (refers, dangling)
    B.md  → [[C]] (refers)
    C.md  → (no outbound)
    D.md  → (no edges, unrelated)
    E.md  → (no outbound)

A contains the token `alpha`. A and B both contain the token `beta`, which
exercises the dedup scenario (Phase 2 returns both A and B; the walk from A
must not re-add B).
"""

import json as jsonlib

import numpy as np
import pytest
from typer.testing import CliRunner

from mindgraph import cli, db, parser
from mindgraph.models import QueryResult
from mindgraph.query import expand_results, run_query


class KeywordEmbedder:
    """Same deterministic embedder pattern as tests/test_query.py."""

    def __init__(self, keyword_to_dim: dict[str, int], dims: int = 384):
        self.keyword_to_dim = {k.lower(): v for k, v in keyword_to_dim.items()}
        self.dims = dims

    def encode(self, texts, convert_to_numpy=True):
        out = np.zeros((len(texts), self.dims), dtype=np.float32)
        for i, text in enumerate(texts):
            lower = text.lower()
            for kw, dim in self.keyword_to_dim.items():
                if kw in lower:
                    out[i, dim] += 1.0
        return out


def _doc_id(rel_path: str) -> str:
    return parser.compute_doc_id(rel_path)


@pytest.fixture
def expand_embedder():
    return KeywordEmbedder({"alpha": 0, "beta": 1})


@pytest.fixture
def expand_db(tmp_path, monkeypatch, expand_embedder):
    """Ingest the ABCDE fixture vault."""
    monkeypatch.setattr(cli, "_load_embedder", lambda: expand_embedder)

    notes = tmp_path / "vault"
    notes.mkdir()
    (notes / "A.md").write_text(
        "Doc A talks about alpha and beta. "
        "Refers to [[B]] (cites), [[E]] (kind), "
        "and [[missing-thing]] (refers).\n"
    )
    (notes / "B.md").write_text(
        "Doc B holds beta content. Links to [[C]] (refers).\n"
    )
    (notes / "C.md").write_text("Doc C has no outbound edges.\n")
    (notes / "D.md").write_text("Doc D is unrelated.\n")
    (notes / "E.md").write_text("Doc E has no outbound edges.\n")

    db_path = str(tmp_path / "test.sqlite")
    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)
    return db_path


class TestExpand:
    def test_depth_1_walks_one_hop(self, expand_db, expand_embedder):
        """ADR scenario 1: A in Phase 2 (depth=0); B and E in expanded (depth=1).

        The dangling [[missing-thing]] target and the unrelated D are absent.
        C is not reached at depth=1.

        final_top_k=1 scopes Phase 2 to A only. With a tiny vault, sqlite-vec's
        KNN happily returns zero-embedding docs at distance 1.0, so default
        top-k would otherwise pull C/D/E into the Phase 2 block as semantic-only
        hits and the walk would find nothing new.
        """
        conn = db.get_db(expand_db)
        try:
            results = run_query(
                conn,
                "alpha",
                expand_embedder,
                final_top_k=1,
                expand=True,
                expand_depth=1,
            )
        finally:
            conn.close()

        by_id = {r.doc_id: r for r in results}
        assert _doc_id("A.md") in by_id
        assert by_id[_doc_id("A.md")].expansion_depth == 0
        assert by_id[_doc_id("A.md")].signal in ("lexical", "semantic", "fused")

        assert _doc_id("B.md") in by_id
        assert by_id[_doc_id("B.md")].signal == "expanded"
        assert by_id[_doc_id("B.md")].expansion_depth == 1

        assert _doc_id("E.md") in by_id
        assert by_id[_doc_id("E.md")].signal == "expanded"
        assert by_id[_doc_id("E.md")].expansion_depth == 1

        assert _doc_id("C.md") not in by_id
        assert _doc_id("D.md") not in by_id
        assert _doc_id("missing-thing.md") not in by_id

    def test_depth_2_walks_two_hops(self, expand_db, expand_embedder):
        """ADR scenario 2: depth=2 reaches C through B."""
        conn = db.get_db(expand_db)
        try:
            results = run_query(
                conn,
                "alpha",
                expand_embedder,
                final_top_k=1,
                expand=True,
                expand_depth=2,
            )
        finally:
            conn.close()

        by_id = {r.doc_id: r for r in results}
        assert by_id[_doc_id("B.md")].expansion_depth == 1
        assert by_id[_doc_id("E.md")].expansion_depth == 1
        assert _doc_id("C.md") in by_id
        assert by_id[_doc_id("C.md")].signal == "expanded"
        assert by_id[_doc_id("C.md")].expansion_depth == 2

    def test_no_expand_matches_phase_2(self, expand_db, expand_embedder):
        """ADR scenario 3: without --expand and with depth=0 both equal Phase 2."""
        conn = db.get_db(expand_db)
        try:
            phase_2 = run_query(conn, "alpha", expand_embedder, final_top_k=1)
            no_expand = run_query(
                conn,
                "alpha",
                expand_embedder,
                final_top_k=1,
                expand=False,
                expand_depth=1,
            )
            zero_depth = run_query(
                conn,
                "alpha",
                expand_embedder,
                final_top_k=1,
                expand=True,
                expand_depth=0,
            )
        finally:
            conn.close()

        assert [r.doc_id for r in no_expand] == [r.doc_id for r in phase_2]
        assert [r.doc_id for r in zero_depth] == [r.doc_id for r in phase_2]
        assert all(r.expansion_depth == 0 for r in phase_2)

    def test_dedup_when_phase_2_already_contains_walk_target(
        self, expand_db, expand_embedder
    ):
        """ADR scenario 6: a walk target already in Phase 2 keeps its Phase 2 signal.

        Query 'beta' matches both A and B lexically. With final_top_k=2 both
        come back from Phase 2. The walk from A would normally add B at
        depth=1, but B is already in seen. Assert no duplicate and B keeps the
        Phase 2 signal.
        """
        conn = db.get_db(expand_db)
        try:
            results = run_query(
                conn,
                "beta",
                expand_embedder,
                final_top_k=2,
                expand=True,
                expand_depth=1,
            )
        finally:
            conn.close()

        b_rows = [r for r in results if r.doc_id == _doc_id("B.md")]
        assert len(b_rows) == 1
        assert b_rows[0].signal in ("lexical", "semantic", "fused")
        assert b_rows[0].expansion_depth == 0

        a_rows = [r for r in results if r.doc_id == _doc_id("A.md")]
        assert len(a_rows) == 1
        assert a_rows[0].expansion_depth == 0

    def test_expand_top_k_caps_appended_results(
        self, expand_db, expand_embedder
    ):
        """ADR scenario 7: --expand-top-k 1 with multiple walked neighbors keeps one."""
        conn = db.get_db(expand_db)
        try:
            results = run_query(
                conn,
                "alpha",
                expand_embedder,
                final_top_k=1,
                expand=True,
                expand_depth=1,
                expand_top_k=1,
            )
        finally:
            conn.close()

        expanded = [r for r in results if r.signal == "expanded"]
        assert len(expanded) == 1
        assert expanded[0].expansion_depth == 1
        assert expanded[0].doc_id in {_doc_id("B.md"), _doc_id("E.md")}

    def test_dangling_edge_does_not_appear_as_expanded(
        self, expand_db, expand_embedder
    ):
        """ADR invariant: dangling edges terminate the walk; no QueryResult row."""
        conn = db.get_db(expand_db)
        try:
            results = run_query(
                conn,
                "alpha",
                expand_embedder,
                final_top_k=1,
                expand=True,
                expand_depth=3,
            )
        finally:
            conn.close()

        doc_ids = {r.doc_id for r in results}
        assert _doc_id("missing-thing.md") not in doc_ids
        for r in results:
            assert r.path != ""
            assert r.path is not None

    def test_unrelated_doc_not_walked(self, expand_db, expand_embedder):
        """ADR scenario 1 corollary: D has no inbound edge from any Phase 2 seed."""
        conn = db.get_db(expand_db)
        try:
            results = run_query(
                conn,
                "alpha",
                expand_embedder,
                final_top_k=1,
                expand=True,
                expand_depth=3,
            )
        finally:
            conn.close()

        assert _doc_id("D.md") not in {r.doc_id for r in results}


class TestExpandResults:
    """Direct unit tests for the expand_results primitive."""

    def test_empty_seed_list_returns_empty(self, expand_db):
        conn = db.get_db(expand_db)
        try:
            assert expand_results(conn, [], depth=2, expand_top_k=20) == []
        finally:
            conn.close()

    def test_zero_depth_returns_empty(self, expand_db, expand_embedder):
        conn = db.get_db(expand_db)
        try:
            phase_2 = run_query(conn, "alpha", expand_embedder, final_top_k=1)
            expanded = expand_results(conn, phase_2, depth=0, expand_top_k=20)
        finally:
            conn.close()
        assert expanded == []

    def test_expanded_results_sorted_by_depth_then_doc_id(
        self, expand_db, expand_embedder
    ):
        """Sort key: (expansion_depth ASC, doc_id ASC, chunk_index ASC)."""
        conn = db.get_db(expand_db)
        try:
            phase_2 = run_query(conn, "alpha", expand_embedder, final_top_k=1)
            expanded = expand_results(conn, phase_2, depth=2, expand_top_k=20)
        finally:
            conn.close()

        keys = [(r.expansion_depth, r.doc_id, r.chunk_index) for r in expanded]
        assert keys == sorted(keys)


class TestExpandCLI:
    def test_expand_flag_appends_depth_to_header(
        self, expand_db, expand_embedder, monkeypatch
    ):
        monkeypatch.setattr(cli, "_load_embedder", lambda: expand_embedder)
        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            [
                "query",
                "alpha",
                "--db",
                expand_db,
                "--top-k",
                "1",
                "--expand",
                "--depth",
                "1",
            ],
        )
        assert result.exit_code == 0
        assert "signal=expanded" in result.stdout
        assert "depth=1" in result.stdout

    def test_expand_flag_emits_expansion_depth_in_json(
        self, expand_db, expand_embedder, monkeypatch
    ):
        monkeypatch.setattr(cli, "_load_embedder", lambda: expand_embedder)
        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            [
                "query",
                "alpha",
                "--db",
                expand_db,
                "--top-k",
                "1",
                "--expand",
                "--depth",
                "1",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = jsonlib.loads(result.stdout)
        assert isinstance(data, list)
        assert all("expansion_depth" in row for row in data)
        expanded_rows = [r for r in data if r["signal"] == "expanded"]
        assert expanded_rows
        for row in expanded_rows:
            assert row["expansion_depth"] >= 1
            assert row["lexical_rank"] is None
            assert row["semantic_rank"] is None
            assert row["rrf_score"] == 0.0

    def test_depth_above_cap_rejected(self, expand_db, expand_embedder, monkeypatch):
        """Hard cap of 3 enforced at the CLI layer."""
        monkeypatch.setattr(cli, "_load_embedder", lambda: expand_embedder)
        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            ["query", "alpha", "--db", expand_db, "--expand", "--depth", "4"],
        )
        assert result.exit_code != 0
