"""Real maintained CLI, single stdio MCP, and stdio proxy/shared MCP gates.

Synthetic production-schema fixtures; no provider, corpus or apparatus edits.
Set MINDGRAPH_STAGE1_RECEIPT_DIR to retain raw behavioral receipts externally.
"""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager, closing, contextmanager
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mindgraph import db, embedders

ALIAS = "reader-alias"
INDEX = "mainframe-knowledge"
TRUST = "durable_knowledge"
QUERY = "alpha"
TEXT = "alpha exact source context " * 40 + "TAIL_SOURCE_SENTINEL"
DIGEST = hashlib.sha256(TEXT.encode()).hexdigest()
COMMAND = [sys.executable, "-c", "from mindgraph.cli import app; app()"]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Probe:
    def __init__(self, root):
        self.root = root
        configured = os.environ.get("MINDGRAPH_STAGE1_RECEIPT_DIR")
        self.receipts = Path(configured) if configured else root / "receipts"
        self.receipts.mkdir(parents=True, exist_ok=True)

    def record(self, label, **fields):
        with (self.receipts / "boundaries.jsonl").open("a") as output:
            output.write(json.dumps({"label": label, **fields}, default=str) + "\n")

    def cli(self, label, *args):
        result = subprocess.run(COMMAND + list(map(str, args)), capture_output=True,
                                text=True, timeout=60)
        self.record(label, boundary="cli", args=args, exit_code=result.returncode,
                    stdout=result.stdout, stderr=result.stderr)
        return result

    async def call(self, session, label, tool, args):
        result = await session.call_tool(tool, args)
        self.record(label, boundary="mcp", tool=tool, args=args,
                    response=result.model_dump(mode="json"))
        return result


@pytest.fixture(scope="module")
def source_vector():
    # The shared production endpoint uses fused retrieval. Seed with the real
    # cached production model, not a mocked embedder or an altered endpoint.
    model = embedders.load_sentence_embedder(embedders.resolve_embedder("minilm"))
    return model.encode([TEXT], convert_to_numpy=True)[0].tolist()


