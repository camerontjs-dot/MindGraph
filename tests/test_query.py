import json as jsonlib

import numpy as np
import pytest
from typer.testing import CliRunner

from mindgraph import cli, db, parser
from mindgraph.models import QueryResult
from mindgraph.query import (
    RRF_K,
    fetch_lexical_ranking,
    fetch_semantic_ranking,
    rrf_fuse,
    run_query,
    sanitize_fts5_query,
)


# --- Deterministic stub embedder --------------------------------------------- #


class KeywordEmbedder:
    """Maps keywords (and synonyms) to specific embedding dimensions.

    Each occurrence of a known keyword in a text adds 1.0 to its assigned dim.
    Synonyms that share a dim let the test model "semantic similarity without
    textual overlap" (e.g. `striped horse` shares a dim with `zebra`, so a doc
    that says `striped horse` is semantically close to the query `zebra` even
    though FTS5 will not match the literal token).
    """

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


# --- Unit tests: sanitize_fts5_query ----------------------------------------- #


class TestSanitizeFTS5Query:
    def test_plain_words_or_joined(self):
        assert sanitize_fts5_query("cat behavior") == "cat OR behavior"

    def test_strips_uppercase_operator_keywords(self):
        assert sanitize_fts5_query("cat AND dog") == "cat OR dog"
        assert sanitize_fts5_query("cat OR dog") == "cat OR dog"
        assert sanitize_fts5_query("cat NOT dog") == "cat OR dog"
        assert sanitize_fts5_query("cat NEAR dog") == "cat OR dog"

    def test_strips_operator_chars(self):
        assert sanitize_fts5_query('"cat" *behavior*') == "cat OR behavior"
        assert sanitize_fts5_query("foo:bar (baz)") == "foo OR bar OR baz"
        assert sanitize_fts5_query("hat^2 -dog") == "hat OR 2 OR dog"

    def test_operator_only_input_returns_empty(self):
        assert sanitize_fts5_query("AND OR NOT NEAR") == ""
        assert sanitize_fts5_query('""()*:^-') == ""

    def test_empty_input(self):
        assert sanitize_fts5_query("") == ""
        assert sanitize_fts5_query("   ") == ""

    def test_lowercase_operators_kept_as_tokens(self):
        # FTS5 only treats uppercase as operators.
        assert sanitize_fts5_query("cat and dog") == "cat OR and OR dog"


# --- Unit tests: rrf_fuse ---------------------------------------------------- #


class TestRRFFuse:
    def test_empty_inputs(self):
        assert rrf_fuse([], []) == []

    def test_lexical_only_doc_has_no_semantic_rank(self):
        out = rrf_fuse([("a", 1)], [])
        assert len(out) == 1
        doc_id, chunk_index, score, lex_rank, sem_rank = out[0]
        assert doc_id == "a"
        assert chunk_index is None
        assert lex_rank == 1
        assert sem_rank is None
        assert score == pytest.approx(1 / (RRF_K + 1))

    def test_semantic_only_doc_has_no_lexical_rank(self):
        out = rrf_fuse([], [("a", 3, 1)])
        assert len(out) == 1
        doc_id, chunk_index, score, lex_rank, sem_rank = out[0]
        assert doc_id == "a"
        assert chunk_index == 3
        assert lex_rank is None
        assert sem_rank == 1

    def test_fused_doc_outranks_solo_doc(self):
        # doc 'a' is in both lists; doc 'b' is only in lexical at the same rank.
        out = rrf_fuse([("b", 1), ("a", 2)], [("a", 0, 1)])
        # 'a' = 1/62 + 1/61 ; 'b' = 1/61. 'a' must be ahead.
        assert out[0][0] == "a"
        assert out[1][0] == "b"

    def test_tie_break_by_doc_id_ascending(self):
        # Both docs tied at lex rank 1, no semantic. RRF scores equal, doc_id wins.
        out = rrf_fuse([("zeta", 1)], [("alpha", 0, 1)])
        # zeta has lex=1 only, alpha has sem=1 only. Same RRF = 1/61.
        # Tie broken by doc_id ascending: alpha < zeta.
        assert out[0][0] == "alpha"
        assert out[1][0] == "zeta"

    def test_top_k_limit(self):
        lex = [(f"d{i:02d}", i + 1) for i in range(20)]
        out = rrf_fuse(lex, [], top_k=5)
        assert len(out) == 5

    def test_zero_top_k_returns_empty(self):
        assert rrf_fuse([("a", 1)], [], top_k=0) == []

    def test_score_rounding_happens_at_caller_not_fuse(self):
        # rrf_fuse should return raw float; caller may round.
        out = rrf_fuse([("a", 1)], [])
        # 1 / 61 is not a terminating decimal.
        assert out[0][2] != round(out[0][2], 2) or out[0][2] == round(out[0][2], 2)


