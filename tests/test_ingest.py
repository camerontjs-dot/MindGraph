import numpy as np
import pytest

from mindgraph import cli, db


class FakeEmbedder:
    """Stand-in for sentence-transformers — returns zero-vectors of the right shape."""

    def encode(self, texts, convert_to_numpy=True):
        return np.zeros((len(texts), 384), dtype=np.float32)


@pytest.fixture
def fake_embedder(monkeypatch):
    monkeypatch.setattr(cli, "_load_embedder", lambda: FakeEmbedder())


@pytest.fixture
def sample_notes(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()

    (notes / "minimal.md").write_text("Just a body, no frontmatter.\n")

    (notes / "with-timeline.md").write_text(
        "---\n"
        "title: Project Notes\n"
        "---\n"
        "Project status and goals.\n\n"
        "Links to [[people/alice]] (lead).\n\n"
        "---\n## Timeline\n- 2026-01-01: kicked off\n"
    )

    people_dir = notes / "people"
    people_dir.mkdir()
    (people_dir / "alice.md").write_text(
        "---\ntitle: Alice\n---\nAlice is a person. Knows [[bob]] (peer).\n"
    )

    return notes


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.sqlite")


def test_ingest_end_to_end(sample_notes, db_path, fake_embedder):
    db.init_db(db_path).close()
    stats = cli._ingest_directory(sample_notes, db_path)

    assert stats["total"] == 3
    assert stats["ingested"] == 3
    assert stats["skipped"] == 0
    assert stats["failed"] == 0

    conn = db.get_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
        hash_rows = conn.execute("SELECT content_hash FROM documents").fetchall()
        assert all(r["content_hash"] for r in hash_rows)

        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert chunk_count >= 3
        vec_count = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        assert vec_count == chunk_count

        edges = list(
            conn.execute(
                "SELECT source_id, target_id, relationship_type FROM edges"
            )
        )
        assert len(edges) == 2
        rels = {e["relationship_type"] for e in edges}
        assert rels == {"lead", "peer"}

        timeline_row = conn.execute(
            "SELECT timeline_text FROM documents WHERE path = ?",
            ("with-timeline.md",),
        ).fetchone()
        assert "kicked off" in timeline_row["timeline_text"]

        minimal_row = conn.execute(
            "SELECT timeline_text FROM documents WHERE path = ?",
            ("minimal.md",),
        ).fetchone()
        assert minimal_row["timeline_text"] is None

        fts_count = conn.execute(
            "SELECT COUNT(*) FROM documents_fts"
        ).fetchone()[0]
        assert fts_count == 3
    finally:
        conn.close()


def test_reingest_unchanged_is_skipped(sample_notes, db_path, fake_embedder):
    db.init_db(db_path).close()
    cli._ingest_directory(sample_notes, db_path)

    conn = db.get_db(db_path)
    before_rowids = sorted(r["rowid"] for r in conn.execute("SELECT rowid FROM chunks"))
    before_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()

    stats = cli._ingest_directory(sample_notes, db_path)
    assert stats["ingested"] == 0
    assert stats["skipped"] == 3
    assert stats["failed"] == 0

    conn = db.get_db(db_path)
    after_rowids = sorted(r["rowid"] for r in conn.execute("SELECT rowid FROM chunks"))
    after_total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()

    assert before_rowids == after_rowids
    assert after_total == before_total


def test_reingest_modified_file_refreshes_only_that_file(
    sample_notes, db_path, fake_embedder
):
    db.init_db(db_path).close()
    cli._ingest_directory(sample_notes, db_path)

    conn = db.get_db(db_path)
    hashes_before = {
        r["path"]: r["content_hash"]
        for r in conn.execute("SELECT path, content_hash FROM documents")
    }
    conn.close()

    (sample_notes / "minimal.md").write_text(
        "Completely different content now with [[new/target]] (cites).\n"
    )

    stats = cli._ingest_directory(sample_notes, db_path)
    assert stats["ingested"] == 1
    assert stats["skipped"] == 2

    conn = db.get_db(db_path)
    try:
        hashes_after = {
            r["path"]: r["content_hash"]
            for r in conn.execute("SELECT path, content_hash FROM documents")
        }
        assert hashes_after["minimal.md"] != hashes_before["minimal.md"]
        assert hashes_after["with-timeline.md"] == hashes_before["with-timeline.md"]
        assert hashes_after["people/alice.md"] == hashes_before["people/alice.md"]

        # The modified file's new edge should be present; old edges from minimal
        # (there were none) should still not exist.
        edges = list(
            conn.execute(
                "SELECT relationship_type FROM edges WHERE source_id = ?",
                (cli.parser.compute_doc_id("minimal.md"),),
            )
        )
        assert [e["relationship_type"] for e in edges] == ["cites"]
    finally:
        conn.close()


def test_ingest_empty_directory(tmp_path, db_path, fake_embedder):
    empty = tmp_path / "empty"
    empty.mkdir()
    db.init_db(db_path).close()

    stats = cli._ingest_directory(empty, db_path)
    assert stats == {"total": 0, "ingested": 0, "skipped": 0, "failed": 0}
