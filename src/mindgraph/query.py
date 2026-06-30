"""Query path: lexical plus semantic retrieval fused with Reciprocal Rank Fusion.

Design is locked in DECISIONS.md § 2026-05-19 — Phase 2 query path. The fusion
constant RRF_K is canonical (Cormack, Clarke, Buettcher 2009) and not configurable
at runtime by design.
"""

import json
import logging
import re
import sqlite3
import struct
from typing import Protocol

from mindgraph.embedders import EmbedTemplate, EmbedderSpec, format_query_text
from mindgraph.exceptions import DatabaseError, MindgraphError
from mindgraph.models import NeighborResult, QueryResult, QueryScopeWarning

logger = logging.getLogger(__name__)

RRF_K = 60
DEFAULT_LEXICAL_TOP_K = 20
DEFAULT_SEMANTIC_TOP_K = 20
DEFAULT_FINAL_TOP_K = 10

# Best-semantic-distance cutoff above which a semantic-only result is flagged
# weak_fit ("this index probably has no answer"). Calibrated against the live
# MainFrame index (Phase 7 ADR): strong hits land near 0.71 (top-5 <= 0.82),
# out-of-scope queries near 1.10, nonsense near 1.22, so 1.0 sits in the gap.
WEAK_FIT_DISTANCE_THRESHOLD = 1.0

_INBOX_SCOPE_RE = re.compile(
    r"\b(inbox|captures?|routing queue|ready queue|waiting for routing|"
    r"00_inbox|01_ingest)\b",
    re.IGNORECASE,
)
_PROJECT_SCOPE_RE = re.compile(
    r"\b(30_projects|project status|active project|project_state|"
    r"next_action|next action|project readme|state\.md|handoff|next gate)\b",
    re.IGNORECASE,
)
_LIVE_FRESHNESS_RE = re.compile(
    r"\b(current|latest|today|this week|this month|right now|now|recent|"
    r"live|as of)\b",
    re.IGNORECASE,
)
_LIVE_STATE_RE = re.compile(
    r"\b(job hunt|finance|calendar|workflow metrics|telemetry|live state|"
    r"status|blocked?|blockers?|remaining|next)\b",
    re.IGNORECASE,
)

# A word-character run. Everything else — apostrophes, question marks, and the
# rest of `.,;/=%[]<>\|~@#$&!` plus the FTS5 operator symbols `"*():^-` — is a
# token boundary. `\w` is unicode-aware for str patterns, and every `\w+` run is
# a valid FTS5 bareword (ASCII alphanumerics/underscore and/or codepoints >=128),
# so an OR-join of these tokens can never produce a MATCH syntax error.
_FTS5_TOKEN = re.compile(r"\w+")

# FTS5 reserved operator keywords. FTS5 treats lowercase `and`, `or`, `not`,
# `near` as ordinary tokens, so only the uppercase forms are dropped.
_FTS5_OPERATOR_KEYWORDS = frozenset({"AND", "OR", "NOT", "NEAR"})


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
    """Reduce free text to a safe OR-joined FTS5 MATCH expression.

    Uses an allowlist: only word-character runs survive as tokens, so arbitrary
    punctuation (apostrophes, question marks, etc.) can never reach the MATCH
    parser, and the uppercase operator keywords AND/OR/NOT/NEAR are dropped so
    they are not interpreted as operators. The result is an implicit OR over
    surviving tokens, per the ADR. Returns an empty string when no tokens
    survive (e.g. operator- or punctuation-only input).
    """
    tokens = [
        token
        for token in _FTS5_TOKEN.findall(text)
        if token not in _FTS5_OPERATOR_KEYWORDS
    ]
    if not tokens:
        return ""
    return " OR ".join(tokens)


