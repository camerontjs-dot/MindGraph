"""Tests for mindgraph doctor/status and query schema fail-fast (MH01)."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mindgraph import cli, db


@pytest.fixture
def runner():
    return CliRunner()


def test_inspect_healthy_db(tmp_path):
    path = str(tmp_path / "ok.sqlite")
    db.init_db(path).close()
    report = db.inspect_database(path, role="test", trust_profile="t")
    assert report["ok"] is True
    assert report["exists"] is True
    assert report["tables_missing"] == []
    assert report["counts"]["documents"] == 0


def test_inspect_read_only_wal_db_without_writable_directory(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    path = locked / "index.sqlite"
    db.init_db(str(path)).close()
    os.chmod(path, 0o444)
    os.chmod(locked, 0o555)
    try:
        report = db.inspect_database(str(path), role="projects")
        assert report["ok"] is True, report
        conn = db.get_db(str(path), read_only=True)
        try:
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        finally:
            conn.close()
    finally:
        os.chmod(locked, 0o755)
        os.chmod(path, 0o644)


def test_inspect_missing_file(tmp_path):
    path = str(tmp_path / "nope.sqlite")
    report = db.inspect_database(path)
    assert report["ok"] is False
    assert "file_missing" in report["issues"]


def test_inspect_stub_empty_sqlite(tmp_path):
    """Tiny file without MindGraph tables fails schema check."""
    path = tmp_path / "stub.sqlite"
    # Create a nearly-empty sqlite (no mindgraph tables)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE junk (id INTEGER)")
    conn.commit()
    conn.close()
    report = db.inspect_database(str(path))
    assert report["ok"] is False
    assert "missing_required_tables" in report["issues"]
    assert "documents_fts" in report["tables_missing"]
    assert report["likely_stub"] is True


def test_validate_query_schema_raises(tmp_path):
    from mindgraph.exceptions import DatabaseError

    path = str(tmp_path / "bad.sqlite")
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE junk (id INTEGER)")
    raw.commit()
    raw.close()
    conn = db.get_db(path)
    try:
        with pytest.raises(DatabaseError, match="missing tables"):
            db.validate_query_schema(conn, path)
    finally:
        conn.close()


def test_find_workspace_stubs(tmp_path):
    stub = tmp_path / "mainframe.sqlite"
    stub.write_bytes(b"\x00" * 100)
    hits = db.find_workspace_stub_sqlite([tmp_path])
    assert len(hits) == 1
    assert hits[0]["size_bytes"] == 100


def test_doctor_cli_single_ok(tmp_path, runner):
    path = tmp_path / "ok.sqlite"
    db.init_db(str(path)).close()
    result = runner.invoke(cli.app, ["doctor", "--db", str(path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert len(payload["databases"]) == 1
    assert payload["databases"][0]["ok"] is True


def test_doctor_cli_single_fail(tmp_path, runner):
    path = tmp_path / "bad.sqlite"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE junk (id INTEGER)")
    conn.commit()
    conn.close()
    result = runner.invoke(cli.app, ["doctor", "--db", str(path), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False


def test_status_alias(tmp_path, runner):
    path = tmp_path / "ok.sqlite"
    db.init_db(str(path)).close()
    result = runner.invoke(cli.app, ["status", "--db", str(path), "--json"])
    assert result.exit_code == 0, result.output


def test_query_fail_fast_on_stub(tmp_path, runner, monkeypatch, caplog):
    """Query on incomplete DB exits before embedder load."""
    import logging

    path = tmp_path / "stub.sqlite"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE junk (id INTEGER)")
    conn.commit()
    conn.close()

    def boom(*_a, **_k):
        raise AssertionError("embedder should not load on schema failure")

    monkeypatch.setattr(cli, "_load_embedder", boom)
    with caplog.at_level(logging.ERROR, logger="mindgraph"):
        result = runner.invoke(cli.app, ["query", "hello", "--db", str(path)])
    assert result.exit_code == 1
    combined = (result.output or "") + (result.stderr or "") + caplog.text
    assert "missing tables" in combined.lower() or "usable MindGraph" in combined


def test_doctor_detects_workspace_stub_flag(tmp_path, runner):
    path = tmp_path / "ok.sqlite"
    db.init_db(str(path)).close()
    stub = tmp_path / "mainframe.sqlite"
    stub.write_bytes(b"\x00" * 200)
    result = runner.invoke(
        cli.app,
        ["doctor", "--db", str(path), "--workspace", str(tmp_path), "--json"],
    )
    # healthy DB → exit 0; stubs listed as non-fatal warnings
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["workspace_stubs"]
    assert payload["databases"][0]["ok"] is True
