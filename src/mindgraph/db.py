import os
import sqlite3
import struct
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import sqlite_vec

from mindgraph.exceptions import DatabaseError
from mindgraph.models import GraphEdge, ParsedDocument

# Tables required for fused lexical + semantic query (MH01 first-contact contract).
REQUIRED_QUERY_TABLES = frozenset(
    {
        "documents",
        "documents_fts",
        "chunks",
        "vec_chunks",
        "edges",
    }
)

# Heuristic: workspace-root stub SQLite files from early scaffolding are tiny.
STUB_SIZE_BYTES = 16_384  # 16 KiB


def _configure_connection(
    conn: sqlite3.Connection, *, read_only: bool
) -> sqlite3.Connection:
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
        # Force SQLite to read the schema now. A WAL database can connect in
        # mode=ro and then fail on its first query when the containing directory
        # cannot host a shared-memory file; callers need that failure here so
        # the immutable snapshot fallback can be selected deterministically.
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    else:
        # WAL lets the long-lived MCP reader and the ingest/refresh writer
        # coexist without `database is locked` errors. Journal mode is a
        # persistent property of the file, so issuing it on every writable
        # connection is idempotent and migrates pre-existing rollback journals.
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _open_read_only(db_path: str) -> sqlite3.Connection:
    """Open a query-only database, falling back to an immutable snapshot.

    Normal ``mode=ro`` remains preferred because it observes a live WAL. The
    immutable fallback is only used when SQLite cannot create/read WAL shared
    memory in a non-writable runtime (for example, a sandbox-mounted index).
    """
    if db_path == ":memory:":
        raise DatabaseError(":memory: cannot be opened read-only")

    resolved = Path(db_path).expanduser().resolve()
    base_uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    last_error: Exception | None = None
    for immutable in (False, True):
        conn: sqlite3.Connection | None = None
        uri = f"{base_uri}&immutable=1" if immutable else base_uri
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=30.0)
            return _configure_connection(conn, read_only=True)
        except (sqlite3.Error, RuntimeError) as exc:
            last_error = exc
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
    assert last_error is not None
    raise DatabaseError(f"Failed to open database at {db_path}: {last_error}")


def get_db(
    db_path: str = "mindgraph.sqlite", *, read_only: bool = False
) -> sqlite3.Connection:
    """Connect to SQLite, load sqlite-vec, and apply the requested access mode."""
    if read_only:
        return _open_read_only(db_path)

    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        _configure_connection(conn, read_only=False)
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
        ).fetchone():
            with conn:
                _ensure_document_provenance_columns(conn)
        return conn
    except (sqlite3.Error, RuntimeError) as e:
        raise DatabaseError(f"Failed to open database at {db_path}: {e}") from e


DEFAULT_EMBEDDING_DIMS = 384


def init_db(
    db_path: str = "mindgraph.sqlite", *, embedding_dims: int = DEFAULT_EMBEDDING_DIMS
) -> sqlite3.Connection:
    """Initialize the database schema for MindGraph."""
    conn = get_db(db_path)

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT,
                path TEXT,
                domain TEXT,
                content_hash TEXT NOT NULL,
                index_id TEXT,
                trust_profile TEXT,
                namespace TEXT,
                source_root TEXT,
                source_path TEXT,
                display_path TEXT,
                timeline_text TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        _ensure_document_provenance_columns(conn)

        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                id UNINDEXED,
                title,
                content
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT,
                chunk_index INTEGER,
                text TEXT,
                FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)

        vec_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vec_chunks'"
        ).fetchone()
        stored_dims = get_embedding_dims(conn)
        if stored_dims is None:
            if vec_exists:
                stored_dims = DEFAULT_EMBEDDING_DIMS
            else:
                stored_dims = embedding_dims
            set_embedding_dims(conn, stored_dims)
        elif stored_dims != embedding_dims:
            raise DatabaseError(
                f"Database {db_path} was initialized with embedding_dims="
                f"{stored_dims}; requested {embedding_dims}. Use a separate DB "
                "per embedder dimension."
            )

        if not vec_exists:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE vec_chunks USING vec0(
                    embedding float[{stored_dims}]
                )
                """
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT,
                target_id TEXT,
                relationship_type TEXT,
                PRIMARY KEY (source_id, target_id, relationship_type)
            )
        """)

    return conn


def list_table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
    ).fetchall()
    return {row["name"] if isinstance(row, sqlite3.Row) else row[0] for row in rows}


def missing_required_tables(
    conn: sqlite3.Connection, required: frozenset[str] = REQUIRED_QUERY_TABLES
) -> list[str]:
    existing = list_table_names(conn)
    return sorted(required - existing)


