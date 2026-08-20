"""Query path: lexical plus semantic retrieval fused with Reciprocal Rank Fusion.

Design is locked in DECISIONS.md § 2026-05-19 — Phase 2 query path. The fusion
constant RRF_K is canonical (Cormack, Clarke, Buettcher 2009) and not configurable
at runtime by design.
"""

import json
import logging
import os
import re
import sqlite3
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Protocol

from mindgraph.embedders import EmbedTemplate, EmbedderSpec, format_query_text
from mindgraph.exceptions import DatabaseError, MindgraphError
from mindgraph.models import NeighborResult, QueryResult, QueryScopeWarning

logger = logging.getLogger(__name__)

RRF_K = 60
DEFAULT_LEXICAL_TOP_K = 20
DEFAULT_SEMANTIC_TOP_K = 20
DEFAULT_FINAL_TOP_K = 10
MAX_QUERY_TOP_K = 1000
MAX_EXPAND_DEPTH = 3

# Best-semantic-distance cutoff above which a semantic-only result is flagged
# weak_fit ("this index probably has no answer"). Calibrated against the live
# MainFrame index (Phase 7 ADR): strong hits land near 0.71 (top-5 <= 0.82),
# out-of-scope queries near 1.10, nonsense near 1.22, so 1.0 sits in the gap.
WEAK_FIT_DISTANCE_THRESHOLD = 1.0

# Scope-warning vocabulary.
#
# Each entry is a regular-expression *fragment*, not a literal, so `captures?`
# and `state\.md` behave as written. Fragments are joined with `|` and wrapped
# in `\b(...)\b`, matched case-insensitively.
#
# The defaults below describe one vault's lifecycle vocabulary. They are a
# starting point, not a claim about anyone else's notes. Override them with a
# JSON file (see `load_scope_vocabulary`) when your own material uses different
# words for "this is current state, not durable knowledge".

DEFAULT_INBOX_TERMS = (
    "inbox", "captures?", "routing queue", "ready queue",
    "waiting for routing", "00_inbox", "01_ingest",
)
DEFAULT_PROJECT_TERMS = (
    "30_projects", "project status", "active project", "project_state",
    "next_action", "next action", "project readme", r"state\.md",
    "handoff", "next gate",
)
DEFAULT_FRESHNESS_TERMS = (
    "current", "latest", "today", "this week", "this month", "right now",
    "now", "recent", "live", "as of",
)
DEFAULT_LIVE_STATE_TERMS = (
    "job hunt", "finance", "calendar", "workflow metrics", "telemetry",
    "live state", "status", "blocked?", "blockers?", "remaining", "next",
)


def _compile_terms(terms: Sequence[str]) -> re.Pattern[str] | None:
    """Compile alternation fragments into `\\b(a|b|c)\\b`, or None if empty."""
    kept = [t for t in terms if t]
    if not kept:
        return None
    return re.compile(r"\b(" + "|".join(kept) + r")\b", re.IGNORECASE)


@dataclass(frozen=True)
class ScopeVocabulary:
    """Term fragments driving `classify_query_scope`.

    An empty term list disables that warning branch entirely.
    """

    inbox_terms: tuple[str, ...] = DEFAULT_INBOX_TERMS
    project_terms: tuple[str, ...] = DEFAULT_PROJECT_TERMS
    freshness_terms: tuple[str, ...] = DEFAULT_FRESHNESS_TERMS
    live_state_terms: tuple[str, ...] = DEFAULT_LIVE_STATE_TERMS

    @cached_property
    def _patterns(self) -> tuple[re.Pattern[str] | None, ...]:
        return (
            _compile_terms(self.inbox_terms),
            _compile_terms(self.project_terms),
            _compile_terms(self.freshness_terms),
            _compile_terms(self.live_state_terms),
        )

    @classmethod
    def from_mapping(cls, data: dict) -> "ScopeVocabulary":
        """Build from a mapping. Absent keys keep their defaults."""
        known = {f: getattr(cls, f, None) for f in cls.__dataclass_fields__}
        unknown = sorted(set(data) - set(known))
        if unknown:
            raise QueryError(
                f"unknown scope vocabulary key(s): {', '.join(unknown)}; "
                f"expected any of: {', '.join(sorted(known))}"
            )
        kwargs = {}
        for field in cls.__dataclass_fields__:
            if field in data:
                value = data[field]
                if isinstance(value, str) or not isinstance(value, Sequence):
                    raise QueryError(
                        f"scope vocabulary key {field!r} must be a list of strings"
                    )
                kwargs[field] = tuple(str(v) for v in value)
        return cls(**kwargs)


