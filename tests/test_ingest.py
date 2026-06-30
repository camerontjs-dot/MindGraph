import json

import numpy as np
import pytest
from typer.testing import CliRunner

from mindgraph import cli, db, parser
from mindgraph.query import list_neighbors


class FakeEmbedder:
    """Stand-in for sentence-transformers — returns zero-vectors of the right shape."""

    def encode(self, texts, convert_to_numpy=True):
        return np.zeros((len(texts), 384), dtype=np.float32)


@pytest.fixture
def fake_embedder(monkeypatch):
    monkeypatch.setattr(cli, "_load_embedder", lambda *_a, **_k: FakeEmbedder())


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
        resolved_edges = conn.execute(
            """
            SELECT COUNT(*)
            FROM edges e
            JOIN documents d ON d.id = e.target_id
            """
        ).fetchone()[0]
        assert resolved_edges == 1

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


def test_ingest_serializes_yaml_date_metadata(tmp_path, db_path, fake_embedder):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "dated.md").write_text(
        "---\ntitle: Dated Note\nupdated: 2026-06-19\n---\nDate metadata.\n"
    )

    db.init_db(db_path).close()
    stats = cli._ingest_directory(notes, db_path)
    assert stats["ingested"] == 1
    assert stats["failed"] == 0

    conn = db.get_db(db_path)
    try:
        metadata = json.loads(
            conn.execute(
                "SELECT metadata_json FROM documents WHERE path = ?",
                ("dated.md",),
            ).fetchone()["metadata_json"]
        )
        assert metadata["updated"] == "2026-06-19"
    finally:
        conn.close()


def test_ingest_resolves_neighbors_to_target_path(tmp_path, db_path, fake_embedder):
    notes = tmp_path / "notes"
    notes.mkdir()
    agents = notes / "agents"
    agents.mkdir()
    ai_business = notes / "ai-business"
    ai_business.mkdir()

    (agents / "source.md").write_text(
        "Connects to [[same-domain]] and [[cross-domain]].\n"
    )
    (agents / "same-domain.md").write_text("Same-domain target.\n")
    (ai_business / "cross-domain.md").write_text("Cross-domain target.\n")

    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)

    conn = db.get_db(db_path)
    try:
        neighbors = list_neighbors(conn, parser.compute_doc_id("agents/source.md"))
    finally:
        conn.close()

    target_paths = {edge.target_path for edge in neighbors}
    assert target_paths == {"agents/same-domain.md", "ai-business/cross-domain.md"}


def test_reingest_unchanged_source_refreshes_resolved_edges(
    tmp_path, db_path, fake_embedder
):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "source.md").write_text("Connects to [[target]] (relates).\n")

    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)

    conn = db.get_db(db_path)
    try:
        before = list_neighbors(conn, parser.compute_doc_id("source.md"))
        before_rowids = sorted(
            r["rowid"] for r in conn.execute("SELECT rowid FROM chunks")
        )
    finally:
        conn.close()

    assert before[0].target_path is None

    (notes / "target.md").write_text("Target arrives later.\n")
    stats = cli._ingest_directory(notes, db_path)
    assert stats["ingested"] == 1
    assert stats["skipped"] == 1
    assert stats["failed"] == 0

    conn = db.get_db(db_path)
    try:
        after = list_neighbors(conn, parser.compute_doc_id("source.md"))
        after_rowids = sorted(
            r["rowid"] for r in conn.execute("SELECT rowid FROM chunks")
        )
    finally:
        conn.close()

    assert after[0].target_path == "target.md"
    assert before_rowids == after_rowids[: len(before_rowids)]


def test_skip_path_does_not_rewrite_unchanged_edges(
    sample_notes, db_path, fake_embedder, monkeypatch
):
    db.init_db(db_path).close()
    cli._ingest_directory(sample_notes, db_path)

    calls: list[str] = []
    real_replace = db.replace_edges

    def counting_replace(conn, source_id, edges):
        calls.append(source_id)
        return real_replace(conn, source_id, edges)

    monkeypatch.setattr(db, "replace_edges", counting_replace)

    stats = cli._ingest_directory(sample_notes, db_path)
    assert stats["skipped"] == 3
    # Nothing changed on disk and no link resolution changed, so the skip path
    # must not issue a single edge rewrite.
    assert calls == []