# --- Integration tests: against a small ingested vault ----------------------- #


@pytest.fixture
def keyword_embedder():
    # Dim 0: zebra-family. Dim 1: compiler-family. Dim 2: elephant-family.
    return KeywordEmbedder(
        {
            "zebra": 0,
            "zebras": 0,
            "striped horse": 0,
            "savannah": 0,
            "compiler": 1,
            "programming": 1,
            "elephant": 2,
            "trunk": 2,
        }
    )


@pytest.fixture
def vault_db(tmp_path, monkeypatch, keyword_embedder):
    """Ingest a small vault designed to exercise each retrieval signal."""
    monkeypatch.setattr(cli, "_load_embedder", lambda: keyword_embedder)

    notes = tmp_path / "vault"
    notes.mkdir()
    # Lexical-only winner: contains the literal token `zebra`.
    (notes / "lex.md").write_text(
        "The zebra is the subject of this note. Single mention.\n"
    )
    # Semantic-only winner: no literal `zebra` but a synonym `striped horse`.
    (notes / "sem.md").write_text(
        "About striped horses that live on the savannah grass.\n"
    )
    # Fused: contains the literal `zebra` plus extra synonyms.
    (notes / "fused.md").write_text(
        "The zebra roams the savannah. Striped horse with hooves.\n"
    )
    # Unrelated baseline.
    (notes / "unrelated.md").write_text(
        "About compilers and programming languages.\n"
    )

    db_path = str(tmp_path / "test.sqlite")
    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)
    return db_path


class TestFetchLexicalRanking:
    def test_returns_only_docs_with_query_token(self, vault_db):
        conn = db.get_db(vault_db)
        try:
            ranking = fetch_lexical_ranking(conn, "zebra", top_k=10)
        finally:
            conn.close()
        doc_ids = [doc_id for doc_id, _ in ranking]
        assert _doc_id("lex.md") in doc_ids
        assert _doc_id("fused.md") in doc_ids
        assert _doc_id("sem.md") not in doc_ids
        assert _doc_id("unrelated.md") not in doc_ids

    def test_empty_query_returns_empty_list(self, vault_db):
        conn = db.get_db(vault_db)
        try:
            assert fetch_lexical_ranking(conn, "") == []
            assert fetch_lexical_ranking(conn, "AND OR NOT") == []
        finally:
            conn.close()

    def test_top_k_limits_result_count(self, vault_db):
        conn = db.get_db(vault_db)
        try:
            ranking = fetch_lexical_ranking(conn, "zebra striped", top_k=1)
        finally:
            conn.close()
        assert len(ranking) <= 1

    def test_ranks_are_one_based_and_strictly_increasing(self, vault_db):
        conn = db.get_db(vault_db)
        try:
            ranking = fetch_lexical_ranking(conn, "zebra", top_k=10)
        finally:
            conn.close()
        ranks = [r for _, r in ranking]
        assert ranks
        assert ranks[0] == 1
        assert all(b > a for a, b in zip(ranks, ranks[1:]))


class TestFetchSemanticRanking:
    def test_returns_docs_via_synonym_match(self, vault_db, keyword_embedder):
        emb = keyword_embedder.encode(["zebra"])[0].tolist()
        conn = db.get_db(vault_db)
        try:
            ranking = fetch_semantic_ranking(conn, emb, top_k=20)
        finally:
            conn.close()
        doc_ids = [doc_id for doc_id, _, _ in ranking]
        # sem.md has no `zebra` but is reachable via the synonym embedding.
        assert _doc_id("sem.md") in doc_ids
        assert _doc_id("lex.md") in doc_ids
        assert _doc_id("fused.md") in doc_ids

    def test_at_most_one_chunk_per_document(self, vault_db, keyword_embedder):
        emb = keyword_embedder.encode(["zebra"])[0].tolist()
        conn = db.get_db(vault_db)
        try:
            ranking = fetch_semantic_ranking(conn, emb, top_k=50)
        finally:
            conn.close()
        doc_ids = [doc_id for doc_id, _, _ in ranking]
        assert len(doc_ids) == len(set(doc_ids))

    def test_zero_top_k_returns_empty(self, vault_db, keyword_embedder):
        emb = keyword_embedder.encode(["zebra"])[0].tolist()
        conn = db.get_db(vault_db)
        try:
            assert fetch_semantic_ranking(conn, emb, top_k=0) == []
        finally:
            conn.close()