DEFAULT_SCOPE_VOCABULARY = ScopeVocabulary()

# Environment variable naming a JSON file that overrides the defaults.
SCOPE_VOCABULARY_ENV = "MINDGRAPH_SCOPE_VOCABULARY"


def load_scope_vocabulary(path: str | os.PathLike[str]) -> ScopeVocabulary:
    """Load a scope vocabulary from a JSON file. Absent keys keep defaults."""
    resolved = Path(path).expanduser()
    try:
        data = json.loads(resolved.read_text())
    except FileNotFoundError:
        raise QueryError(f"scope vocabulary file not found: {resolved}")
    except json.JSONDecodeError as exc:
        raise QueryError(f"scope vocabulary file is not valid JSON: {resolved} ({exc})")
    if not isinstance(data, dict):
        raise QueryError(f"scope vocabulary file must contain a JSON object: {resolved}")
    return ScopeVocabulary.from_mapping(data)


@lru_cache(maxsize=1)
def _vocabulary_from_env(raw: str | None) -> ScopeVocabulary:
    return load_scope_vocabulary(raw) if raw else DEFAULT_SCOPE_VOCABULARY


def active_scope_vocabulary() -> ScopeVocabulary:
    """The vocabulary in force: the env-var override, else the defaults."""
    return _vocabulary_from_env(os.environ.get(SCOPE_VOCABULARY_ENV))

# A word-character run. Everything else — apostrophes, question marks, and the
# rest of `.,;/=%[]<>\|~@#$&!` plus the FTS5 operator symbols `"*():^-` — is a
# token boundary. `\w` is unicode-aware for str patterns, and every `\w+` run is
# a valid FTS5 bareword (ASCII alphanumerics/underscore and/or codepoints >=128),
# so an OR-join of these tokens can never produce a MATCH syntax error.
_FTS5_TOKEN = re.compile(r"\w+")

