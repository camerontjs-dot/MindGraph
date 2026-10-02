"""Development contract tests: real SQLite/FTS ingestion and CLI execution."""
import hashlib
import json

import pytest
from typer.testing import CliRunner

from mindgraph import db, index_identity
from mindgraph.cli import app
from mindgraph.exceptions import DatabaseError

runner = CliRunner()


def declaration(scope="operations"):
    operations = scope == "operations"
    return {
        "schema_version": index_identity.INDEX_SCHEMA,
        "retrieval_scope": scope,
        "index_id": "mainframe-operations" if operations else "mainframe-projects",
        "trust_profile": "operations_status" if operations else "project_status",
        "lifecycle_root": "40_operations" if operations else "30_projects",
        "producer": "development-fixture-producer",
        "manifest_sha256": hashlib.sha256(b"explicit development manifest").hexdigest(),
        "source_document_map_sha256": hashlib.sha256(b"explicit development source map").hexdigest(),
    }


def produce(tmp_path, scope="operations"):
    root = tmp_path / scope
    root.mkdir()
    (root / "README.md").write_text("---\ntitle: Compass\ntype: note\n---\n# Compass\n\n## Truth\n\noperations nomination compass\n")
    path = tmp_path / f"{scope}.sqlite"
    identity = declaration(scope)
    result = runner.invoke(app, ["ingest", str(root), "--db", str(path), "--lexical-only",
                                 "--index-id", identity["index_id"], "--trust-profile", identity["trust_profile"],
                                 "--namespace", "fixture", "--display-prefix", identity["lifecycle_root"] + "/fixture"])
    assert result.exit_code == 0, result.output
    # The producer's source map is a declared contract, not an arbitrary SHA.
    # Derive the expected row from the selected source and ingestion inputs.
    source_map = [{
        "id": hashlib.sha256(f"{identity['index_id']}\0fixture\0README.md".encode()).hexdigest()[:16],
        "namespace": "fixture", "index_id": identity["index_id"],
        "trust_profile": identity["trust_profile"], "source_root": str(root.resolve()),
        "source_path": "README.md", "display_path": identity["lifecycle_root"] + "/fixture/README.md",
        "content_hash": hashlib.sha256((root / "README.md").read_bytes()).hexdigest(),
    }]
    identity["source_document_map_sha256"] = hashlib.sha256(
        json.dumps(source_map, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    identity_file = tmp_path / f"{scope}-identity.json"
    identity_file.write_text(json.dumps(identity))
    result = runner.invoke(app, ["bind-index", "--db", str(path), "--identity-file", str(identity_file)])
    assert result.exit_code == 0, result.output
    return path, json.loads(result.stdout)


def query(path, question):
    return runner.invoke(app, ["query", question, "--db", str(path), "--lexical-only", "--json", "--no-intent", "--identity-envelope"])


@pytest.mark.parametrize("question, has_hits", [("compass", True), ("zzmissingdevelopmentidentity", False)])
def test_valid_operations_hits_and_no_hits_keep_database_authority(tmp_path, question, has_hits):
    path, bound = produce(tmp_path)
    result = query(path, question)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["schema_version"] == index_identity.QUERY_SCHEMA
    assert envelope["database_identity"] == bound
    assert bool(envelope["results"]) is has_hits
    assert bound["document_count"] == 1


@pytest.mark.parametrize("question", ["compass", "zzmissingdevelopmentidentity"])
def test_projects_identity_is_explicit_and_cannot_satisfy_operations(tmp_path, question):
    path, bound = produce(tmp_path, "projects")
    result = query(path, question)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["database_identity"]["index_id"] == "mainframe-projects"
    conn = db.get_db(str(path), read_only=True)
    try:
        with index_identity.read_snapshot(conn), pytest.raises(DatabaseError, match="required identity"):
            index_identity.read_identity(conn, expected={"retrieval_scope": "operations"})
    finally:
        conn.close()


def test_unidentified_empty_database_fails_and_is_not_backfilled(tmp_path, caplog):
    path = tmp_path / "mainframe-operations.sqlite"
    db.init_db(str(path)).close()
    result = query(path, "compass")
    assert result.exit_code != 0
    assert "producer binding missing" in caplog.text
    conn = db.get_db(str(path), read_only=True)
    assert conn.execute("SELECT COUNT(*) FROM index_meta WHERE key = 'index_identity'").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("field, value", [("index_id", "mainframe-projects"), ("trust_profile", "project_status"),
                                          ("lifecycle_root", "30_projects"), ("schema_version", "unknown")])
def test_wrong_stored_identity_fails_even_for_no_hits(tmp_path, field, value, caplog):
    path, identity = produce(tmp_path)
    identity[field] = value
    conn = db.get_db(str(path))
    with conn:
        conn.execute("UPDATE index_meta SET value = ? WHERE key = 'index_identity'", (json.dumps(identity),))
    conn.close()
    result = query(path, "zzmissingdevelopmentidentity")
    assert result.exit_code != 0
    assert "Index identity unavailable or incompatible" in caplog.text


def test_stale_document_map_is_rejected_on_no_hit_and_cannot_be_rebound(tmp_path, caplog):
    path, _ = produce(tmp_path)
    conn = db.get_db(str(path))
    with conn:
        conn.execute("UPDATE documents SET content_hash = ?", ("b" * 64,))
    conn.close()
    result = query(path, "zzmissingdevelopmentidentity")
    assert result.exit_code != 0
    assert "changed after producer binding" in caplog.text
    identity_file = tmp_path / "operations-identity.json"
    result = runner.invoke(app, ["bind-index", "--db", str(path), "--identity-file", str(identity_file)])
    assert result.exit_code != 0
    assert "existing binding differs" in result.output


@pytest.mark.parametrize("question", ["compass", "zzmissingdevelopmentidentity"])
def test_valid_wrong_source_map_is_rejected_without_returned_rows_as_authority(tmp_path, question, caplog):
    path, identity = produce(tmp_path)
    _, projects = produce(tmp_path, "projects")
    identity["source_document_map_sha256"] = projects["source_document_map_sha256"]
    conn = db.get_db(str(path))
    with conn:
        conn.execute("UPDATE index_meta SET value = ? WHERE key = 'index_identity'", (json.dumps(identity),))
    conn.close()
    result = query(path, question)
    assert result.exit_code != 0
    assert "producer source document map differs" in caplog.text
    result = runner.invoke(app, ["index-identity", "--db", str(path)])
    assert result.exit_code != 0
    assert "producer source document map differs" in result.output
    result = runner.invoke(app, ["serve-daemon", "--scope", f"operations:operations_status={path}",
                                 "--require-scope-identity", "operations=mainframe-operations:40_operations"])
    assert result.exit_code != 0
    assert "producer source document map differs" in result.output


def test_valid_wrong_source_map_cannot_be_bound_to_unidentified_documents(tmp_path):
    path, identity = produce(tmp_path)
    conn = db.get_db(str(path))
    with conn:
        conn.execute("DELETE FROM index_meta WHERE key = 'index_identity'")
    conn.close()
    identity = {field: identity[field] for field in index_identity.DECLARATION_FIELDS}
    identity["source_document_map_sha256"] = hashlib.sha256(b"different genuine source map").hexdigest()
    declaration_file = tmp_path / "wrong-source-map.json"
    declaration_file.write_text(json.dumps(identity))
    result = runner.invoke(app, ["bind-index", "--db", str(path), "--identity-file", str(declaration_file)])
    assert result.exit_code != 0
    assert "producer source document map differs" in result.output
    conn = db.get_db(str(path), read_only=True)
    assert conn.execute("SELECT COUNT(*) FROM index_meta WHERE key = 'index_identity'").fetchone()[0] == 0
    conn.close()


def test_projects_map_cannot_be_stamped_with_operations_declaration(tmp_path):
    path, _ = produce(tmp_path, "projects")
    identity_file = tmp_path / "forged-operations.json"
    identity_file.write_text(json.dumps(declaration()))
    result = runner.invoke(app, ["bind-index", "--db", str(path), "--identity-file", str(identity_file)])
    assert result.exit_code != 0
    assert "stored document index/trust differs" in result.output


def test_missing_database_fails_without_creating_file(tmp_path):
    missing = tmp_path / "absent.sqlite"
    assert query(missing, "compass").exit_code != 0
    assert not missing.exists()


def test_required_daemon_scope_rejects_projects_before_model_or_listener(tmp_path):
    path, _ = produce(tmp_path, "projects")
    result = runner.invoke(app, ["serve-daemon", "--scope", f"operations:operations_status={path}",
                                 "--require-scope-identity", "operations=mainframe-operations:40_operations"])
    assert result.exit_code != 0
    assert "required identity" in result.output


def test_legacy_array_and_intent_envelope_remain_compatible(tmp_path):
    path, _ = produce(tmp_path)
    base = ["query", "compass", "--db", str(path), "--lexical-only", "--json", "--no-intent"]
    assert isinstance(json.loads(runner.invoke(app, base).stdout), list)
    envelope = json.loads(runner.invoke(app, base + ["--envelope"]).stdout)
    assert envelope["schema_version"] == "1"
    assert "database_identity" not in envelope
    assert runner.invoke(app, base + ["--envelope", "--identity-envelope"]).exit_code != 0


def test_identity_and_query_share_one_read_snapshot(tmp_path):
    from mindgraph import query as query_mod
    path, bound = produce(tmp_path)
    reader = db.get_db(str(path), read_only=True)
    writer = db.get_db(str(path))
    try:
        with index_identity.read_snapshot(reader):
            assert index_identity.read_identity(reader) == bound
            changed = {**bound, "trust_profile": "project_status"}
            with writer:
                writer.execute("UPDATE index_meta SET value=? WHERE key='index_identity'", (json.dumps(changed),))
            # The writer's new identity cannot be paired with the old query rows.
            assert index_identity.read_identity(reader) == bound
            rows = query_mod.run_query(reader, "compass", None, semantic_top_k=0)
            assert rows and all(row.trust_profile == bound["trust_profile"] for row in rows)
        with index_identity.read_snapshot(reader), pytest.raises(DatabaseError):
            index_identity.read_identity(reader)
    finally:
        reader.close()
        writer.close()
