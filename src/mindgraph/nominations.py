"""Canonical nomination envelope + explicit expansion handles (Stage 1A).

This module projects already-ranked `QueryResult` rows into stable
`Nomination` objects and resolves transparent expansion handles back to
exact source-backed chunks. It does not retrieve, fuse, traverse, rerank,
rewrite, or summarize. Ranking and meaning come from `run_query`; this
module only nominates compactly and expands explicitly.

Design precedent: `graph_admission.py` (`ga1:<sha256>`, UNKNOWN freshness,
additive opt-in, no invented currentness).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
from collections.abc import Sequence

from mindgraph.exceptions import MindgraphError
from mindgraph.models import Nomination, NominationExpansion, QueryResult

POLICY_VERSION = "nom1"
EXPANSION_VERSION = "exp1"
#: Compact preview budget, matching the human CLI excerpt (`cli.py`).
PREVIEW_CHARS = 280
PREVIEW_ELLIPSIS = "..."


class NominationError(MindgraphError):
    """Raised when a nomination cannot be projected or expanded.

    Fail-closed by design: malformed, missing, stale, or scope-mismatched
    expansion targets raise instead of silently returning a different source.
    """


def measured_chunk_tokens(text: str) -> int:
    """RC1 chunk-token measure: whitespace-separated tokens, not model tokens."""
    return len(text.split())


def preview_text(chunk_text: str, limit: int = PREVIEW_CHARS) -> tuple[str, bool]:
    """Deterministic exact extract, not a generated summary.

    Mirrors the human CLI excerpt: strip, flatten newlines to spaces, then
    truncate to `limit` with an ellipsis marker. Returns (preview, truncated).
    """
    flat = (chunk_text or "").strip().replace("\n", " ")
    if len(flat) > limit:
        return flat[: max(0, limit - len(PREVIEW_ELLIPSIS))] + PREVIEW_ELLIPSIS, True
    return flat, False


def retrieval_reasons(result: QueryResult) -> list[str]:
    """Inspectable multi-signal reasons; never a flattened confidence value."""
    if result.signal == "lexical":
        return ["lexical_match"]
    if result.signal == "semantic":
        reasons = ["semantic_match"]
        if result.weak_fit:
            reasons.append("weak_fit")
        return reasons
    if result.signal == "fused":
        return ["lexical_match", "semantic_match", "fused_rank"]
    if result.signal == "expanded":
        return ["graph_expansion"]
    if result.signal == "associated":
        reasons = ["semantic_association"]
        if result.weak_fit:
            reasons.append("weak_fit")
        return reasons
    return [result.signal]


def _present(value: str | None) -> bool:
    return isinstance(value, str) and value != ""


def _bound_scope(scope_index: str | None, result: QueryResult) -> str | None:
    if _present(scope_index):
        return scope_index
    if _present(result.index_id):
        return result.index_id
    return None


def nomination_identity_payload(
    *,
    query_text: str,
    scope_index: str | None,
    doc_id: str,
    content_hash: str | None,
    chunk_index: int,
    signal: str,
    path: str | None,
) -> dict:
    """Versioned canonical payload. Every key is always present, including nulls."""
    return {
        "policy_version": POLICY_VERSION,
        "query_sha256": hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
        "scope_index": scope_index,
        "doc_id": doc_id,
        "content_hash": content_hash,
        "chunk_index": chunk_index,
        "signal": signal,
        "path": path,
    }


def nomination_id(payload: dict) -> str:
    """`nom1:<sha256>` over compact UTF-8 JSON with sorted keys."""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{POLICY_VERSION}:{digest}"


def _expansion_payload(
    *,
    scope_index: str | None,
    doc_id: str,
    chunk_index: int,
    content_hash: str | None,
) -> dict:
    """Canonical handle payload. Every key is always present, including nulls."""
    return {
        "v": EXPANSION_VERSION,
        "scope": scope_index,
        "doc_id": doc_id,
        "chunk_index": chunk_index,
        "content_hash": content_hash,
    }


def encode_expansion_handle(
    *,
    scope_index: str | None,
    doc_id: str,
    chunk_index: int,
    content_hash: str | None,
) -> str:
    """Transparent deterministic handle: `exp1:<base64url(canonical JSON)>`."""
    payload = _expansion_payload(
        scope_index=scope_index,
        doc_id=doc_id,
        chunk_index=chunk_index,
        content_hash=content_hash,
    )
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(encoded).decode("ascii")
    return f"{EXPANSION_VERSION}:{token}"


def parse_expansion_handle(handle: str) -> dict:
    """Decode and validate a handle without touching the database."""
    if not isinstance(handle, str) or not handle.startswith(f"{EXPANSION_VERSION}:"):
        raise NominationError(
            f"invalid expansion handle: must start with {EXPANSION_VERSION!r}"
        )
    token = handle.split(":", 1)[1]
    if not token:
        raise NominationError("invalid expansion handle: empty payload")
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise NominationError(f"invalid expansion handle: not base64url ({exc})") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise NominationError(f"invalid expansion handle: not JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise NominationError("invalid expansion handle: payload must be an object")
    if payload.get("v") != EXPANSION_VERSION:
        raise NominationError("invalid expansion handle: version mismatch")
    for key in ("scope", "doc_id", "chunk_index", "content_hash"):
        if key not in payload:
            raise NominationError(f"invalid expansion handle: missing {key!r}")
    doc_id = payload["doc_id"]
    chunk_index = payload["chunk_index"]
    scope = payload["scope"]
    content_hash = payload["content_hash"]
    if not _present(doc_id):
        raise NominationError("invalid expansion handle: doc_id is missing")
    if isinstance(chunk_index, bool) or not isinstance(chunk_index, int) or chunk_index < 0:
        raise NominationError("invalid expansion handle: chunk_index must be a non-negative int")
    if scope is not None and not isinstance(scope, str):
        raise NominationError("invalid expansion handle: scope must be a string or null")
    if content_hash is not None and not isinstance(content_hash, str):
        raise NominationError("invalid expansion handle: content_hash must be a string or null")
    if isinstance(content_hash, str) and content_hash == "":
        raise NominationError("invalid expansion handle: content_hash must not be empty")
    return {
        "v": EXPANSION_VERSION,
        "scope": scope,
        "doc_id": doc_id,
        "chunk_index": chunk_index,
        "content_hash": content_hash,
    }


def project_nominations(
    results: Sequence[QueryResult],
    *,
    query_text: str,
    scope_index: str | None = None,
) -> list[Nomination]:
    """Project every ranked row to a canonical nomination, preserving order.

    Pure projection: input rows are never mutated, reordered, or filtered.
    Unknowns stay UNKNOWN/null; no value is invented.
    """
    nominations: list[Nomination] = []
    for result in results:
        bound_scope = _bound_scope(scope_index, result)
        payload = nomination_identity_payload(
            query_text=query_text,
            scope_index=bound_scope,
            doc_id=result.doc_id,
            content_hash=result.content_hash,
            chunk_index=result.chunk_index,
            signal=result.signal,
            path=result.path,
        )
        preview, truncated = preview_text(result.chunk_text)
        nominations.append(
            Nomination(
                nomination_id=nomination_id(payload),
                expansion_handle=encode_expansion_handle(
                    scope_index=bound_scope,
                    doc_id=result.doc_id,
                    chunk_index=result.chunk_index,
                    content_hash=result.content_hash,
                ),
                title=result.title,
                preview=preview,
                preview_truncated=truncated,
                preview_chars=len(preview),
                chunk_token_count=measured_chunk_tokens(result.chunk_text or ""),
                doc_id=result.doc_id,
                path=result.path,
                source_path=result.source_path,
                display_path=result.display_path,
                content_hash=result.content_hash,
                chunk_index=result.chunk_index,
                citation_class=result.citation_class,
                provenance_warning=result.provenance_warning,
                trust_profile=result.trust_profile,
                index_id=result.index_id,
                namespace=result.namespace,
                doc_type=result.doc_type,
                domain=result.domain,
                freshness="UNKNOWN",
                raw_status=result.status,
                signal=result.signal,
                retrieval_reasons=retrieval_reasons(result),
                lexical_rank=result.lexical_rank,
                semantic_rank=result.semantic_rank,
                rrf_score=result.rrf_score,
                semantic_distance=result.semantic_distance,
                weak_fit=result.weak_fit,
                expansion_depth=result.expansion_depth,
                association_depth=result.association_depth,
                query_scope_warning=result.query_scope_warning,
                scope_index=bound_scope,
            )
        )
    return nominations


def _hashes_equal(first: str | None, second: str | None) -> bool | None:
    if first is None or second is None:
        return None
    return first.strip().lower() == second.strip().lower()


def resolve_expansion(
    conn: sqlite3.Connection,
    handle: str,
    *,
    scope_index: str | None = None,
) -> NominationExpansion:
    """Resolve a handle to exact source-backed material, fail-closed.

    No silent substitution: a missing document/chunk, a content-hash mismatch,
    a scope mismatch, or a malformed handle raises `NominationError`. A hash
    mismatch returns no text. Expansion adds context, not authority.
    """
    parsed = parse_expansion_handle(handle)
    handle_scope = parsed["scope"]
    if _present(handle_scope) and _present(scope_index) and handle_scope != scope_index:
        raise NominationError(
            f"expansion handle scope {handle_scope!r} does not match "
            f"requested scope {scope_index!r}"
        )
    bound_scope = scope_index if _present(scope_index) else handle_scope

    try:
        doc_row = conn.execute(
            """
            SELECT id, title, path, content_hash, metadata_json, index_id,
                   trust_profile, namespace, source_path, display_path
            FROM documents WHERE id = ?
            """,
            (parsed["doc_id"],),
        ).fetchone()
    except sqlite3.Error as exc:
        raise NominationError(f"expansion lookup failed: {exc}") from exc
    if doc_row is None:
        raise NominationError(
            f"expansion target missing: doc_id={parsed['doc_id']!r} not in this index"
        )
    stored_hash = doc_row["content_hash"] if "content_hash" in doc_row.keys() else None
    handle_hash = parsed["content_hash"]
    if _present(handle_hash) and _present(stored_hash):
        if not _hashes_equal(handle_hash, stored_hash):
            raise NominationError(
                "stale expansion handle: indexed content hash changed; "
                f"handle={handle_hash!r} stored={stored_hash!r}"
            )
        hash_match: bool | None = True
    else:
        hash_match = None

    try:
        chunk_row = conn.execute(
            "SELECT text FROM chunks WHERE doc_id = ? AND chunk_index = ?",
            (parsed["doc_id"], parsed["chunk_index"]),
        ).fetchone()
    except sqlite3.Error as exc:
        raise NominationError(f"expansion lookup failed: {exc}") from exc
    if chunk_row is None:
        raise NominationError(
            f"expansion target missing: chunk {parsed['chunk_index']} "
            f"for doc_id={parsed['doc_id']!r} not in this index"
        )

    # Reuse the single source of truth for citation/authority metadata.
    from mindgraph import query as query_mod

    resolved = query_mod._resolve_document(conn, parsed["doc_id"])
    if resolved is None:  # pragma: no cover - defensive; doc_row existed above
        raise NominationError(
            f"expansion target missing: doc_id={parsed['doc_id']!r} has no document row"
        )
    return NominationExpansion(
        expansion_handle=handle,
        doc_id=parsed["doc_id"],
        path=resolved["path"],
        title=resolved["title"],
        chunk_index=parsed["chunk_index"],
        chunk_text=chunk_row["text"] if "text" in chunk_row.keys() else "",
        content_hash=stored_hash,
        content_hash_match=hash_match,
        freshness="UNKNOWN",
        raw_status=resolved["status"],
        citation_class=resolved["citation_class"],
        provenance_warning=resolved["provenance_warning"],
        trust_profile=resolved["trust_profile"],
        index_id=resolved["index_id"],
        namespace=resolved["namespace"],
        doc_type=resolved["doc_type"],
        domain=resolved["domain"],
        source_path=resolved["source_path"],
        display_path=resolved["display_path"],
        scope_index=bound_scope,
    )
