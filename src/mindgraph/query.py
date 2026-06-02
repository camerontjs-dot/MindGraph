"""Query path: lexical plus semantic retrieval fused with Reciprocal Rank Fusion.

Design is locked in DECISIONS.md § 2026-05-19 — Phase 2 query path. The fusion
constant RRF_K is canonical (Cormack, Clarke, Buettcher 2009) and not configurable
at runtime by design.
"""

import re
import sqlite3
import struct
from typing import Protocol

from mindgraph.exceptions import DatabaseError, MindgraphError
from mindgraph.models import NeighborResult, QueryResult

RRF_K = 60
DEFAULT_LEXICAL_TOP_K = 20
DEFAULT_SEMANTIC_TOP_K = 20
DEFAULT_FINAL_TOP_K = 10

# FTS5 operator characters and uppercase keywords that must be stripped from
# free-text input before constructing a MATCH expression. FTS5 treats lowercase
# `and`, `or`, `not`, `near` as ordinary tokens, so only the uppercase forms are
# stripped as operators.
_FTS5_OPERATOR_CHARS = re.compile(r'["*():^\-]')
_FTS5_KEYWORD = re.compile(r"\b(?:AND|OR|NOT|NEAR)\b")


class QueryError(MindgraphError):
    """Raised when a query execution fails."""


class Embedder(Protocol):
    """The minimal interface the query path needs from a sentence embedder.

    Matches the surface of `sentence_transformers.SentenceTransformer.encode`
    that the ingest path already depends on. A test embedder can fulfill this
    by implementing `encode(texts, convert_to_numpy=True)` returning a 2D array.
    """

    def encode(self, texts, convert_to_numpy=True): ...  # pragma: no cover


def _encode_without_progress(embedder: Embedder, texts):
    """Encode text while suppressing sentence-transformers progress output."""
    try:
        return embedder.encode(
            texts, convert_to_numpy=True, show_progress_bar=False
        )
    except TypeError:
        return embedder.encode(texts, convert_to_numpy=True)


def sanitize_fts5_query(text: str) -> str:
    """Strip FTS5 operator syntax and produce an OR-joined MATCH expression.

    The result is an implicit OR over surviving tokens, per the ADR. Returns
    an empty string when no tokens survive the strip (e.g. operator-only input).
    """
    cleaned = _FTS5_OPERATOR_CHARS.sub(" ", text)
    cleaned = _FTS5_KEYWORD.sub(" ", cleaned)
    tokens = cleaned.split()
    if not tokens:
        return ""
    return " OR ".join(tokens)


def _serialize_embedding(vec) -> bytes:
    """Pack a float vector into the bytes format sqlite-vec expects."""
    return struct.pack(f"{len(vec)}f", *vec)


def fetch_lexical_ranking(
    conn: sqlite3.Connection,
    query_text: str,
    top_k: int = DEFAULT_LEXICAL_TOP_K,
) -> list[tuple[str, int]]:
    """Return (doc_id, rank) pairs from FTS5 BM25 ranking.

    Ranks start at 1. Returns an empty list when sanitization strips all tokens.
    """
    if top_k <= 0:
        return []
    match_expr = sanitize_fts5_query(query_text)
    if not match_expr:
        return []
    try:
        cursor = conn.execute(
            """
            SELECT id
            FROM documents_fts
            WHERE documents_fts MATCH ?
            ORDER BY bm25(documents_fts) ASC, id ASC
            LIMIT ?
            """,
            (match_expr, top_k),
        )
        return [(row["id"], rank + 1) for rank, row in enumerate(cursor)]
    except sqlite3.Error as e:
        raise QueryError(f"FTS5 query failed: {e}") from e


def fetch_semantic_ranking(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    top_k: int = DEFAULT_SEMANTIC_TOP_K,
) -> list[tuple[str, int, int]]:
    """Return (doc_id, chunk_index, rank) tuples ordered by vec_chunks distance.

    Promotes to document granularity by keeping the best-ranked chunk per
    document, per ADR § retrieval pipeline. Ranks start at 1 and reflect the
    chunk-level KNN position before dedup. A document's rank is the position
    of its first chunk in the chunk-level ranking.
    """
    if top_k <= 0:
        return []
    try:
        # sqlite-vec requires the LIMIT or `k = ?` constraint to be visible on
        # the vec0 virtual-table query itself. Wrapping the KNN in a subquery
        # keeps the constraint local to vec_chunks, then we join chunks for
        # the (doc_id, chunk_index) tuple.
        cursor = conn.execute(
            """
            SELECT c.doc_id, c.chunk_index, knn.distance
            FROM (
                SELECT rowid, distance
                FROM vec_chunks
                WHERE embedding MATCH ?
                  AND k = ?
            ) AS knn
            JOIN chunks c ON c.rowid = knn.rowid
            ORDER BY knn.distance ASC, c.doc_id ASC, c.chunk_index ASC
            """,
            (_serialize_embedding(query_embedding), top_k),
        )
        rows = cursor.fetchall()
    except sqlite3.Error as e:
        raise QueryError(f"vec_chunks query failed: {e}") from e

    seen: dict[str, tuple[int, int]] = {}
    for rank, row in enumerate(rows, start=1):
        doc_id = row["doc_id"]
        if doc_id not in seen:
            seen[doc_id] = (row["chunk_index"], rank)
    return [(doc_id, chunk_index, rank) for doc_id, (chunk_index, rank) in seen.items()]


