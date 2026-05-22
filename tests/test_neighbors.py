import json as jsonlib

import numpy as np
import pytest
from typer.testing import CliRunner

from mindgraph import cli, db, parser
from mindgraph.models import NeighborResult
from mindgraph.query import list_neighbors


class _ZeroEmbedder:
    def encode(self, texts, convert_to_numpy=True):
        return np.zeros((len(texts), 384), dtype=np.float32)


@pytest.fixture
def fake_embedder(monkeypatch):
    monkeypatch.setattr(cli, "_load_embedder", lambda: _ZeroEmbedder())


@pytest.fixture
def neighbors_db(tmp_path, fake_embedder):
    notes = tmp_path / "vault"
    notes.mkdir()
    (notes / "source.md").write_text(
        "Refers to [[target_a]] (cites), [[target_b]], "
        "and [[missing/x]] (refers).\n"
    )
    (notes / "target_a.md").write_text("Target A.\n")
    (notes / "target_b.md").write_text("Target B.\n")
    (notes / "no_edges.md").write_text("Pure prose with no outbound links.\n")

    db_path = str(tmp_path / "test.sqlite")
    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)
    return db_path


def _doc_id(rel: str) -> str:
    return parser.compute_doc_id(rel)


class TestListNeighbors:
    def test_returns_neighbor_result_objects(self, neighbors_db):
        conn = db.get_db(neighbors_db)
        try:
            edges = list_neighbors(conn, _doc_id("source.md"))
        finally:
            conn.close()
        assert all(isinstance(e, NeighborResult) for e in edges)

    def test_returns_all_outbound_edges_including_dangling(self, neighbors_db):
        conn = db.get_db(neighbors_db)
        try:
            edges = list_neighbors(conn, _doc_id("source.md"))
        finally:
            conn.close()
        target_ids = {e.target_id for e in edges}
        assert _doc_id("target_a.md") in target_ids
        assert _doc_id("target_b.md") in target_ids
        assert _doc_id("missing/x.md") in target_ids
        assert len(edges) == 3

    def test_resolves_source_path_on_every_edge(self, neighbors_db):
        conn = db.get_db(neighbors_db)
        try:
            edges = list_neighbors(conn, _doc_id("source.md"))
        finally:
            conn.close()
        assert all(e.source_path == "source.md" for e in edges)

    def test_dangling_edge_has_null_target_path(self, neighbors_db):
        conn = db.get_db(neighbors_db)
        try:
            edges = list_neighbors(conn, _doc_id("source.md"))
        finally:
            conn.close()
        by_target = {e.target_id: e for e in edges}
        dangling = by_target[_doc_id("missing/x.md")]
        assert dangling.target_path is None
        assert dangling.relationship_type == "refers"

    def test_existing_edges_have_resolved_target_path(self, neighbors_db):
        conn = db.get_db(neighbors_db)
        try:
            edges = list_neighbors(conn, _doc_id("source.md"))
        finally:
            conn.close()
        by_target = {e.target_id: e for e in edges}
        assert by_target[_doc_id("target_a.md")].target_path == "target_a.md"
        assert by_target[_doc_id("target_b.md")].target_path == "target_b.md"

    def test_relationship_type_preserved_including_null(self, neighbors_db):
        conn = db.get_db(neighbors_db)
        try:
            edges = list_neighbors(conn, _doc_id("source.md"))
        finally:
            conn.close()
        by_target = {e.target_id: e for e in edges}
        assert by_target[_doc_id("target_a.md")].relationship_type == "cites"
        assert by_target[_doc_id("target_b.md")].relationship_type is None
        assert by_target[_doc_id("missing/x.md")].relationship_type == "refers"

    def test_source_with_no_edges_returns_empty(self, neighbors_db):
        conn = db.get_db(neighbors_db)
        try:
            edges = list_neighbors(conn, _doc_id("no_edges.md"))
        finally:
            conn.close()
        assert edges == []

    def test_sort_order_target_id_ascending(self, neighbors_db):
        conn = db.get_db(neighbors_db)
        try:
            edges = list_neighbors(conn, _doc_id("source.md"))
        finally:
            conn.close()
        target_ids = [e.target_id for e in edges]
        assert target_ids == sorted(target_ids)


class TestNeighborsCLI:
    def test_neighbors_command_text_output(self, neighbors_db):
        runner = CliRunner()
        result = runner.invoke(
            cli.app, ["neighbors", _doc_id("source.md"), "--db", neighbors_db]
        )
        assert result.exit_code == 0
        assert "target_path" in result.stdout

    def test_neighbors_command_json_output(self, neighbors_db):
        runner = CliRunner()
        result = runner.invoke(
            cli.app,
            ["neighbors", _doc_id("source.md"), "--db", neighbors_db, "--json"],
        )
        assert result.exit_code == 0
        data = jsonlib.loads(result.stdout)
        assert len(data) == 3
        for row in data:
            for field in (
                "source_id",
                "target_id",
                "relationship_type",
                "source_path",
                "target_path",
            ):
                assert field in row

    def test_neighbors_command_reports_no_edges_message(self, neighbors_db):
        runner = CliRunner()
        result = runner.invoke(
            cli.app, ["neighbors", _doc_id("no_edges.md"), "--db", neighbors_db]
        )
        assert result.exit_code == 0
        assert "no outbound edges" in result.stdout