def validate_query_schema(
    conn: sqlite3.Connection,
    db_path: str,
    *,
    required: frozenset[str] = REQUIRED_QUERY_TABLES,
) -> None:
    """Fail fast when a DB cannot support fused query (MH01 doctor contract).

    Raises DatabaseError with an actionable message instead of soft-degrading
    to semantic-only / empty FTS results on stub databases.
    """
    missing = missing_required_tables(conn, required)
    if missing:
        raise DatabaseError(
            f"Database at {db_path} is not a usable MindGraph index; "
            f"missing tables: {', '.join(missing)}. "
            "Authoritative DBs live under ~/.mindgraph/ "
            "(not workspace-root stub *.sqlite files). "
            "Run: bin/mindgraph doctor && bin/mindgraph-refresh"
        )


def _count(conn: sqlite3.Connection, sql: str) -> int | None:
    try:
        row = conn.execute(sql).fetchone()
        if row is None:
            return None
        return int(row[0])
    except sqlite3.Error:
        return None


def inspect_database(
    db_path: str,
    *,
    role: str | None = None,
    trust_profile: str | None = None,
) -> dict[str, Any]:
    """Return a doctor diagnostic payload for one database path.

    Does not load the embedding model. Safe for first-contact preflight.
    """
    expanded = os.path.expanduser(db_path)
    path = Path(expanded)
    report: dict[str, Any] = {
        "path": str(path),
        "role": role,
        "trust_profile": trust_profile,
        "exists": path.exists(),
        "size_bytes": None,
        "mtime_iso": None,
        "ok": False,
        "issues": [],
        "warnings": [],
        "tables_present": [],
        "tables_missing": sorted(REQUIRED_QUERY_TABLES),
        "counts": {},
        "embedding_dims": None,
        "likely_stub": False,
    }

    if not path.exists():
        report["issues"].append("file_missing")
        return report

    try:
        stat = path.stat()
    except OSError as e:
        report["issues"].append(f"stat_failed:{e}")
        return report

    report["size_bytes"] = stat.st_size
    report["mtime_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
    if stat.st_size <= STUB_SIZE_BYTES:
        report["likely_stub"] = True
        report["warnings"].append(
            f"file is very small ({stat.st_size} bytes); may be a workspace stub, "
            "not an authoritative index"
        )

    conn: sqlite3.Connection | None = None
    try:
        conn = get_db(str(path), read_only=True)
        tables = list_table_names(conn)
        report["tables_present"] = sorted(tables)
        missing = missing_required_tables(conn)
        report["tables_missing"] = missing
        if missing:
            report["issues"].append("missing_required_tables")
        report["embedding_dims"] = get_embedding_dims(conn)
        report["counts"] = {
            "documents": _count(conn, "SELECT COUNT(*) FROM documents")
            if "documents" in tables
            else None,
            "chunks": _count(conn, "SELECT COUNT(*) FROM chunks")
            if "chunks" in tables
            else None,
            "edges": _count(conn, "SELECT COUNT(*) FROM edges")
            if "edges" in tables
            else None,
            "vec_chunks": _count(conn, "SELECT COUNT(*) FROM vec_chunks")
            if "vec_chunks" in tables
            else None,
        }
        docs = report["counts"].get("documents")
        if docs == 0 and not missing:
            report["warnings"].append("index has zero documents — run mindgraph-refresh")
        report["ok"] = not report["issues"]
    except DatabaseError as e:
        report["issues"].append("open_failed")
        report["warnings"].append(str(e))
    except sqlite3.Error as e:
        report["issues"].append("sqlite_error")
        report["warnings"].append(str(e))
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    return report


def default_dual_db_specs() -> list[dict[str, str]]:
    """Authoritative MainFrame dual-index locations (MH01 / HARNESS)."""
    home = Path.home() / ".mindgraph"
    return [
        {
            "role": "knowledge",
            "trust_profile": "durable_knowledge",
            "path": str(home / "mainframe.sqlite"),
            "refresh_hint": "bin/mindgraph-refresh",
        },
        {
            "role": "projects",
            "trust_profile": "project_status",
            "path": str(home / "mainframe-projects.sqlite"),
            "refresh_hint": "bin/mindgraph-refresh-projects",
        },
    ]


def find_workspace_stub_sqlite(search_roots: list[Path] | None = None) -> list[dict[str, Any]]:
    """Detect tiny workspace-root *.sqlite files that look authoritative but are not."""
    roots = search_roots or [Path.cwd()]
    hits: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for name in ("mainframe.sqlite", "mainframe-projects.sqlite"):
            p = root / name
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size <= STUB_SIZE_BYTES:
                hits.append(
                    {
                        "path": str(p.resolve()),
                        "size_bytes": size,
                        "warning": (
                            "Workspace-root sqlite looks like a stub. "
                            "Query ~/.mindgraph/*.sqlite instead."
                        ),
                    }
                )
    return hits


def get_embedding_dims(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT value FROM index_meta WHERE key = 'embedding_dims'"
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return None


def set_embedding_dims(conn: sqlite3.Connection, dims: int) -> None:
    conn.execute(
        """
        INSERT INTO index_meta (key, value) VALUES ('embedding_dims', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(dims),),
    )


def _ensure_document_provenance_columns(conn: sqlite3.Connection) -> None:
    """Add provenance columns to older databases without requiring a rebuild."""
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(documents)").fetchall()
    }
    for column in (
        "index_id",
        "trust_profile",
        "namespace",
        "source_root",
        "source_path",
        "display_path",
    ):
        if column not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {column} TEXT")


def _serialize_embedding(vec: list[float]) -> bytes:
    """Pack a float vector into the bytes format sqlite-vec expects."""
    return struct.pack(f"{len(vec)}f", *vec)


def get_document_hash(conn: sqlite3.Connection, doc_id: str) -> str | None:
    row = conn.execute(
        "SELECT content_hash FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    return row["content_hash"] if row else None


def _delete_document_artifacts(conn: sqlite3.Connection, doc_id: str) -> None:
    """Remove chunks, vec_chunks, FTS rows, and outgoing edges for a doc."""
    chunk_rowids = [
        row["rowid"]
        for row in conn.execute(
            "SELECT rowid FROM chunks WHERE doc_id = ?", (doc_id,)
        )
    ]
    if chunk_rowids:
        placeholders = ",".join("?" for _ in chunk_rowids)
        conn.execute(
            f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})",
            chunk_rowids,
        )
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM documents_fts WHERE id = ?", (doc_id,))
    conn.execute("DELETE FROM edges WHERE source_id = ?", (doc_id,))


def delete_document(conn: sqlite3.Connection, doc_id: str) -> None:
    """Fully remove a document from the index, including the documents row.

    Drops the chunks, vec rows, FTS row, and outbound edges (via
    `_delete_document_artifacts`) and then the `documents` row itself. Inbound
    edges (where this doc is the *target*) are intentionally left in place so
    they become dangling, matching the engine's link-resolution model where an
    unresolved target stays dangling rather than being guessed. Used by ingest
    to prune deleted/renamed source files.
    """
    _delete_document_artifacts(conn, doc_id)
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def upsert_document(conn: sqlite3.Connection, doc: ParsedDocument) -> None:
    """Insert or replace a document and clear any prior chunks/edges/FTS rows."""
    import json
    from datetime import datetime, timezone

    _delete_document_artifacts(conn, doc.id)

    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT created_at FROM documents WHERE id = ?", (doc.id,)
    ).fetchone()
    created_at = existing["created_at"] if existing else now

    conn.execute(
        """
        INSERT OR REPLACE INTO documents
            (
                id, title, path, domain, content_hash, index_id, trust_profile,
                namespace, source_root, source_path, display_path,
                timeline_text, metadata_json, created_at, updated_at
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc.id,
            doc.title,
            doc.path,
            doc.metadata.get("domain"),
            doc.content_hash,
            doc.index_id,
            doc.trust_profile,
            doc.namespace,
            doc.source_root,
            doc.source_path,
            doc.display_path,
            doc.timeline_text,
            json.dumps(doc.metadata, default=str),
            created_at,
            now,
        ),
    )

    conn.execute(
        "INSERT INTO documents_fts (id, title, content) VALUES (?, ?, ?)",
        (doc.id, doc.title, doc.truth_text),
    )


def insert_chunks_and_embeddings(
    conn: sqlite3.Connection,
    doc_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
) -> None:
    if len(chunks) != len(embeddings):
        raise DatabaseError(
            f"chunk/embedding count mismatch for {doc_id}: "
            f"{len(chunks)} chunks vs {len(embeddings)} embeddings"
        )
    for idx, (text, embedding) in enumerate(zip(chunks, embeddings)):
        cursor = conn.execute(
            "INSERT INTO chunks (doc_id, chunk_index, text) VALUES (?, ?, ?)",
            (doc_id, idx, text),
        )
        rowid = cursor.lastrowid
        conn.execute(
            "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)",
            (rowid, _serialize_embedding(embedding)),
        )


def insert_edges(conn: sqlite3.Connection, edges: Iterable[GraphEdge]) -> None:
    for edge in edges:
        conn.execute(
            """
            INSERT OR IGNORE INTO edges (source_id, target_id, relationship_type)
            VALUES (?, ?, ?)
            """,
            (edge.source_id, edge.target_id, edge.relationship_type),
        )


def get_outbound_edge_keys(
    conn: sqlite3.Connection, source_id: str
) -> set[tuple[str, str | None]]:
    """Return the set of (target_id, relationship_type) for a source's edges.

    Lets the ingest skip-path detect when a re-resolved edge set is identical to
    what is already stored, so it can avoid a redundant DELETE+INSERT write.
    """
    return {
        (row["target_id"], row["relationship_type"])
        for row in conn.execute(
            "SELECT target_id, relationship_type FROM edges WHERE source_id = ?",
            (source_id,),
        )
    }


def replace_edges(
    conn: sqlite3.Connection, source_id: str, edges: Iterable[GraphEdge]
) -> None:
    """Replace all outbound edges for one source document."""
    conn.execute("DELETE FROM edges WHERE source_id = ?", (source_id,))
    insert_edges(conn, edges)


if __name__ == "__main__":
    init_db()
    print("Database schema initialized successfully.")