def test_skip_path_rewrites_edges_when_resolution_changes(
    tmp_path, db_path, fake_embedder
):
    notes = tmp_path / "notes"
    notes.mkdir()
    agents = notes / "agents"
    agents.mkdir()
    # `[[foo]]` is a bare stem. With no sibling agents/foo.md it dangles to
    # compute_doc_id("foo.md").
    (agents / "source.md").write_text("Links to [[foo]] (rel).\n")

    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)

    conn = db.get_db(db_path)
    try:
        before = list_neighbors(conn, parser.compute_doc_id("agents/source.md"))
        assert before[0].target_id == parser.compute_doc_id("foo.md")
        assert before[0].target_path is None
    finally:
        conn.close()

    # Add the sibling. source.md is byte-identical (skip path), but `[[foo]]`
    # now resolves to agents/foo.md — a different target_id — so the skip path
    # must rewrite the edge rather than leave the stale dangling target.
    (agents / "foo.md").write_text("The foo target.\n")
    stats = cli._ingest_directory(notes, db_path)
    assert stats["skipped"] == 1
    assert stats["ingested"] == 1

    conn = db.get_db(db_path)
    try:
        after = list_neighbors(conn, parser.compute_doc_id("agents/source.md"))
        assert after[0].target_id == parser.compute_doc_id("agents/foo.md")
        assert after[0].target_path == "agents/foo.md"
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
    assert stats == {
        "total": 0,
        "ingested": 0,
        "skipped": 0,
        "pruned": 0,
        "failed": 0,
    }


def test_ingest_prunes_deleted_file(tmp_path, db_path, fake_embedder):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "keep.md").write_text("This note stays put.\n")
    (notes / "remove.md").write_text("This note will be deleted later.\n")

    db.init_db(db_path).close()
    stats = cli._ingest_directory(notes, db_path)
    assert stats["ingested"] == 2
    assert stats["pruned"] == 0

    (notes / "remove.md").unlink()
    stats = cli._ingest_directory(notes, db_path)
    assert stats["pruned"] == 1
    assert stats["skipped"] == 1  # keep.md unchanged

    removed_id = parser.compute_doc_id("remove.md")
    conn = db.get_db(db_path)
    try:
        paths = {r["path"] for r in conn.execute("SELECT path FROM documents")}
        assert paths == {"keep.md"}
        # All artifacts of the pruned doc are gone, not just the documents row.
        for table, col in (("chunks", "doc_id"), ("documents_fts", "id")):
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (removed_id,)
            ).fetchone()[0]
            assert count == 0
    finally:
        conn.close()


def test_ingest_prunes_renamed_file(tmp_path, db_path, fake_embedder):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "old-name.md").write_text("Stable content that just moves.\n")

    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)

    (notes / "old-name.md").rename(notes / "new-name.md")
    stats = cli._ingest_directory(notes, db_path)
    # A rename is a new doc id at the new path plus an orphan at the old one.
    assert stats["pruned"] == 1
    assert stats["ingested"] == 1

    conn = db.get_db(db_path)
    try:
        paths = {r["path"] for r in conn.execute("SELECT path FROM documents")}
        assert paths == {"new-name.md"}
    finally:
        conn.close()


def test_pruned_doc_leaves_inbound_edges_dangling(tmp_path, db_path, fake_embedder):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "source.md").write_text("Points to [[target]] (cites).\n")
    (notes / "target.md").write_text("The target of the link.\n")

    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)

    conn = db.get_db(db_path)
    try:
        before = list_neighbors(conn, parser.compute_doc_id("source.md"))
        assert before[0].target_path == "target.md"
    finally:
        conn.close()

    (notes / "target.md").unlink()
    stats = cli._ingest_directory(notes, db_path)
    assert stats["pruned"] == 1

    conn = db.get_db(db_path)
    try:
        after = list_neighbors(conn, parser.compute_doc_id("source.md"))
        # The inbound edge survives but now dangles (target row removed).
        assert len(after) == 1
        assert after[0].target_path is None
    finally:
        conn.close()


