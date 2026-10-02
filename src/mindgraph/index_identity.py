"""Producer-declared index authority bound to the queried SQLite snapshot.

This contract identifies a corpus. It does not verify source claims or defend
against a malicious writer capable of replacing both data and its binding.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import PurePosixPath
import re
import sqlite3

from mindgraph.exceptions import DatabaseError

INDEX_SCHEMA = "mindgraph-index-identity/v1"
QUERY_SCHEMA = "mindgraph-query-identity/v1"
KEY = "index_identity"
DECLARATION_FIELDS = frozenset({
    "schema_version", "index_id", "trust_profile", "retrieval_scope",
    "lifecycle_root", "producer", "manifest_sha256", "source_document_map_sha256",
})
BINDING_FIELDS = DECLARATION_FIELDS | {"document_count", "database_document_map_sha256"}
MAP_FIELDS = (
    "id", "index_id", "trust_profile", "namespace", "source_root",
    "source_path", "display_path", "path", "content_hash",
)
SOURCE_MAP_FIELDS = tuple(field for field in MAP_FIELDS if field != "path")


def _fail(detail):
    raise DatabaseError(f"Index identity unavailable or incompatible: {detail}")


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_fields(identity, *, binding):
    fields = BINDING_FIELDS if binding else DECLARATION_FIELDS
    if not isinstance(identity, dict) or set(identity) != fields:
        _fail("invalid v1 fields")
    if identity["schema_version"] != INDEX_SCHEMA:
        _fail("unsupported schema version")
    for field in ("index_id", "trust_profile", "retrieval_scope", "lifecycle_root", "producer"):
        value = identity[field]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            _fail(f"invalid {field}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identity["lifecycle_root"]):
        _fail("lifecycle_root must be one relative path component")
    for field in ("manifest_sha256", "source_document_map_sha256"):
        if not _sha(identity[field]):
            _fail(f"invalid {field}")
    if binding:
        count = identity["document_count"]
        if type(count) is not int or count < 0:
            _fail("invalid document_count")
        if not _sha(identity["database_document_map_sha256"]):
            _fail("invalid database_document_map_sha256")


def _document_map(conn):
    try:
        rows = conn.execute(f"SELECT {', '.join(MAP_FIELDS)} FROM documents ORDER BY id").fetchall()
        return [dict(zip(MAP_FIELDS, row)) for row in rows]
    except sqlite3.Error as exc:
        _fail(f"document map unreadable ({exc})")


def _validate_map(identity, rows):
    prefix = identity["lifecycle_root"] + "/"
    for row in rows:
        if row["index_id"] != identity["index_id"] or row["trust_profile"] != identity["trust_profile"]:
            _fail("stored document index/trust differs from binding")
        source = row["source_path"]
        display = row["display_path"]
        if not isinstance(source, str) or not source or PurePosixPath(source).is_absolute() or ".." in PurePosixPath(source).parts:
            _fail("invalid stored source path")
        if not isinstance(display, str) or not display.startswith(prefix) or ".." in PurePosixPath(display).parts or row["path"] != display:
            _fail("stored document lifecycle/path differs from binding")
        if not isinstance(row["namespace"], str) or not row["namespace"] or not _sha(row["content_hash"]):
            _fail("missing stored namespace/content identity")


def _map_hash(rows):
    data = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def source_document_map_hash(rows):
    """Hash the producer's source projection, independent of query results.

    Source producers compare these eight fields against their selected files.
    The engine reconstructs the same projection from the complete stored map;
    typed lifecycle classification remains the producer's separate authority.
    """
    projected = [
        {field: row.get(field) for field in SOURCE_MAP_FIELDS}
        for row in sorted(rows, key=lambda row: (str(row.get("namespace")), str(row.get("source_path"))))
    ]
    return _map_hash(projected)


def _validate_source_map(identity, rows):
    if identity["source_document_map_sha256"] != source_document_map_hash(rows):
        _fail("stored source document map differs from producer declaration")


@contextmanager
def read_snapshot(conn):
    """Keep identity, map and retrieval on one read transaction."""
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if owns_transaction:
            conn.rollback()


def read_identity(conn, *, expected=None):
    """Validate stored authority against all documents, even on a no-hit query."""
    try:
        row = conn.execute("SELECT value FROM index_meta WHERE key = ?", (KEY,)).fetchone()
    except sqlite3.Error as exc:
        _fail(f"binding unreadable ({exc})")
    if row is None:
        _fail("producer binding missing; stage an explicitly identified corpus")
    try:
        identity = json.loads(row[0])
    except (ValueError, TypeError):
        _fail("malformed producer binding")
    _validate_fields(identity, binding=True)
    for field, value in (expected or {}).items():
        if identity.get(field) != value:
            _fail(f"stored {field} differs from required identity")
    rows = _document_map(conn)
    _validate_map(identity, rows)
    if identity["document_count"] != len(rows) or identity["database_document_map_sha256"] != _map_hash(rows):
        _fail("stored document map changed after producer binding")
    _validate_source_map(identity, rows)
    return identity


def bind_identity(conn, declaration):
    """Write one explicit producer declaration; no inference or rebinding."""
    _validate_fields(declaration, binding=False)
    if conn.in_transaction:
        _fail("binding requires its own writer transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = _document_map(conn)
        _validate_map(declaration, rows)
        _validate_source_map(declaration, rows)
        identity = {**declaration, "document_count": len(rows), "database_document_map_sha256": _map_hash(rows)}
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        old = conn.execute("SELECT value FROM index_meta WHERE key = ?", (KEY,)).fetchone()
        if old is not None:
            if json.loads(old[0]) != identity:
                _fail("existing binding differs; rebuild a fresh staged database")
        else:
            conn.execute("INSERT INTO index_meta (key, value) VALUES (?, ?)", (KEY, encoded))
        conn.commit()
        return identity
    except Exception:
        conn.rollback()
        raise


def query_envelope(identity, results):
    return {"schema_version": QUERY_SCHEMA, "database_identity": identity,
            "results": [row.model_dump() for row in results]}
