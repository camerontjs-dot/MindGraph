"""Phase 4 — example-vault retrieval smoke (CI-safe).

Asserts the expected retrieval surface against the committed
`examples/example-vault/` vault using the deterministic `KeywordEmbedder`
stub from `tests/test_query.py`. The real-model captures in the asset README
come from `scripts/run_example_smoke.py`, which uses
`sentence-transformers/all-MiniLM-L6-v2`; this test does not require network
access.

Topology (locked in DECISIONS.md § 2026-05-21 — Phase 4 public packaging):

    feedback-loops.md       → [[reinforcing-loops]] (illustrates),
                              [[balancing-loops]] (illustrates)
    reinforcing-loops.md    → [[systems-archetypes]] (related)
    balancing-loops.md      → [[systems-archetypes]] (related)
    systems-archetypes.md   → [[unicycle-mental-model]] (cites, dangling)
    mental-models-overview.md → [[feedback-loops]] (related)
    bounded-rationality.md  → [[feedback-loops]] (related)
    unrelated-noise.md      → (no edges, off-theme)
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

from mindgraph import cli, db, parser
from mindgraph.query import list_neighbors, run_query


class KeywordEmbedder:
    """Same deterministic embedder pattern as tests/test_query.py and tests/test_expand.py."""

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


EXAMPLE_VAULT_DIR = (
    Path(__file__).resolve().parent.parent / "examples" / "example-vault"
)


@pytest.fixture
def example_embedder():
    """Sparse KeywordEmbedder for the committed example vault.

    Mapping rationale:
    - `circular` → dim 0: unique to feedback-loops.md. Lets the expansion test
      scope Phase 2 to the seed cleanly via `final_top_k=1`.
    - `satisfic` and `heuristic` → dim 1: `satisfic` is a substring of
      `satisficing` and `satisficer` in bounded-rationality.md; `heuristic`
      appears in no doc. A query for `heuristic` therefore has zero lexical
      hits and pulls bounded-rationality on the semantic side only, isolating
      the semantic-only path.
    - `balancing` → dim 2: hits balancing-loops.md most heavily plus several
      other systems docs. Exercises the fused signal on balancing-loops.
    """
    return KeywordEmbedder(
        {
            "circular": 0,
            "satisfic": 1,
            "heuristic": 1,
            "balancing": 2,
        }
    )


@pytest.fixture
def example_db(tmp_path, monkeypatch, example_embedder):
    """Copy the committed example vault into tmp_path and ingest it.

    Copying into tmp_path keeps the test self-contained and matches the
    cli._ingest_directory contract (reads from a real directory tree).
    """
    monkeypatch.setattr(cli, "_load_embedder", lambda: example_embedder)

    vault_copy = tmp_path / "example-vault"
    shutil.copytree(EXAMPLE_VAULT_DIR, vault_copy)

    db_path = str(tmp_path / "test.sqlite")
    db.init_db(db_path).close()
    cli._ingest_directory(vault_copy, db_path)
    return db_path


class TestExampleVaultRetrievalPaths:
    """One test per retrieval path the example vault is designed to exercise.

    For paths where the KeywordEmbedder behavior on a small vault is fuzzy
    (zero-embedding docs come back from sqlite-vec's KNN at distance 1.0),
    the test asserts presence rather than a specific signal label. The
    semantic-only test has a controlled mapping so it asserts the signal.
    """

    def test_unique_lexical_keyword_finds_mental_models_doc(
        self, example_db, example_embedder
    ):
        """`antinet` is unique to mental-models-overview.md.

        FTS5 matches that one doc only; the keyword is not in the embedder
        mapping, so the semantic signal is fuzzy. The test asserts the doc
        lands at the top of the fused result rather than asserting a
        particular signal label.
        """
        conn = db.get_db(example_db)
        try:
            results = run_query(
                conn, "antinet", example_embedder, final_top_k=5
            )
        finally:
            conn.close()

        target = _doc_id("mental-models-overview.md")
        doc_ids = [r.doc_id for r in results]
        assert target in doc_ids
        assert results[0].doc_id == target

    def test_semantic_synonym_finds_bounded_rationality(
        self, example_db, example_embedder
    ):
        """`heuristic` has zero lexical hits across the vault.

        The embedder maps `heuristic` and `satisfic` to the same dim.
        bounded-rationality.md contains `satisficing` and `satisficer`
        (both match the substring `satisfic`), so it is the only doc with
        non-zero activation on that dim. The signal must be `semantic`
        because the lexical ranking is empty.
        """
        conn = db.get_db(example_db)
        try:
            results = run_query(
                conn, "heuristic", example_embedder, final_top_k=5
            )
        finally:
            conn.close()

        by_id = {r.doc_id: r for r in results}
        target = _doc_id("bounded-rationality.md")
        assert target in by_id
        assert by_id[target].signal == "semantic"
        assert by_id[target].lexical_rank is None
        assert by_id[target].semantic_rank is not None

    def test_fused_query_lands_on_balancing_loops(
        self, example_db, example_embedder
    ):
        """`balancing` matches several docs both lexically and semantically.

        balancing-loops.md has the heaviest activation and the most lexical
        occurrences, so it carries the fused signal in the result set.
        """
        conn = db.get_db(example_db)
        try:
            results = run_query(
                conn, "balancing", example_embedder, final_top_k=5
            )
        finally:
            conn.close()

        by_id = {r.doc_id: r for r in results}
        target = _doc_id("balancing-loops.md")
        assert target in by_id
        assert by_id[target].signal == "fused"
        assert by_id[target].lexical_rank is not None
        assert by_id[target].semantic_rank is not None


class TestExampleVaultGraphSurface:
    """Asserts the graph topology committed in the example vault."""

    def test_dangling_edge_preserved_on_systems_archetypes(self, example_db):
        """systems-archetypes.md links to unicycle-mental-model.md (not present).

        list_neighbors must return the row with target_path=None so the CLI
        can surface broken links rather than silently dropping them.
        """
        conn = db.get_db(example_db)
        try:
            neighbors = list_neighbors(
                conn, _doc_id("systems-archetypes.md")
            )
        finally:
            conn.close()

        dangling = [
            n
            for n in neighbors
            if n.target_id == _doc_id("unicycle-mental-model.md")
        ]
        assert len(dangling) == 1
        assert dangling[0].target_path is None
        assert dangling[0].relationship_type == "cites"

    def test_seed_has_two_illustrates_edges(self, example_db):
        """feedback-loops.md links to both one-hop targets with `illustrates`."""
        conn = db.get_db(example_db)
        try:
            neighbors = list_neighbors(conn, _doc_id("feedback-loops.md"))
        finally:
            conn.close()

        target_ids = {n.target_id for n in neighbors}
        assert _doc_id("reinforcing-loops.md") in target_ids
        assert _doc_id("balancing-loops.md") in target_ids
        assert all(n.relationship_type == "illustrates" for n in neighbors)
        assert all(n.target_path is not None for n in neighbors)


class TestExampleVaultGraphExpansion:
    """Exercises the Phase 3 expansion path against the committed vault."""

    def test_expansion_walks_seed_two_hops_skipping_dangling(
        self, example_db, example_embedder
    ):
        """Seed query `circular` returns feedback-loops at depth 0.

        --depth 2 reaches reinforcing-loops and balancing-loops at depth 1
        (the seed's two outbound edges) and systems-archetypes at depth 2
        (reached through both one-hop docs; the BFS deduplicates). The
        dangling unicycle-mental-model target is skipped by the walk.

        final_top_k=1 scopes Phase 2 to the seed only, matching the
        convention from tests/test_expand.py.
        """
        conn = db.get_db(example_db)
        try:
            results = run_query(
                conn,
                "circular",
                example_embedder,
                final_top_k=1,
                expand=True,
                expand_depth=2,
            )
        finally:
            conn.close()

        by_id = {r.doc_id: r for r in results}

        seed = _doc_id("feedback-loops.md")
        assert seed in by_id
        assert by_id[seed].expansion_depth == 0

        reinforcing = _doc_id("reinforcing-loops.md")
        balancing = _doc_id("balancing-loops.md")
        assert by_id[reinforcing].signal == "expanded"
        assert by_id[reinforcing].expansion_depth == 1
        assert by_id[balancing].signal == "expanded"
        assert by_id[balancing].expansion_depth == 1

        archetypes = _doc_id("systems-archetypes.md")
        assert by_id[archetypes].signal == "expanded"
        assert by_id[archetypes].expansion_depth == 2

        assert _doc_id("unicycle-mental-model.md") not in by_id

    def test_expansion_excludes_unrelated_noise(
        self, example_db, example_embedder
    ):
        """unrelated-noise.md has no inbound edge from any seed-reachable doc."""
        conn = db.get_db(example_db)
        try:
            results = run_query(
                conn,
                "circular",
                example_embedder,
                final_top_k=1,
                expand=True,
                expand_depth=3,
            )
        finally:
            conn.close()

        assert _doc_id("unrelated-noise.md") not in {
            r.doc_id for r in results
        }
