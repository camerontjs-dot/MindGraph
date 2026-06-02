import sqlite3
import struct
from collections.abc import Iterable

import sqlite_vec

from mindgraph.exceptions import DatabaseError
from mindgraph.models import GraphEdge, ParsedDocument


def get_db(db_path: str = "mindgraph.sqlite") -> sqlite3.Connection:
    """Connect to the SQLite database and load the sqlite-vec extension."""
    try:
        conn = sqlite3.connect(db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    except sqlite3.Error as e:
        raise DatabaseError(f"Failed to open database at {db_path}: {e}") from e


def init_db(db_path: str = "mindgraph.sqlite") -> sqlite3.Connection:
    """Initialize the database schema for MindGraph."""
    conn = get_db(db_path)

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT,
                path TEXT,
                domain TEXT,
                content_hash TEXT NOT NULL,
                timeline_text TEXT,
                metadata_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

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

        # 384 dimensions for all-MiniLM-L6-v2
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                embedding float[384]
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS edges (
                source_id TEXT,
                target_id TEXT,
                relationship_type TEXT,
                PRIMARY KEY (source_id, target_id, relationship_type)
            )
        """)

    return conn


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
            (id, title, path, domain, content_hash, timeline_text, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc.id,
            doc.title,
            doc.path,
            doc.metadata.get("domain"),
            doc.content_hash,
            doc.timeline_text,
            json.dumps(doc.metadata),
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


def replace_edges(
    conn: sqlite3.Connection, source_id: str, edges: Iterable[GraphEdge]
) -> None:
    """Replace all outbound edges for one source document."""
    conn.execute("DELETE FROM edges WHERE source_id = ?", (source_id,))
    insert_edges(conn, edges)


if __name__ == "__main__":
    init_db()
    print("Database schema initialized successfully.")
