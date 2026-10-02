"""Canonical nominations and explicitly index-bound expansion (Stage 1A).

Projection retains nom1 identity, rank, exact preview and provenance. exp2
locators distinguish scope aliases from stored index identity. Legacy exp1
locators must be requeried; they cannot safely prove the new binding.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence

from mindgraph.exceptions import MindgraphError
from mindgraph.models import Nomination, NominationExpansion, QueryResult
from mindgraph import expansion_binding as binding

POLICY_VERSION = "nom1"
EXPANSION_VERSION = binding.VERSION
PREVIEW_CHARS = 280


class NominationError(MindgraphError):
    """Expansion failed closed; no substituted source is returned."""


def measured_chunk_tokens(text: str) -> int:
    """RC1 whitespace token count, not a destination tokenizer count."""
    return len(text.split())


def preview_text(chunk_text: str, limit: int = PREVIEW_CHARS) -> tuple[str, bool]:
    flat = (chunk_text or "").strip().replace("\n", " ")
    if len(flat) > limit:
        return flat[: max(0, limit)], True
    return flat, False


def retrieval_reasons(result: QueryResult) -> list[str]:
    if result.signal == "lexical":
        return ["lexical_match"]
    if result.signal == "semantic":
        return ["semantic_match"] + (["weak_fit"] if result.weak_fit else [])
    if result.signal == "fused":
        return ["lexical_match", "semantic_match", "fused_rank"]
    if result.signal == "expanded":
        return ["graph_expansion"]
    if result.signal == "associated":
        return ["semantic_association"] + (["weak_fit"] if result.weak_fit else [])
    return [result.signal]


def _present(value: str | None) -> bool:
    return isinstance(value, str) and value != ""


def _bound_scope(scope_index: str | None, result: QueryResult) -> str | None:
    if _present(scope_index):
        return scope_index
    if _present(result.index_id):
        return result.index_id
    return None


def nomination_identity_payload(*, query_text: str, scope_index: str | None,
                                doc_id: str, content_hash: str | None,
                                chunk_index: int, signal: str, path: str | None) -> dict:
    # Preserve the qualified nom1 payload exactly, including null fields.
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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{POLICY_VERSION}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def encode_expansion_handle(*, scope_index: str | None, doc_id: str,
                            chunk_index: int, content_hash: str | None,
                            index_id: str | None = None, namespace: str | None = None,
                            path: str | None = None) -> str:
    """Encode exp2 without inventing unavailable index identity.

    A nomination with unknown index identity can still be inspected compactly,
    but redemption will fail explicitly until a scoped index is available.
    """
    try:
        return binding.encode_handle(scope=scope_index, index_id=index_id,
                                     namespace=namespace, path=path, doc_id=doc_id,
                                     chunk_index=chunk_index, content_hash=content_hash)
    except binding.ExpansionBindingError as exc:
        raise NominationError(str(exc)) from exc


def parse_expansion_handle(handle: str) -> dict:
    try:
        return binding.decode_handle(handle)
    except binding.ExpansionBindingError as exc:
        raise NominationError(str(exc)) from exc


def project_nominations(results: Sequence[QueryResult], *, query_text: str,
                        scope_index: str | None = None) -> list[Nomination]:
    """Project without ranking, filtering, mutating or summarizing source rows."""
    nominations: list[Nomination] = []
    for result in results:
        bound_scope = _bound_scope(scope_index, result)
        payload = nomination_identity_payload(
            query_text=query_text, scope_index=bound_scope, doc_id=result.doc_id,
            content_hash=result.content_hash, chunk_index=result.chunk_index,
            signal=result.signal, path=result.path,
        )
        preview, truncated = preview_text(result.chunk_text)
        nominations.append(Nomination(
            nomination_id=nomination_id(payload),
            expansion_handle=encode_expansion_handle(
                scope_index=bound_scope, doc_id=result.doc_id,
                chunk_index=result.chunk_index, content_hash=result.content_hash,
                index_id=result.index_id, namespace=result.namespace, path=result.path,
            ),
            title=result.title, preview=preview, preview_truncated=truncated,
            preview_chars=len(preview),
            chunk_token_count=measured_chunk_tokens(result.chunk_text or ""),
            doc_id=result.doc_id, path=result.path, source_path=result.source_path,
            display_path=result.display_path, content_hash=result.content_hash,
            chunk_index=result.chunk_index, citation_class=result.citation_class,
            provenance_warning=result.provenance_warning, trust_profile=result.trust_profile,
            index_id=result.index_id, namespace=result.namespace, doc_type=result.doc_type,
            domain=result.domain, freshness="UNKNOWN", raw_status=result.status,
            signal=result.signal, retrieval_reasons=retrieval_reasons(result),
            lexical_rank=result.lexical_rank, semantic_rank=result.semantic_rank,
            rrf_score=result.rrf_score, semantic_distance=result.semantic_distance,
            weak_fit=result.weak_fit, expansion_depth=result.expansion_depth,
            association_depth=result.association_depth,
            query_scope_warning=result.query_scope_warning, scope_index=bound_scope,
        ))
    return nominations


def resolve_expansion(conn: sqlite3.Connection, handle: str, *,
                      scope_index: str | None = None) -> NominationExpansion:
    """Validate the selected index and target before returning any source text.

    A savepoint holds one read snapshot through identity validation, chunk read
    and metadata resolution. It preserves an existing caller transaction and
    does not write, migrate or repair the index.
    """
    parsed = parse_expansion_handle(handle)
    opened = False
    try:
        conn.execute("SAVEPOINT mindgraph_bound_expansion")
        opened = True
        doc_row, hash_match = binding.validate_target(conn, parsed, requested_scope=scope_index)
        chunk_row = conn.execute(
            "SELECT text FROM chunks WHERE doc_id=? AND chunk_index=?",
            (parsed["doc_id"], parsed["chunk_index"]),
        ).fetchone()
        if chunk_row is None:
            raise NominationError("expansion target missing: chunk not in selected index")
        from mindgraph import query as query_mod
        resolved = query_mod._resolve_document(conn, parsed["doc_id"])
        if resolved is None:
            raise NominationError("expansion target missing: source metadata unavailable")
        expanded = NominationExpansion(
            expansion_handle=handle, doc_id=parsed["doc_id"], path=resolved["path"],
            title=resolved["title"], chunk_index=parsed["chunk_index"],
            chunk_text=chunk_row[0], content_hash=doc_row["content_hash"],
            content_hash_match=hash_match, freshness="UNKNOWN", raw_status=resolved["status"],
            citation_class=resolved["citation_class"], provenance_warning=resolved["provenance_warning"],
            trust_profile=resolved["trust_profile"], index_id=resolved["index_id"],
            namespace=resolved["namespace"], doc_type=resolved["doc_type"], domain=resolved["domain"],
            source_path=resolved["source_path"], display_path=resolved["display_path"],
            scope_index=scope_index if scope_index is not None else parsed["scope"],
        )
        conn.execute("RELEASE mindgraph_bound_expansion")
        opened = False
        return expanded
    except (binding.ExpansionBindingError, sqlite3.Error) as exc:
        raise NominationError(str(exc) if isinstance(exc, binding.ExpansionBindingError)
                              else "expansion failed: index read unavailable") from exc
    finally:
        if opened:
            conn.execute("ROLLBACK TO mindgraph_bound_expansion")
            conn.execute("RELEASE mindgraph_bound_expansion")