# FTS5 reserved operator keywords. FTS5 treats lowercase `and`, `or`, `not`,
# `near` as ordinary tokens, so only the uppercase forms are dropped.
_FTS5_OPERATOR_KEYWORDS = frozenset({"AND", "OR", "NOT", "NEAR"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SHA256_TAGGED = re.compile(r"^sha256:[0-9a-f]{64}$")


class QueryError(MindgraphError):
    """Raised when a query execution fails."""


def _validate_limit(name: str, value: int, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryError(f"{name} must be an integer")
    if value < 0 or value > maximum:
        raise QueryError(f"{name} must be between 0 and {maximum}")
    return value


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


def classify_query_scope(
    query_text: str, vocabulary: ScopeVocabulary | None = None
) -> QueryScopeWarning | None:
    """Return an advisory lifecycle-scope warning for current-state queries.

    This is deliberately a query-intent guardrail, not a no-answer classifier.
    A query can have strong lexical and semantic matches while still asking the
    wrong database for inbox, live, or project-status state.

    The terms driving it are configurable; see `ScopeVocabulary`. Passing None
    uses `active_scope_vocabulary()`, which honours the
    `MINDGRAPH_SCOPE_VOCABULARY` environment variable.
    """
    vocab = vocabulary if vocabulary is not None else active_scope_vocabulary()
    inbox_re, project_re, freshness_re, live_state_re = vocab._patterns
    if inbox_re and inbox_re.search(query_text):
        return QueryScopeWarning(
            intent="inbox_state",
            recommended_trust_profile="inbox_or_ingest_queue",
            message=(
                "Query appears to ask for inbox or routing-queue state. "
                "Route it to the live inbox/ingest surface before treating "
                "ranked chunks as current-state nominations."
            ),
        )
    if project_re and project_re.search(query_text):
        return QueryScopeWarning(
            intent="project_status",
            recommended_trust_profile="project_status",
            message=(
                "Query appears to ask for project status. Route it to a "
                "project-status database or inspect the project records before "
                "treating ranked chunks as current-state nominations."
            ),
        )
    if (
        freshness_re
        and live_state_re
        and freshness_re.search(query_text)
        and live_state_re.search(query_text)
    ):
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
    _validate_limit("lexical_top_k", top_k, maximum=MAX_QUERY_TOP_K)
    if top_k == 0:
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
    _validate_limit("semantic_top_k", top_k, maximum=MAX_QUERY_TOP_K)
    if top_k == 0:
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
    _validate_limit("final_top_k", top_k, maximum=MAX_QUERY_TOP_K)
    if top_k == 0:
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


#: Frontmatter states that make a document non-citable regardless of how well
#: its text matches a query.
_NON_CITABLE_STATUS = {"quarantined", "retracted", "superseded"}
_NON_CITABLE_TAGS = {"fabricated-citation", "quarantined", "needs-resourcing"}


def _citation_assessment(metadata: dict) -> tuple[str, str | None]:
    """Classify a document's citability and build its trust warning.

    Returns `(citation_class, warning)` where citation_class is one of
    `citable`, `unverified`, or `not_citable`.

    The two travel together deliberately. Before 2026-08-20 only the prose
    warning existed, so the sole thing standing between a caller and a
    fabricated source was that the caller read a sentence and chose to obey it.
    Ranking is trust-blind — a fabricated document is written to be on topic and
    so scores like any other — and on live queries quarantined documents from
    the 2026-08-09 incident took the top rank. A machine-readable class lets a
    consumer partition on trust without parsing English.

    `unverified` is deliberately not `not_citable`: a needs-audit capture is a
    legitimate retrieval nomination, and collapsing the two would either hide
    real candidates or launder fabricated ones into the same bucket.
    """
    status = str(metadata.get("status") or "").strip().lower()
    raw_tags = metadata.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [t.strip(" '\"[]") for t in raw_tags.split(",")]
    tags = {str(t).strip().lower() for t in raw_tags}

    if status in _NON_CITABLE_STATUS or (tags & _NON_CITABLE_TAGS):
        reason = metadata.get("citation_status") or metadata.get("provenance")
        detail = f" ({reason})" if reason else ""
        return "not_citable", (
            f"NOT CITABLE — this document is {status or 'flagged'}{detail}. "
            "Its content may be correct but has no verified source. "
            "Do not cite it; re-source any claim before use."
        )
    if "needs-audit" in tags or "full-text-pending" in tags:
        return "unverified", (
            "UNVERIFIED — this capture is flagged needs-audit / full-text-pending. "
            "Treat as a nomination, not as evidence."
        )
    return "citable", None


def _provenance_warning(metadata: dict) -> str | None:
    """Trust warning only. See `_citation_assessment` for the classification."""
    return _citation_assessment(metadata)[1]


def partition_by_citation(
    results: list["QueryResult"],
) -> tuple[list["QueryResult"], list["QueryResult"]]:
    """Split ranked results into usable candidates and non-citable ones.

    Order within each list is preserved, so ranking is unchanged — this
    separates, it does not re-rank and it does not drop. A caller that wants
    everything can concatenate the two back together.

    `unverified` stays with the candidates. It means "nomination, not evidence",
    which is a caveat on use, not a bar on it. Only `not_citable` — quarantined,
    retracted, superseded, or tagged as a fabricated citation — is separated,
    because that is the class a caller must not cite at all.
    """
    candidates = [r for r in results if r.citation_class != "not_citable"]
    excluded = [r for r in results if r.citation_class == "not_citable"]
    return candidates, excluded


def citation_counts(results: list["QueryResult"]) -> dict[str, int]:
    """Count results by citation class, always reporting all three keys."""
    counts = {"citable": 0, "unverified": 0, "not_citable": 0}
    for result in results:
        counts[result.citation_class] = counts.get(result.citation_class, 0) + 1
    return counts


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
            path, title, domain, content_hash, metadata_json, index_id, trust_profile,
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
        "content_hash": _coerce_optional_str(row["content_hash"]),
        "doc_type": _coerce_optional_str(metadata.get("type")),
        "domain": _coerce_optional_str(row["domain"]),
        "status": _coerce_optional_str(metadata.get("status")),
        "provenance_warning": _citation_assessment(metadata)[1],
        "citation_class": _citation_assessment(metadata)[0],
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
    for name, value in (
        ("lexical_top_k", lexical_top_k),
        ("semantic_top_k", semantic_top_k),
        ("final_top_k", final_top_k),
        ("expand_top_k", expand_top_k),
        ("associate_top_k", associate_top_k),
        ("associate_seed_k", associate_seed_k),
    ):
        _validate_limit(name, value, maximum=MAX_QUERY_TOP_K)
    _validate_limit("expand_depth", expand_depth, maximum=MAX_EXPAND_DEPTH)

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
                provenance_warning=resolved["provenance_warning"],
                    citation_class=resolved["citation_class"],
                index_id=resolved["index_id"],
                trust_profile=resolved["trust_profile"],
                namespace=resolved["namespace"],
                source_root=resolved["source_root"],
                source_path=resolved["source_path"],
                display_path=resolved["display_path"],
                content_hash=resolved["content_hash"],
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
    _validate_limit("associate_top_k", top_k, maximum=MAX_QUERY_TOP_K)
    _validate_limit("associate_seed_k", seed_k, maximum=MAX_QUERY_TOP_K)
    if top_k == 0 or seed_k == 0 or not phase_2_results:
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
        candidate_k = min(MAX_QUERY_TOP_K, top_k + len(seen))
        semantic = fetch_semantic_ranking(conn, query_embedding, top_k=candidate_k)
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
                provenance_warning=resolved["provenance_warning"],
                    citation_class=resolved["citation_class"],
                    index_id=resolved["index_id"],
                    trust_profile=resolved["trust_profile"],
                    namespace=resolved["namespace"],
                    source_root=resolved["source_root"],
                    source_path=resolved["source_path"],
                    display_path=resolved["display_path"],
                    content_hash=resolved["content_hash"],
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
    _validate_limit("expand_depth", depth, maximum=MAX_EXPAND_DEPTH)
    _validate_limit("expand_top_k", expand_top_k, maximum=MAX_QUERY_TOP_K)
    if depth == 0 or expand_top_k == 0 or not phase_2_results:
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
                provenance_warning=resolved["provenance_warning"],
                    citation_class=resolved["citation_class"],
                        index_id=resolved["index_id"],
                        trust_profile=resolved["trust_profile"],
                        namespace=resolved["namespace"],
                        source_root=resolved["source_root"],
                        source_path=resolved["source_path"],
                        display_path=resolved["display_path"],
                        content_hash=resolved["content_hash"],
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


def _filter_by_c0_eligibility(
    results: list[QueryResult],
    eligibility_manifest: dict[str, Any] | None,
) -> list[QueryResult]:
    """Keep only results with exact C-0 manifest membership; stamp the run id.

    Validates the manifest contract, then admits a result only when its
    ``doc_id``, normalized source path, and indexed content hash all match one
    approved record. Admitted rows are copies carrying the consumed
    ``eligibility_run_id``. This applies no seat or character limit — see
    ``_allocate_context_budget`` for that half. Behaviour is identical to the
    filtering half of ``apply_dual_gate_governance`` before the split
    (DECISIONS.md § 2026-07-27, MainFrame ADR-047).
    """
    manifest_run_id, approved = _approved_manifest_records(eligibility_manifest)

    eligible: list[QueryResult] = []
    for result in results:
        approved_record = approved.get(result.doc_id)
        if approved_record is None:
            continue
        source_path = _result_source_path(result)
        content_hash = _indexed_hash(result.content_hash)
        if source_path != approved_record["path"]:
            continue
        if content_hash != approved_record["sha256"]:
            continue
        eligible.append(result.model_copy(update={"eligibility_run_id": manifest_run_id}))
    return eligible


def _allocate_context_budget(
    results: list[QueryResult],
    max_seats: int,
    max_chars: int,
    quiet_keywords: list[str] | None,
) -> list[QueryResult]:
    """Shortlist seats, apply the ``quiet_keywords`` swap, then the char budget.

    Allocation is subtractive at document granularity: every returned row comes
    from ``results`` in input order, though the trailing row may be a truncated
    copy. This makes no eligibility judgement and stamps no provenance. The
    logic is the allocation half of ``apply_dual_gate_governance`` before the
    split, preserved verbatim (DECISIONS.md § 2026-07-27, MainFrame ADR-047).
    """
    if max_seats < 0 or max_chars < 0:
        raise QueryError("max_seats and max_chars must be non-negative")
    if max_seats == 0:
        return []

    if quiet_keywords and len(results) > max_seats:
        shortlisted = results[:max_seats]
        for candidate in results[max_seats:]:
            candidate_text = (candidate.chunk_text or "").lower()
            if any(keyword.lower() in candidate_text for keyword in quiet_keywords):
                shortlisted[-1] = candidate
                break
    else:
        shortlisted = results[:max_seats]

    allocated: list[QueryResult] = []
    current_chars = 0
    for item in shortlisted:
        passage = item.chunk_text or ""
        if current_chars + len(passage) <= max_chars:
            allocated.append(item)
            current_chars += len(passage)
            continue
        remaining = max_chars - current_chars
        if remaining > 100:
            allocated.append(item.model_copy(update={"chunk_text": passage[:remaining] + "..."}))
        break
    return allocated


def apply_dual_gate_governance(
    results: list[QueryResult],
    max_seats: int = 3,
    max_chars: int = 4000,
    quiet_keywords: list[str] | None = None,
    *,
    eligibility_manifest: dict[str, Any] | None,
) -> list[QueryResult]:
    """Return manifest-approved, seat-bounded context without an ungated fallback.

    This is a consumer-side boundary over already-ranked retrieval nominations.
    It does not alter ``run_query`` ranking or make a truth/compliance claim.
    ``quiet_keywords`` remains a caller-directed ordering input, never a way to
    admit a result that lacks C-0 membership.

    Composition order is load-bearing: filtering runs first, so manifest
    validation still raises before the negative-argument check and the
    ``max_seats == 0`` early return, exactly as it did before the split.
    """
    return _allocate_context_budget(
        _filter_by_c0_eligibility(results, eligibility_manifest),
        max_seats,
        max_chars,
        quiet_keywords,
    )


def filter_by_c0_eligibility(
    results: list[QueryResult],
    *,
    eligibility_manifest: dict[str, Any] | None,
) -> list[QueryResult]:
    """C-0 filtering with no allocation step — the C-0-only evaluation arm.

    Returns every manifest-approved result, with no seat count and no character
    budget applied. This exists so the C-0-only arm of the dual-gate 2x2 differs
    from the governed arm by an absent step rather than by limits set high
    enough to be inert. Not exported, not on the CLI or MCP surface.
    """
    return _filter_by_c0_eligibility(results, eligibility_manifest)


def allocate_ungoverned_context(
    results: list[QueryResult],
    max_seats: int = 3,
    max_chars: int = 4000,
    quiet_keywords: list[str] | None = None,
    *,
    evaluation_use_only: Literal[True],
    **rejected_keywords: Any,
) -> list[QueryResult]:
    """Allocate seats and characters with NO C-0 gate — evaluation instrument only.

    The returned context is **not** C-0 governed. Nothing here checks source
    eligibility, so this must not feed a governed answer path; it exists solely
    so the Speaker-only arm of the dual-gate 2x2 can run allocation without
    filtering. Allocation is subtractive, so this reaches no source that ungated
    ``run_query`` did not already return.

    ``evaluation_use_only=True`` is keyword-only and has no default, so every
    call site carries a visible acknowledgement. The function takes no manifest,
    and never emits governance provenance: ``eligibility_run_id`` is cleared on
    the way in and asserted null on the way out, so a governed row fed back in
    cannot launder its run identity through this path.
    """
    if "eligibility_manifest" in rejected_keywords:
        raise QueryError(
            "allocate_ungoverned_context takes no eligibility manifest; "
            "use apply_dual_gate_governance for governed context"
        )
    if rejected_keywords:
        unexpected = next(iter(rejected_keywords))
        raise TypeError(
            "allocate_ungoverned_context() got an unexpected keyword argument "
            f"'{unexpected}'"
        )
    if evaluation_use_only is not True:
        raise QueryError(
            "allocate_ungoverned_context requires evaluation_use_only=True; "
            "ungoverned allocation is an evaluation instrument, not a governed path"
        )

    ungoverned = [
        result.model_copy(update={"eligibility_run_id": None}) for result in results
    ]
    allocated = _allocate_context_budget(ungoverned, max_seats, max_chars, quiet_keywords)
    assert all(item.eligibility_run_id is None for item in allocated), (
        "ungoverned allocation must never emit C-0 eligibility provenance"
    )
    return allocated


def _normalized_path(path: str) -> str:
    """Normalize a source path for manifest membership comparison only."""
    normalized = path.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.rstrip("/")


def _result_source_path(result: QueryResult) -> str | None:
    if result.source_root and result.source_path:
        return _normalized_path(f"{result.source_root}/{result.source_path}")
    if result.path:
        return _normalized_path(result.path)
    return None


def _indexed_hash(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.removeprefix("sha256:")
    return normalized if _SHA256_HEX.fullmatch(normalized) else None


def _approved_manifest_records(
    eligibility_manifest: dict[str, Any] | None,
) -> tuple[str, dict[str, dict[str, str]]]:
    """Validate the C-0 public boundary contract and index it by document id."""
    if not isinstance(eligibility_manifest, dict):
        raise QueryError("governed context requires a C-0 eligibility manifest")
    run_id = eligibility_manifest.get("eligibility_run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise QueryError("C-0 eligibility manifest has no eligibility_run_id")
    inventory = eligibility_manifest.get("approved_inventory")
    if not isinstance(inventory, list) or not inventory:
        raise QueryError("C-0 eligibility manifest approved_inventory is empty")

    records: dict[str, dict[str, str]] = {}
    for index, raw_record in enumerate(inventory):
        if not isinstance(raw_record, dict):
            raise QueryError(f"C-0 approved record {index} is not an object")
        doc_id = raw_record.get("doc_id")
        path = raw_record.get("path")
        sha256 = raw_record.get("sha256")
        status = raw_record.get("status")
        if not isinstance(doc_id, str) or not doc_id:
            raise QueryError(f"C-0 approved record {index} has no doc_id")
        if doc_id in records:
            raise QueryError(f"C-0 approved inventory has duplicate doc_id '{doc_id}'")
        if not isinstance(path, str) or not path:
            raise QueryError(f"C-0 approved record '{doc_id}' has no source path")
        if not isinstance(sha256, str) or not _SHA256_TAGGED.fullmatch(sha256):
            raise QueryError(
                f"C-0 approved record '{doc_id}' has malformed sha256; "
                "require sha256:<64 lowercase hex>"
            )
        if status not in ("Approved", "Effective"):
            raise QueryError(
                f"C-0 approved record '{doc_id}' has invalid status '{status}'"
            )
        records[doc_id] = {
            "path": _normalized_path(path),
            "sha256": sha256.removeprefix("sha256:"),
        }
    return run_id.strip(), records