def seed(path, source_vector):
    conn = db.init_db(str(path), semantic_enabled=True)
    for doc_id, status, tags in [("same-doc", "queued", []),
                                 ("unverified", "queued", ["needs-audit"]),
                                 ("excluded", "superseded", [])]:
        metadata = json.dumps({"type": "note", "status": status, "tags": tags})
        conn.execute("""INSERT INTO documents
            (id,title,path,content_hash,index_id,namespace,trust_profile,
             source_path,display_path,metadata_json,domain)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, doc_id, doc_id + ".md", DIGEST, INDEX, "knowledge", TRUST,
             doc_id + ".md", doc_id + ".md", metadata, "test"))
        db.insert_chunks_and_embeddings(conn, doc_id, [TEXT], [source_vector])
        conn.execute("INSERT INTO documents_fts(id,title,content) VALUES(?,?,?)",
                     (doc_id, doc_id, TEXT))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
    assert conn.execute("SELECT text FROM chunks WHERE doc_id='same-doc'").fetchone()[0] == TEXT
    conn.close()


@pytest.fixture
def fixture_indexes(tmp_path, source_vector):
    left = tmp_path / "left.sqlite"
    seed(left, source_vector)
    mutations = {
        # Exact #23 shape: same doc/chunk/hash, different index/trust/path.
        "collision-original": "UPDATE documents SET index_id='mainframe-projects',trust_profile='project_status',path='project.md',namespace='projects'",
        # Stronger collision: even trust, namespace and path are identical.
        "collision-same-trust": "UPDATE documents SET index_id='index-b'",
        "missing-index": "UPDATE documents SET index_id=NULL",
        "blank-index": "UPDATE documents SET index_id=' '",
        "mixed-index": "UPDATE documents SET index_id='index-b' WHERE id='excluded'",
        "mixed-null": "UPDATE documents SET index_id=NULL WHERE id='excluded'",
        "contradictory-meta": "INSERT INTO index_meta(key,value) VALUES('index_id','index-b')",
        "missing-document": "DELETE FROM documents WHERE id='same-doc'",
        "missing-chunk": "DELETE FROM chunks WHERE doc_id='same-doc'",
        "missing-hash": "UPDATE documents SET content_hash='' WHERE id='same-doc'",
    }
    indexes = {"valid": left}
    for name, sql in mutations.items():
        path = tmp_path / (name + ".sqlite")
        shutil.copyfile(left, path)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute(sql)
            conn.commit()
        indexes[name] = path
    for name in ("namespace", "path", "stale", "handle-index", "malformed", "exp1", "wrong-scope"):
        indexes[name] = left
    # Direct preflight of the collision rather than trusting its constructor.
    with closing(sqlite3.connect(indexes["collision-original"])) as conn:
        row = conn.execute("SELECT index_id,content_hash FROM documents WHERE id='same-doc'").fetchone()
        assert row == ("mainframe-projects", DIGEST)
        assert conn.execute("SELECT text FROM chunks WHERE doc_id='same-doc' AND chunk_index=0").fetchone()[0] == TEXT
    probe = Probe(tmp_path)
    probe.record("fixtures/preflight", index=INDEX, digest=DIGEST,
                 fixtures={k: {"path": str(v), "sha256": hashlib.sha256(v.read_bytes()).hexdigest()}
                           for k, v in indexes.items()})
    return indexes


def encode(payload):
    # Independent wire construction, not the candidate's encode predicate.
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "exp2:" + base64.urlsafe_b64encode(raw).decode()


def altered(handle, case, scope=None):
    payload = json.loads(base64.urlsafe_b64decode(handle.split(":", 1)[1]))
    if scope is not None:
        payload["scope"] = scope
    changes = {"namespace": {"namespace": "wrong"}, "path": {"path": "wrong.md"},
               "stale": {"content_hash": "0" * 64}, "handle-index": {"index_id": None},
               "wrong-scope": {"scope": "wrong-alias"}}
    payload.update(changes.get(case, {}))
    if case == "malformed":
        return "exp2:!!!"
    if case == "exp1":
        return "exp1:e30="
    return encode(payload)


def compact(payload):
    def walk(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key != "chunk_text"
                if key in {"results", "not_citable"}:
                    assert path == ("citation_counts",) and type(child) is int
                walk(child, path + (key,))
        elif isinstance(value, list):
            for child in value:
                walk(child, path)
    walk(payload)
    assert TEXT not in json.dumps(payload)
    assert "TAIL_SOURCE_SENTINEL" not in json.dumps(payload)


def nominations_match(payload, rows, scope):
    compact(payload)
    noms = payload["nominations"]
    assert [n["doc_id"] for n in noms] == [r["doc_id"] for r in rows]
    for nomination, row in zip(noms, rows):
        for key in ("doc_id", "path", "content_hash", "chunk_index", "citation_class",
                    "provenance_warning", "trust_profile", "index_id", "namespace",
                    "source_path", "display_path", "signal", "lexical_rank", "semantic_rank"):
            assert nomination[key] == row[key]
        assert nomination["freshness"] == "UNKNOWN"
        assert nomination["raw_status"] == row["status"]
        expected_reasons = {"lexical": ["lexical_match"],
                            "semantic": ["semantic_match"],
                            "fused": ["lexical_match", "semantic_match", "fused_rank"]}
        if row["signal"] == "semantic" and row["weak_fit"]:
            expected_reasons["semantic"].append("weak_fit")
        assert nomination["retrieval_reasons"] == expected_reasons[row["signal"]]
        assert nomination["preview"] == TEXT[:280]
        assert nomination["preview_chars"] == 280
        assert nomination["preview_truncated"] is True
        identity = dict(policy_version="nom1", query_sha256=hashlib.sha256(QUERY.encode()).hexdigest(),
                        scope_index=scope, doc_id=row["doc_id"], content_hash=row["content_hash"],
                        chunk_index=row["chunk_index"], signal=row["signal"], path=row["path"])
        expected = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert nomination["nomination_id"] == "nom1:" + expected
    return next(n["expansion_handle"] for n in noms if n["doc_id"] == "same-doc")


def failure(text):
    assert "TAIL_SOURCE_SENTINEL" not in text
    assert "alpha exact source context" not in text
    assert '"chunk_text"' not in text


def exact_expansion(payload):
    assert payload["doc_id"] == "same-doc"
    assert payload["chunk_index"] == 0
    assert payload["chunk_text"] == TEXT
    assert payload["index_id"] == INDEX
    assert payload["content_hash"] == DIGEST
    assert payload["path"] == "same-doc.md"
    assert payload["namespace"] == "knowledge"
    assert payload["trust_profile"] == TRUST
    assert payload["freshness"] == "UNKNOWN"
    assert payload["raw_status"] == "queued"


@asynccontextmanager
async def session_for(probe, label, args):
    with (probe.receipts / (label + ".stderr")).open("w") as log:
        params = StdioServerParameters(command=sys.executable,
                                      args=COMMAND[1:] + list(map(str, args)),
                                      env=dict(os.environ))
        async with stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=60)) as session:
                await session.initialize()
                catalog = await session.list_tools()
                probe.record(label + "/catalog", response=catalog.model_dump(mode="json"))
                yield session
    failure((probe.receipts / (label + ".stderr")).read_text())


@contextmanager
def shared_daemon(probe, indexes):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    args = COMMAND + ["serve-daemon", "--host", "127.0.0.1", "--port", str(port)]
    for alias, path in indexes.items():
        args += ["--scope", f"{alias}:registration-label={path}"]
    with (probe.receipts / "daemon.stderr").open("w") as log:
        process = subprocess.Popen(args, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                assert process.poll() is None, "private daemon exited; inspect daemon.stderr"
                try:
                    with urllib.request.urlopen(url + "/health", timeout=1) as reply:
                        health = json.load(reply)
                    probe.record("daemon/health", response=health, pid=process.pid)
                    break
                except (OSError, ValueError):
                    time.sleep(0.1)
            else:
                pytest.fail("private shared MCP startup timed out")
            yield ["mcp-proxy", "--url", url + "/mcp", "--no-auto-start",
                   "--state-dir", str(probe.root / "private-state")]
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    failure((probe.receipts / "daemon.stderr").read_text())


def test_real_cli_matrix(tmp_path, fixture_indexes):
    probe = Probe(tmp_path)
    left = fixture_indexes["valid"]
    args = ["query", QUERY, "--db", left, "--json", "--lexical-only", "--no-intent"]
    legacy = probe.cli("cli/legacy", *args)
    assert legacy.returncode == 0, legacy.stderr
    rows = json.loads(legacy.stdout)
    assert len(rows) == 3
    assert {r["citation_class"] for r in rows} == {"citable", "unverified", "not_citable"}
    found = probe.cli("cli/compact", *args, "--envelope", "--nominations", "--nomination-scope", ALIAS)
    assert found.returncode == 0, found.stderr
    handle = nominations_match(json.loads(found.stdout), rows, ALIAS)
    again = probe.cli("cli/determinism", *args, "--envelope", "--nominations", "--nomination-scope", ALIAS)
    assert again.returncode == 0 and found.stdout == again.stdout
    invalid = probe.cli("cli/invalid-opt-in", *args, "--nominations")
    assert invalid.returncode != 0
    failure(invalid.stdout + invalid.stderr)
    positive = probe.cli("cli/valid", "expand-nomination", handle, "--db", left, "--scope", ALIAS, "--json")
    assert positive.returncode == 0, positive.stderr
    exact_expansion(json.loads(positive.stdout))
    for case, target in fixture_indexes.items():
        if case == "valid":
            continue
        failed = probe.cli("cli/" + case, "expand-nomination", altered(handle, case),
                           "--db", target, "--scope", ALIAS, "--json")
        assert failed.returncode != 0, case
        failure(failed.stdout + failed.stderr)
        if case == "exp1":
            assert "requer" in failed.stderr.lower()
    no_scope = probe.cli("cli/collision-no-scope", "expand-nomination", handle,
                         "--db", fixture_indexes["collision-original"], "--json")
    assert no_scope.returncode != 0 and not no_scope.stdout
    failure(no_scope.stderr)
    disconnected = probe.cli("cli/disconnected", "expand-nomination", handle,
                              "--db", tmp_path / "absent.sqlite", "--json")
    assert disconnected.returncode != 0
    failure(disconnected.stdout + disconnected.stderr)
    for signal, options in [("semantic", ["--lexical-top-k", "0"]), ("fused", [])]:
        real_args = ["query", QUERY, "--db", left, "--json", "--no-intent", *options]
        full = probe.cli("cli/" + signal + "-legacy", *real_args)
        assert full.returncode == 0, full.stderr
        real_rows = json.loads(full.stdout)
        assert real_rows and all(row["signal"] == signal for row in real_rows)
        projected = probe.cli("cli/" + signal + "-compact", *real_args,
                              "--envelope", "--nominations", "--nomination-scope", ALIAS)
        assert projected.returncode == 0, projected.stderr
        nominations_match(json.loads(projected.stdout), real_rows, ALIAS)


@pytest.mark.anyio
async def test_real_single_stdio_matrix(tmp_path, fixture_indexes):
    probe = Probe(tmp_path)
    args = {"question": QUERY, "semantic_top_k": 0}
    async with session_for(probe, "single-valid", ["serve-mcp", "--db", fixture_indexes["valid"]]) as session:
        legacy = await probe.call(session, "single/legacy", "query", args)
        assert not legacy.isError
        rows = json.loads(legacy.content[0].text)
        env = await probe.call(session, "single/envelope", "query", {**args, "envelope": True})
        assert not env.isError and "nominations" not in json.loads(env.content[0].text)
        compact_args = {**args, "envelope": True, "nominations": True}
        found = await probe.call(session, "single/compact", "query", compact_args)
        assert not found.isError
        handle = nominations_match(json.loads(found.content[0].text), rows, INDEX)
        repeated = await probe.call(session, "single/determinism", "query", compact_args)
        assert not repeated.isError and repeated.content == found.content
        invalid = await probe.call(session, "single/invalid-opt-in", "query", {**args, "nominations": True})
        assert invalid.isError
        failure(invalid.model_dump_json())
        valid = await probe.call(session, "single/valid", "expand_nomination", {"expansion_handle": handle})
        assert not valid.isError
        exact_expansion(json.loads(valid.content[0].text))
        for case in ("namespace", "path", "stale", "handle-index", "malformed", "exp1"):
            failed = await probe.call(session, "single/" + case, "expand_nomination",
                                      {"expansion_handle": altered(handle, case)})
            assert failed.isError, case
            failure(failed.model_dump_json())
        for signal, options in [("semantic", {"lexical_top_k": 0}), ("fused", {})]:
            real_args = {"question": QUERY, **options}
            full = await probe.call(session, "single/" + signal + "-legacy", "query", real_args)
            assert not full.isError
            real_rows = json.loads(full.content[0].text)
            assert real_rows and all(row["signal"] == signal for row in real_rows)
            projected = await probe.call(session, "single/" + signal + "-compact", "query",
                                         {**real_args, "envelope": True, "nominations": True})
            assert not projected.isError
            nominations_match(json.loads(projected.content[0].text), real_rows, INDEX)
    probe.record("single/wrong-scope", outcome="NOT_APPLICABLE", reason="single database tool has no scope argument")
    for case, target in fixture_indexes.items():
        if target == fixture_indexes["valid"]:
            continue
        async with session_for(probe, "single-" + case, ["serve-mcp", "--db", target]) as session:
            failed = await probe.call(session, "single/" + case, "expand_nomination", {"expansion_handle": handle})
            assert failed.isError, case
            failure(failed.model_dump_json())


@pytest.mark.anyio
async def test_real_shared_proxy_matrix(tmp_path, fixture_indexes):
    probe = Probe(tmp_path)
    # Collision aliases select the wrong database while retaining the caller
    # alias encoded in handles originally minted against the knowledge index.
    registrations = {"gate-" + k: v for k, v in fixture_indexes.items()}
    with shared_daemon(probe, registrations) as proxy_args:
        async with session_for(probe, "shared-proxy", proxy_args) as session:
            args = {"question": QUERY, "scope": "gate-valid"}
            legacy = await probe.call(session, "shared/legacy", "query", args)
            assert not legacy.isError
            rows = json.loads(legacy.content[0].text)["results"]
            found = await probe.call(session, "shared/compact", "query", {**args, "nominations": True})
            assert not found.isError
            handle = nominations_match(json.loads(found.content[0].text), rows, "gate-valid")
            repeated = await probe.call(session, "shared/determinism", "query", {**args, "nominations": True})
            assert not repeated.isError and repeated.content == found.content
            valid = await probe.call(session, "shared/valid", "expand_nomination",
                                     {"expansion_handle": handle, "scope": "gate-valid"})
            assert not valid.isError
            payload = json.loads(valid.content[0].text)
            exact_expansion(payload)
            assert payload["scope_trust_profile"] == "registration-label"
            assert payload["scope_index"] == payload["scope"] == "gate-valid"
            for case in fixture_indexes:
                if case == "valid":
                    continue
                alias = "gate-" + case
                case_handle = altered(handle, case, scope=alias)
                if case.startswith("collision"):
                    minted = probe.cli("shared/mint-" + case, "query", QUERY, "--db", fixture_indexes["valid"],
                                       "--json", "--envelope", "--nominations", "--nomination-scope", alias,
                                       "--lexical-only", "--no-intent")
                    assert minted.returncode == 0, minted.stderr
                    case_handle = next(n["expansion_handle"] for n in json.loads(minted.stdout)["nominations"]
                                       if n["doc_id"] == "same-doc")
                failed = await probe.call(session, "shared/" + case, "expand_nomination",
                                          {"expansion_handle": case_handle, "scope": alias})
                assert failed.isError, case
                failure(failed.model_dump_json())
            unknown = await probe.call(session, "shared/disconnected", "expand_nomination",
                                       {"expansion_handle": handle, "scope": "unregistered"})
            assert unknown.isError
            failure(unknown.model_dump_json())