def classify_query_scope(query_text: str) -> QueryScopeWarning | None:
    """Return an advisory lifecycle-scope warning for current-state queries.

    This is deliberately a query-intent guardrail, not a no-answer classifier.
    A query can have strong lexical and semantic matches while still asking the
    wrong database for inbox, live, or project-status state.
    """
    if _INBOX_SCOPE_RE.search(query_text):
        return QueryScopeWarning(
            intent="inbox_state",
            recommended_trust_profile="inbox_or_ingest_queue",
            message=(
                "Query appears to ask for inbox or routing-queue state. "
                "Route it to the live inbox/ingest surface before treating "
                "ranked chunks as current-state nominations."
            ),
        )
    if _PROJECT_SCOPE_RE.search(query_text):
        return QueryScopeWarning(
            intent="project_status",
            recommended_trust_profile="project_status",
            message=(
                "Query appears to ask for project status. Route it to a "
                "project-status database or inspect the project records before "
                "treating ranked chunks as current-state nominations."
            ),
        )
    if _LIVE_FRESHNESS_RE.search(query_text) and _LIVE_STATE_RE.search(query_text):
        return QueryScopeWarning(
            intent="live_state",
            recommended_trust_profile="time_bound_live_state",
            message=(
                "Query appears to ask for current or live state. A durable "
                "knowledge database can return relevant background notes that "
                "are not current-status nominations."
            ),
        )
    return None


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
) -> list[tuple[str, int, int, float]]:
    """Return (doc_id, chunk_index, rank, distance) ordered by vec_chunks distance.

    Promotes to document granularity by keeping the best-ranked chunk per
    document, per ADR § retrieval pipeline. Ranks start at 1 and reflect the
    chunk-level KNN position before dedup. A document's rank and distance are
    those of its first (closest) chunk in the chunk-level ranking. The distance
    is carried through fusion for the Phase 7 weak-fit signal.
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

    seen: dict[str, tuple[int, int, float]] = {}
    for rank, row in enumerate(rows, start=1):
        doc_id = row["doc_id"]
        if doc_id not in seen:
            seen[doc_id] = (row["chunk_index"], rank, row["distance"])
    return [
        (doc_id, chunk_index, rank, distance)
        for doc_id, (chunk_index, rank, distance) in seen.items()
    ]


def rrf_fuse(
    lexical: list[tuple[str, int]],
    semantic: list[tuple[str, int, int, float]],
    top_k: int = DEFAULT_FINAL_TOP_K,
) -> list[tuple[str, int | None, float, int | None, int | None, float | None]]:
    """Fuse lexical and semantic rankings with RRF at the canonical k = 60.

    Returns (doc_id, chunk_index, rrf_score, lexical_rank, semantic_rank,
    semantic_distance). The chunk_index and distance come from the semantic
    ranking when present; lexical-only results carry None for both and the
    chunk is filled in by the caller with chunk 0 (see ADR § Phase 2 §
    lexical-only chunk choice). The distance rides along untouched — it does not
    enter the RRF math, which stays rank-based per the locked Phase 2 ADR. Sort
    order: rrf_score descending, then doc_id ascending. Deterministic.
    """
    if top_k <= 0:
        return []
    lex_map = {doc_id: rank for doc_id, rank in lexical}
    sem_map = {
        doc_id: (chunk_index, rank, distance)
        for doc_id, chunk_index, rank, distance in semantic
    }
    doc_ids = set(lex_map) | set(sem_map)

    scored: list[
        tuple[str, int | None, float, int | None, int | None, float | None]
    ] = []
    for doc_id in doc_ids:
        lex_rank = lex_map.get(doc_id)
        sem_entry = sem_map.get(doc_id)
        sem_rank = sem_entry[1] if sem_entry else None
        chunk_index = sem_entry[0] if sem_entry else None
        sem_distance = sem_entry[2] if sem_entry else None

        score = 0.0
        if lex_rank is not None:
            score += 1.0 / (RRF_K + lex_rank)
        if sem_rank is not None:
            score += 1.0 / (RRF_K + sem_rank)

        scored.append((doc_id, chunk_index, score, lex_rank, sem_rank, sem_distance))

    scored.sort(key=lambda row: (-row[2], row[0]))
    return scored[:top_k]


def _attribute_signal(lex_rank: int | None, sem_rank: int | None):
    if lex_rank is not None and sem_rank is not None:
        return "fused"
    if lex_rank is not None:
        return "lexical"
    return "semantic"


def _is_weak_fit(
    lex_rank: int | None, sem_rank: int | None, distance: float | None
) -> bool:
    """Flag a result the index probably has no real answer for.

    Per the Phase 7 ADR: a result is weak only when it was nominated purely by
    semantics (no lexical/fused overlap to corroborate it) and its best chunk
    distance is worse than the calibrated threshold. A lexical or fused hit
    always carries independent evidence, so it is never weak; expanded graph
    results carry no distance and stay False.
    """
    return (
        lex_rank is None
        and sem_rank is not None
        and distance is not None
        and distance > WEAK_FIT_DISTANCE_THRESHOLD
    )


def _resolve_chunk_text(
    conn: sqlite3.Connection, doc_id: str, chunk_index: int
) -> str:
    row = conn.execute(
        "SELECT text FROM chunks WHERE doc_id = ? AND chunk_index = ?",
        (doc_id, chunk_index),
    ).fetchone()
    return row["text"] if row else ""


def _coerce_optional_str(value) -> str | None:
    """Normalize a frontmatter scalar to a string, or None when absent/empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    """Resolve a document's display + frontmatter fields, or None if missing.

    Returns path, title, provenance, and the `type`/`domain`/`status`
    frontmatter values. `domain` is read from its dedicated column;
    `doc_type`/`status` come from the stored `metadata_json`. All three are
    null when the source omits them.
    """
    row = conn.execute(
        """
        SELECT
            path, title, domain, metadata_json, index_id, trust_profile,
            namespace, source_root, source_path, display_path
        FROM documents
        WHERE id = ?
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    metadata = {}
    if row["metadata_json"]:
        try:
            parsed = json.loads(row["metadata_json"])
            if isinstance(parsed, dict):
                metadata = parsed
        except (ValueError, TypeError):
            metadata = {}
    return {
        "path": row["path"],
        "title": row["title"],
        "doc_type": _coerce_optional_str(metadata.get("type")),
        "domain": _coerce_optional_str(row["domain"]),
        "status": _coerce_optional_str(metadata.get("status")),
        "index_id": _coerce_optional_str(row["index_id"]),
        "trust_profile": _coerce_optional_str(row["trust_profile"]),
        "namespace": _coerce_optional_str(row["namespace"]),
        "source_root": _coerce_optional_str(row["source_root"]),
        "source_path": _coerce_optional_str(row["source_path"]),
        "display_path": _coerce_optional_str(row["display_path"]),
    }


DEFAULT_EXPAND_DEPTH = 1
DEFAULT_EXPAND_TOP_K = 20
DEFAULT_ASSOCIATE_TOP_K = 10
DEFAULT_ASSOCIATE_SEED_K = 5


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
    associate: bool = False,
    associate_top_k: int = DEFAULT_ASSOCIATE_TOP_K,
    associate_seed_k: int = DEFAULT_ASSOCIATE_SEED_K,
    embedder_spec: EmbedderSpec | None = None,
    embed_template: EmbedTemplate = "none",
) -> list[QueryResult]:
    """Run the Phase 2 query pipeline end-to-end and return QueryResult rows.

    Lexical-only results surface chunk 0 by default because there is no semantic
    ranking to pick a better chunk from. This is a minor v0.1 simplification
    recorded in DECISIONS.md.

    When `expand` is True, walks outbound `[[link]]` edges from the Phase 2
    results to `expand_depth` hops and appends the walked documents with
    `signal="expanded"`. See DECISIONS.md § 2026-05-20 — Phase 3 graph expansion.
    """
    query_scope_warning = classify_query_scope(query_text)
    try:
        lexical = fetch_lexical_ranking(conn, query_text, top_k=lexical_top_k)
    except QueryError as exc:
        # A lexical failure must not sink the whole query: fall back to
        # semantic-only retrieval with a logged warning. The sanitizer makes
        # this path unreachable for ordinary punctuation, but it guards against
        # a malformed FTS index or any future MATCH edge case.
        logger.warning(
            "lexical ranking failed; degrading to semantic-only: %s", exc
        )
        lexical = []

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
    for doc_id, chunk_index, rrf_score, lex_rank, sem_rank, sem_distance in fused:
        resolved = _resolve_document(conn, doc_id)
        if resolved is None:
            # FTS5 row exists but documents row was deleted out from under us.
            # Treat as a data-integrity failure rather than silently dropping.
            raise QueryError(
                f"FTS5 hit for doc_id={doc_id} has no matching documents row"
            )
        effective_chunk_index = chunk_index if chunk_index is not None else 0
        chunk_text = _resolve_chunk_text(conn, doc_id, effective_chunk_index)
        results.append(
            QueryResult(
                doc_id=doc_id,
                chunk_index=effective_chunk_index,
                path=resolved["path"],
                title=resolved["title"],
                doc_type=resolved["doc_type"],
                domain=resolved["domain"],
                status=resolved["status"],
                index_id=resolved["index_id"],
                trust_profile=resolved["trust_profile"],
                namespace=resolved["namespace"],
                source_root=resolved["source_root"],
                source_path=resolved["source_path"],
                display_path=resolved["display_path"],
                signal=_attribute_signal(lex_rank, sem_rank),
                rrf_score=round(rrf_score, 6),
                lexical_rank=lex_rank,
                semantic_rank=sem_rank,
                semantic_distance=(
                    round(sem_distance, 6) if sem_distance is not None else None
                ),
                weak_fit=_is_weak_fit(lex_rank, sem_rank, sem_distance),
                query_scope_warning=query_scope_warning,
                chunk_text=chunk_text,
                expansion_depth=0,
            )
        )

    output = list(results)

    if expand:
        output.extend(
            expand_results(
                conn,
                results,
                depth=expand_depth,
                expand_top_k=expand_top_k,
                query_scope_warning=query_scope_warning,
            )
        )

    if associate:
        output.extend(
            associate_results(
                conn,
                results,
                embedder,
                top_k=associate_top_k,
                seed_k=associate_seed_k,
                embedder_spec=embedder_spec,
                embed_template=embed_template,
                query_scope_warning=query_scope_warning,
            )
        )

    return output


def _association_seed_text(conn: sqlite3.Connection, seed: QueryResult) -> str:
    chunk = _resolve_chunk_text(conn, seed.doc_id, seed.chunk_index)
    title = (seed.title or "").strip()
    excerpt = chunk[:500].strip()
    if title and excerpt:
        return f"{title}\n{excerpt}"
    return title or excerpt


def associate_results(
    conn: sqlite3.Connection,
    phase_2_results: list[QueryResult],
    embedder: Embedder,
    *,
    top_k: int = DEFAULT_ASSOCIATE_TOP_K,
    seed_k: int = DEFAULT_ASSOCIATE_SEED_K,
    embedder_spec: EmbedderSpec | None = None,
    embed_template: EmbedTemplate = "none",
    query_scope_warning: QueryScopeWarning | None = None,
) -> list[QueryResult]:
    """Find embedding-neighbor documents from Phase 2 fused seeds.

    Seeds are the pre-expand Phase 2 rows only (ADR-034). Association is
    append-only: rows use signal=associated, rrf_score=0, association_depth=1.
    """
    if top_k <= 0 or not phase_2_results:
        return []
    if query_scope_warning is None:
        query_scope_warning = phase_2_results[0].query_scope_warning

    spec = embedder_spec
    effective_seed_k = min(seed_k, len(phase_2_results)) if seed_k > 0 else 0
    if effective_seed_k <= 0:
        return []

    seen: set[str] = {r.doc_id for r in phase_2_results}
    associated: list[QueryResult] = []

    for seed in phase_2_results[:effective_seed_k]:
        seed_text = _association_seed_text(conn, seed)
        if not seed_text:
            continue
        if spec is not None:
            seed_text = format_query_text(spec, seed_text, template=embed_template)
        raw = _encode_without_progress(embedder, [seed_text])
        query_embedding = [float(x) for x in raw[0]]
        semantic = fetch_semantic_ranking(
            conn, query_embedding, top_k=top_k + len(seen)
        )
        for doc_id, chunk_index, _rank, distance in semantic:
            if doc_id in seen:
                continue
            resolved = _resolve_document(conn, doc_id)
            if resolved is None:
                continue
            chunk_text = _resolve_chunk_text(conn, doc_id, chunk_index)
            associated.append(
                QueryResult(
                    doc_id=doc_id,
                    chunk_index=chunk_index,
                    path=resolved["path"],
                    title=resolved["title"],
                    doc_type=resolved["doc_type"],
                    domain=resolved["domain"],
                    status=resolved["status"],
                    index_id=resolved["index_id"],
                    trust_profile=resolved["trust_profile"],
                    namespace=resolved["namespace"],
                    source_root=resolved["source_root"],
                    source_path=resolved["source_path"],
                    display_path=resolved["display_path"],
                    signal="associated",
                    rrf_score=0.0,
                    lexical_rank=None,
                    semantic_rank=None,
                    semantic_distance=round(distance, 6),
                    weak_fit=_is_weak_fit(None, 1, distance),
                    query_scope_warning=query_scope_warning,
                    chunk_text=chunk_text,
                    expansion_depth=0,
                    association_depth=1,
                )
            )
            seen.add(doc_id)
            if len(associated) >= top_k:
                break
        if len(associated) >= top_k:
            break

    associated.sort(
        key=lambda r: (r.association_depth, r.semantic_distance or 0.0, r.doc_id)
    )
    return associated[:top_k]


def expand_results(
    conn: sqlite3.Connection,
    phase_2_results: list[QueryResult],
    *,
    depth: int,
    expand_top_k: int,
    query_scope_warning: QueryScopeWarning | None = None,
) -> list[QueryResult]:
    """Walk outbound graph edges from the Phase 2 seeds and return expanded rows.

    Deterministic BFS per DECISIONS.md § 2026-05-20 — Phase 3 graph expansion.
    Dangling targets terminate the walk at their depth. Documents already in the
    seed set are not re-emitted. Final sort: (expansion_depth, doc_id, chunk_index).
    """
    if depth <= 0 or not phase_2_results:
        return []
    if query_scope_warning is None:
        query_scope_warning = phase_2_results[0].query_scope_warning

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
                chunk_text = _resolve_chunk_text(conn, target_id, 0)
                expanded.append(
                    QueryResult(
                        doc_id=target_id,
                        chunk_index=0,
                        path=resolved["path"],
                        title=resolved["title"],
                        doc_type=resolved["doc_type"],
                        domain=resolved["domain"],
                        status=resolved["status"],
                        index_id=resolved["index_id"],
                        trust_profile=resolved["trust_profile"],
                        namespace=resolved["namespace"],
                        source_root=resolved["source_root"],
                        source_path=resolved["source_path"],
                        display_path=resolved["display_path"],
                        signal="expanded",
                        rrf_score=0.0,
                        lexical_rank=None,
                        semantic_rank=None,
                        query_scope_warning=query_scope_warning,
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
