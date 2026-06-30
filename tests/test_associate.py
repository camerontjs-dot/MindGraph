"""Phase 9 — semantic association tests (ADR-034)."""

import pytest

from mindgraph import cli, db, parser
from mindgraph.query import associate_results, run_query


class KeywordEmbedder:
    def __init__(self, keyword_to_dim: dict[str, int], dims: int = 384):
        self.keyword_to_dim = {k.lower(): v for k, v in keyword_to_dim.items()}
        self.dims = dims

    def encode(self, texts, convert_to_numpy=True):
        import numpy as np

        out = np.zeros((len(texts), self.dims), dtype=np.float32)
        for i, text in enumerate(texts):
            lower = text.lower()
            for kw, dim in self.keyword_to_dim.items():
                if kw in lower:
                    out[i, dim] += 1.0
        return out


@pytest.fixture
def associate_embedder():
    return KeywordEmbedder({"gamma": 0, "beta": 1, "zzunique": 2})


@pytest.fixture
def associate_db(tmp_path, monkeypatch, associate_embedder):
    monkeypatch.setattr(cli, "_load_embedder", lambda *_a, **_k: associate_embedder)

    notes = tmp_path / "vault"
    notes.mkdir()
    (notes / "seed.md").write_text(
        "# Seed\nzzunique anchor. gamma protocol overview. [[linked]] (refers).\n"
    )
    (notes / "neighbor.md").write_text(
        "# Neighbor\ngamma protocol details without explicit link.\n"
    )
    (notes / "linked.md").write_text("# Linked\nbeta content only.\n")

    db_path = str(tmp_path / "test.sqlite")
    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)
    return db_path


class TestAssociate:
    def test_associate_finds_semantic_neighbor_not_in_seeds(
        self, associate_db, associate_embedder
    ):
        conn = db.get_db(associate_db)
        try:
            fused = run_query(
                conn,
                "zzunique",
                associate_embedder,
                final_top_k=1,
            )
            assert len(fused) == 1
            assert fused[0].path.endswith("seed.md")

            associated = associate_results(
                conn,
                fused,
                associate_embedder,
                top_k=5,
                seed_k=1,
            )
            paths = [r.path for r in associated]
            assert any(p.endswith("neighbor.md") for p in paths)
            assert all(r.signal == "associated" for r in associated)
            assert all(r.association_depth == 1 for r in associated)
            assert all(r.rrf_score == 0.0 for r in associated)
        finally:
            conn.close()

    def test_associate_cli_flag(self, associate_db):
        from typer.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            [
                "query",
                "zzunique",
                "--db",
                associate_db,
                "--associate",
                "--top-k",
                "1",
                "--associate-top-k",
                "5",
                "--json",
            ],
        )
        assert result.exit_code == 0
        import json

        rows = json.loads(result.stdout)
        signals = {row["signal"] for row in rows}
        assert "fused" in signals or "lexical" in signals
        assert "associated" in signals

    def test_expand_and_associate_are_distinct(self, associate_db, associate_embedder):
        conn = db.get_db(associate_db)
        try:
            rows = run_query(
                conn,
                "zzunique",
                associate_embedder,
                final_top_k=1,
                expand=True,
                expand_top_k=5,
                associate=True,
                associate_top_k=5,
            )
            expanded = [r for r in rows if r.signal == "expanded"]
            associated = [r for r in rows if r.signal == "associated"]
            assert expanded
            assert associated
            assert any(r.path.endswith("linked.md") for r in expanded)
            assert any(r.path.endswith("neighbor.md") for r in associated)
        finally:
            conn.close()