def rrf_fuse(
    lexical: list[tuple[str, int]],
    semantic: list[tuple[str, int, int]],
    top_k: int = DEFAULT_FINAL_TOP_K,
) -> list[tuple[str, int | None, float, int | None, int | None]]:
    """Fuse lexical and semantic rankings with RRF at the canonical k = 60.

    Returns (doc_id, chunk_index, rrf_score, lexical_rank, semantic_rank). The
    chunk_index comes from the semantic ranking when present; lexical-only
    results carry None and are filled in by the caller with chunk 0 if no
    better chunk is available (see ADR § Phase 2 § lexical-only chunk choice).
    Sort order: rrf_score descending, then doc_id ascending. Deterministic.
    """
    if top_k <= 0:
        return []
    lex_map = {doc_id: rank for doc_id, rank in lexical}
    sem_map = {doc_id: (chunk_index, rank) for doc_id, chunk_index, rank in semantic}
    doc_ids = set(lex_map) | set(sem_map)

    scored: list[tuple[str, int | None, float, int | None, int | None]] = []
    for doc_id in doc_ids:
        lex_rank = lex_map.get(doc_id)
        sem_entry = sem_map.get(doc_id)
        sem_rank = sem_entry[1] if sem_entry else None
        chunk_index = sem_entry[0] if sem_entry else None

        score = 0.0
        if lex_rank is not None:
            score += 1.0 / (RRF_K + lex_rank)
        if sem_rank is not None:
            score += 1.0 / (RRF_K + sem_rank)

        scored.append((doc_id, chunk_index, score, lex_rank, sem_rank))

    scored.sort(key=lambda row: (-row[2], row[0]))
    return scored[:top_k]


def _attribute_signal(lex_rank: int | None, sem_rank: int | None):
    if lex_rank is not None and sem_rank is not None:
        return "fused"
    if lex_rank is not None:
        return "lexical"
    return "semantic"


def _resolve_chunk_text(
    conn: sqlite3.Connection, doc_id: str, chunk_index: int
) -> str:
    row = conn.execute(
        "SELECT text FROM chunks WHERE doc_id = ? AND chunk_index = ?",
        (doc_id, chunk_index),
    ).fetchone()
    return row["text"] if row else ""


def _resolve_document(
    conn: sqlite3.Connection, doc_id: str
) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT path, title FROM documents WHERE id = ?", (doc_id,)
    ).fetchone()
    if row is None:
        return None
    return row["path"], row["title"]


DEFAULT_EXPAND_DEPTH = 1
DEFAULT_EXPAND_TOP_K = 20