def test_ingest_many_namespaces_duplicate_project_paths_and_prunes_union(
    tmp_path, db_path, fake_embedder
):
    projects = tmp_path / "30_projects"
    alpha = projects / "alpha"
    beta = projects / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    (alpha / "README.md").write_text(
        "---\ntitle: Alpha Project\n---\nAlpha status links to [[decisions]] (records).\n"
    )
    (alpha / "decisions.md").write_text("Alpha decision record.\n")
    (alpha / "workbench").mkdir()
    (alpha / "workbench" / "README.md").write_text("Noisy nested source.\n")
    (beta / "README.md").write_text(
        "---\ntitle: Beta Project\n---\nBeta status is separate.\n"
    )

    def scope(slug, root):
        return cli.IngestScope(
            root=root,
            index_id="mainframe-projects",
            trust_profile="project_status",
            namespace=slug,
            source_root=root,
            display_prefix=f"30_projects/{slug}",
            include_globs=("README.md", "decisions.md"),
            exclude_globs=("workbench/*", "workbench/**/*"),
        )

    db.init_db(db_path).close()
    stats = cli._ingest_scopes([scope("alpha", alpha), scope("beta", beta)], db_path)
    assert stats["total"] == 3
    assert stats["ingested"] == 3
    assert stats["failed"] == 0

    alpha_readme_id = parser.compute_scoped_doc_id(
        "mainframe-projects", "alpha", "README.md"
    )
    beta_readme_id = parser.compute_scoped_doc_id(
        "mainframe-projects", "beta", "README.md"
    )
    assert alpha_readme_id != beta_readme_id

    conn = db.get_db(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, path, index_id, trust_profile, namespace, source_root,
                   source_path, display_path
            FROM documents
            ORDER BY path
            """
        ).fetchall()
        assert {row["path"] for row in rows} == {
            "30_projects/alpha/README.md",
            "30_projects/alpha/decisions.md",
            "30_projects/beta/README.md",
        }
        by_id = {row["id"]: row for row in rows}
        assert by_id[alpha_readme_id]["namespace"] == "alpha"
        assert by_id[alpha_readme_id]["index_id"] == "mainframe-projects"
        assert by_id[alpha_readme_id]["trust_profile"] == "project_status"
        assert by_id[alpha_readme_id]["source_path"] == "README.md"
        assert by_id[alpha_readme_id]["display_path"] == (
            "30_projects/alpha/README.md"
        )

        neighbors = list_neighbors(conn, alpha_readme_id)
        assert len(neighbors) == 1
        assert neighbors[0].target_path == "30_projects/alpha/decisions.md"
    finally:
        conn.close()

    (beta / "README.md").unlink()
    stats = cli._ingest_scopes([scope("alpha", alpha), scope("beta", beta)], db_path)
    assert stats["pruned"] == 1

    conn = db.get_db(db_path)
    try:
        paths = {row["path"] for row in conn.execute("SELECT path FROM documents")}
        assert paths == {
            "30_projects/alpha/README.md",
            "30_projects/alpha/decisions.md",
        }
    finally:
        conn.close()


def test_ingest_many_command_loads_manifest(tmp_path, db_path, fake_embedder):
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("Manifest project status.\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "index_id": "mainframe-projects",
                "trust_profile": "project_status",
                "include": ["README.md"],
                "scopes": [
                    {
                        "namespace": "manifest-project",
                        "root": str(project),
                        "source_root": str(project),
                        "display_prefix": "30_projects/manifest-project",
                    }
                ],
            }
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli.app, ["ingest-many", str(manifest), "--db", db_path])
    assert result.exit_code == 0

    conn = db.get_db(db_path)
    try:
        row = conn.execute(
            "SELECT path, namespace FROM documents"
        ).fetchone()
        assert row["path"] == "30_projects/manifest-project/README.md"
        assert row["namespace"] == "manifest-project"
    finally:
        conn.close()


def test_ingest_many_allow_failures_keeps_partial_index(
    tmp_path, db_path, fake_embedder
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "good.md").write_text("Good project context.\n")
    (project / "bad.md").write_text("---\nupdated: bad: yaml\n---\nBad.\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "include": ["*.md"],
                "scopes": [
                    {
                        "namespace": "partial",
                        "root": str(project),
                        "display_prefix": "30_projects/partial",
                    }
                ],
            }
        )
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["ingest-many", str(manifest), "--db", db_path, "--allow-failures"],
    )
    assert result.exit_code == 0

    conn = db.get_db(db_path)
    try:
        paths = {row["path"] for row in conn.execute("SELECT path FROM documents")}
        assert paths == {"30_projects/partial/good.md"}
    finally:
        conn.close()


def test_ingest_many_allow_failures_does_not_prune_still_present_bad_file(
    tmp_path, db_path, fake_embedder
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "good.md").write_text("Good project context.\n")
    (project / "fragile.md").write_text("Initially valid project context.\n")
    scope = cli.IngestScope(
        root=project,
        index_id="mainframe-projects",
        trust_profile="project_status",
        namespace="partial",
        source_root=project,
        display_prefix="30_projects/partial",
        include_globs=("*.md",),
    )

    db.init_db(db_path).close()
    stats = cli._ingest_scopes([scope], db_path)
    assert stats["ingested"] == 2

    (project / "fragile.md").write_text("---\nupdated: bad: yaml\n---\nBad.\n")
    stats = cli._ingest_scopes([scope], db_path)
    assert stats["failed"] == 1
    assert stats["pruned"] == 0

    conn = db.get_db(db_path)
    try:
        paths = {row["path"] for row in conn.execute("SELECT path FROM documents")}
        assert paths == {
            "30_projects/partial/good.md",
            "30_projects/partial/fragile.md",
        }
    finally:
        conn.close()