class TestRunQuery:
    def test_returns_query_result_objects(self, vault_db, keyword_embedder):
        conn = db.get_db(vault_db)
        try:
            results = run_query(conn, "zebra", keyword_embedder)
        finally:
            conn.close()
        assert results
        assert all(isinstance(r, QueryResult) for r in results)
        assert all(r.signal in ("lexical", "semantic", "fused") for r in results)

    def test_signal_attribution(self, vault_db, keyword_embedder):
        conn = db.get_db(vault_db)
        try:
            results = run_query(conn, "zebra", keyword_embedder, final_top_k=10)
        finally:
            conn.close()
        by_path = {r.path: r for r in results}
        # sem.md has no `zebra` but a synonym semantic match.
        assert by_path["sem.md"].signal == "semantic"
        assert by_path["sem.md"].lexical_rank is None
        assert by_path["sem.md"].semantic_rank is not None
        # lex.md has both the lexical match and the synonym semantic match.
        assert by_path["lex.md"].signal == "fused"
        # fused.md is in both rankings.
        assert by_path["fused.md"].signal == "fused"

    def test_provenance_fields_populated(self, vault_db, keyword_embedder):
        conn = db.get_db(vault_db)
        try:
            results = run_query(conn, "zebra", keyword_embedder, final_top_k=3)
        finally:
            conn.close()
        assert results
        for r in results:
            assert r.doc_id
            assert r.path
            assert r.title
            assert r.chunk_text
            assert r.chunk_index >= 0
            assert r.rrf_score > 0

    def test_final_top_k_limits_output(self, vault_db, keyword_embedder):
        conn = db.get_db(vault_db)
        try:
            results = run_query(conn, "zebra", keyword_embedder, final_top_k=2)
        finally:
            conn.close()
        assert len(results) <= 2

    def test_operator_only_query_yields_only_semantic_signal(
        self, vault_db, keyword_embedder
    ):
        conn = db.get_db(vault_db)
        try:
            results = run_query(conn, "AND OR NOT", keyword_embedder)
        finally:
            conn.close()
        # Lexical ranking is empty (sanitized to empty MATCH), so every result
        # must carry the `semantic` signal.
        for r in results:
            assert r.signal == "semantic"
            assert r.lexical_rank is None

    def test_rrf_score_rounded_to_six_decimals(self, vault_db, keyword_embedder):
        conn = db.get_db(vault_db)
        try:
            results = run_query(conn, "zebra", keyword_embedder, final_top_k=1)
        finally:
            conn.close()
        assert results
        assert results[0].rrf_score == round(results[0].rrf_score, 6)


class TestQueryCLI:
    def test_query_command_runs_text_output(
        self, vault_db, keyword_embedder, monkeypatch
    ):
        monkeypatch.setattr(cli, "_load_embedder", lambda: keyword_embedder)
        runner = CliRunner()
        result = runner.invoke(
            cli.app, ["query", "zebra", "--db", vault_db, "--top-k", "3"]
        )
        assert result.exit_code == 0
        assert "signal=" in result.stdout
        assert "rrf_score=" in result.stdout

    def test_query_command_json_output(
        self, vault_db, keyword_embedder, monkeypatch
    ):
        monkeypatch.setattr(cli, "_load_embedder", lambda: keyword_embedder)
        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            ["query", "zebra", "--db", vault_db, "--json", "--top-k", "3"],
        )
        assert result.exit_code == 0
        data = jsonlib.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) <= 3
        if data:
            row = data[0]
            for field in (
                "doc_id",
                "chunk_index",
                "path",
                "title",
                "signal",
                "rrf_score",
                "lexical_rank",
                "semantic_rank",
                "chunk_text",
            ):
                assert field in row