def run_query(
    conn: sqlite3.Connection,
    query_text: str,
    embedder: Embedder,
    *,
    lexical_top_k: int = DEFAULT_LEXICAL_TOP_K,
    semantic_top_k: int = DEFAULT_SEMANTIC_TOP_K,
    final_top_k: int = DEFAULT_FINAL_TOP_K,
    expand: bool = False,
    expand_depth: int = DEFAULT_EXPAND_DEPTH,
    expand_top_k: int = DEFAULT_EXPAND_TOP_K,
) -> list[QueryResult]:
    """Run the Phase 2 query pipeline end-to-end and return QueryResult rows.

    Lexical-only results surface chunk 0 by default because there is no semantic
    ranking to pick a better chunk from. This is a minor v0.1 simplification
    recorded in DECISIONS.md.

    When `expand` is True, walks outbound `[[link]]` edges from the Phase 2
    results to `expand_depth` hops and appends the walked documents with
    `signal="expanded"`. See DECISIONS.md § 2026-05-20 — Phase 3 graph expansion.
    """
    lexical = fetch_lexical_ranking(conn, query_text, top_k=lexical_top_k)

    if semantic_top_k > 0:
        raw = _encode_without_progress(embedder, [query_text])
        query_embedding = [float(x) for x in raw[0]]
        semantic = fetch_semantic_ranking(
            conn, query_embedding, top_k=semantic_top_k
        )
    else:
        semantic = []

    fused = rrf_fuse(lexical, semantic, top_k=final_top_k)

    results: list[QueryResult] = []
    for doc_id, chunk_index, rrf_score, lex_rank, sem_rank in fused:
        resolved = _resolve_document(conn, doc_id)
        if resolved is None:
            # FTS5 row exists but documents row was deleted out from under us.
            # Treat as a data-integrity failure rather than silently dropping.
            raise QueryError(
                f"FTS5 hit for doc_id={doc_id} has no matching documents row"
            )
        path, title = resolved
        effective_chunk_index = chunk_index if chunk_index is not None else 0
        chunk_text = _resolve_chunk_text(conn, doc_id, effective_chunk_index)
        results.append(
            QueryResult(
                doc_id=doc_id,
                chunk_index=effective_chunk_index,
                path=path,
                title=title,
                signal=_attribute_signal(lex_rank, sem_rank),
                rrf_score=round(rrf_score, 6),
                lexical_rank=lex_rank,
                semantic_rank=sem_rank,
                chunk_text=chunk_text,
                expansion_depth=0,
            )
        )

    if not expand:
        return results

    expanded = expand_results(
        conn, results, depth=expand_depth, expand_top_k=expand_top_k
    )
    return results + expanded


def expand_results(
    conn: sqlite3.Connection,
    phase_2_results: list[QueryResult],
    *,
    depth: int,
    expand_top_k: int,
) -> list[QueryResult]:
    """Walk outbound graph edges from the Phase 2 seeds and return expanded rows.

    Deterministic BFS per DECISIONS.md § 2026-05-20 — Phase 3 graph expansion.
    Dangling targets terminate the walk at their depth. Documents already in the
    seed set are not re-emitted. Final sort: (expansion_depth, doc_id, chunk_index).
    """
    if depth <= 0 or not phase_2_results:
        return []

    seen: set[str] = {r.doc_id for r in phase_2_results}
    frontier: list[str] = list(seen)
    expanded: list[QueryResult] = []

    for d in range(1, depth + 1):
        next_frontier: list[str] = []
        for source_id in frontier:
            for edge in list_neighbors(conn, source_id):
                target_id = edge.target_id
                if edge.target_path is None:
                    continue
                if target_id in seen:
                    continue
                resolved = _resolve_document(conn, target_id)
                if resolved is None:
                    # Defensive: list_neighbors already filtered dangling via
                    # target_path, so a missing documents row here is a data
                    # integrity issue worth surfacing.
                    continue
                path, title = resolved
                chunk_text = _resolve_chunk_text(conn, target_id, 0)
                expanded.append(
                    QueryResult(
                        doc_id=target_id,
                        chunk_index=0,
                        path=path,
                        title=title,
                        signal="expanded",
                        rrf_score=0.0,
                        lexical_rank=None,
                        semantic_rank=None,
                        chunk_text=chunk_text,
                        expansion_depth=d,
                    )
                )
                seen.add(target_id)
                next_frontier.append(target_id)
        if not next_frontier:
            break
        frontier = next_frontier

    expanded.sort(key=lambda r: (r.expansion_depth, r.doc_id, r.chunk_index))
    return expanded[:expand_top_k]


def list_neighbors(
    conn: sqlite3.Connection,
    doc_id: str,
) -> list[NeighborResult]:
    """List outbound edges from a document, preserving dangling edges.

    Sort order: target_id ascending, then relationship_type ascending (with
    NULL relationship_type sorting first per SQLite default).
    """
    try:
        cursor = conn.execute(
            """
            SELECT
                e.source_id,
                e.target_id,
                e.relationship_type,
                src.path AS source_path,
                tgt.path AS target_path
            FROM edges e
            LEFT JOIN documents src ON src.id = e.source_id
            LEFT JOIN documents tgt ON tgt.id = e.target_id
            WHERE e.source_id = ?
            ORDER BY e.target_id ASC, e.relationship_type ASC
            """,
            (doc_id,),
        )
        return [
            NeighborResult(
                source_id=row["source_id"],
                target_id=row["target_id"],
                relationship_type=row["relationship_type"],
                source_path=row["source_path"],
                target_path=row["target_path"],
            )
            for row in cursor
        ]
    except sqlite3.Error as e:
        raise QueryError(f"neighbors lookup failed: {e}") from e
