"""Opt-in projection that nominates at most one already-expanded graph row.

This module does not retrieve, fuse, traverse, or rewrite `run_query` output.
It reads that output and the stored authored edges, then returns zero or one
`GraphAdmission`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence

from mindgraph.models import GraphAdmission, NeighborResult, QueryResult
from mindgraph.query import QueryError, list_neighbors

POLICY_VERSION = "ga1"
TOKEN_LIMIT = 50
BASE_SIGNALS = frozenset({"lexical", "semantic", "fused"})


def measured_chunk_tokens(text: str) -> int:
    """RC1 chunk-token measure: whitespace-separated tokens, not model tokens."""
    return len(text.split())


def project_graph_admissions(
    conn: sqlite3.Connection,
    results: Sequence[QueryResult],
    *,
    query_text: str,
    k: int,
    scope_index: str | None = None,
) -> list[GraphAdmission]:
    """Return `[]` or `[GraphAdmission]` for one already-produced result list.

    `k` is the ordinary consumer cutoff. Rows at positions `1..min(k, len)`
    stay the preserved prefix and are never admitted. When the whole output
    is shorter than or equal to `k`, there is nothing beyond the prefix.
    """
    if k < 1 or len(results) <= k:
        return []

    prefix = list(results[:k])
    prefix_doc_ids = {row.doc_id for row in prefix}
    prefix_hashes = {row.content_hash for row in prefix if _present(row.content_hash)}
    seeds = _eligible_seeds(results)
    if not seeds:
        return []

    for candidate in results[k:]:
        if not _candidate_shape_ok(candidate, prefix_doc_ids, prefix_hashes):
            continue
        try:
            bound = _bind_seed_edge(conn, candidate, seeds)
        except QueryError:
            return []
        if bound is None:
            continue
        seed_position, seed, edge = bound
        token_count = measured_chunk_tokens(candidate.chunk_text)
        bound_scope = scope_index if _present(scope_index) else (
            candidate.index_id if _present(candidate.index_id) else None
        )
        payload = identity_payload(
            query_text=query_text,
            scope_index=bound_scope,
            k=k,
            seed_doc_id=seed.doc_id,
            seed_content_hash=seed.content_hash or "",
            seed_position=seed_position,
            candidate_doc_id=candidate.doc_id,
            candidate_content_hash=candidate.content_hash or "",
            edge_source_id=edge.source_id,
            edge_target_id=edge.target_id,
            edge_source_path=edge.source_path or "",
            edge_target_path=edge.target_path or "",
            relationship_type=edge.relationship_type,
        )
        return [
            GraphAdmission(
                admission_id=admission_id(payload),
                result=candidate,
                seed_doc_id=seed.doc_id,
                seed_content_hash=seed.content_hash or "",
                seed_position=seed_position,
                edge_source_id=edge.source_id,
                edge_target_id=edge.target_id,
                edge_source_path=edge.source_path or "",
                edge_target_path=edge.target_path or "",
                relationship_type=edge.relationship_type,
                freshness="UNKNOWN",
                raw_status=candidate.status,
                chunk_token_count=token_count,
                token_limit=TOKEN_LIMIT,
            )
        ]
    return []


def identity_payload(
    *,
    query_text: str,
    scope_index: str | None,
    k: int,
    seed_doc_id: str,
    seed_content_hash: str,
    seed_position: int,
    candidate_doc_id: str,
    candidate_content_hash: str,
    edge_source_id: str,
    edge_target_id: str,
    edge_source_path: str,
    edge_target_path: str,
    relationship_type: str | None,
) -> dict:
    """Versioned canonical payload. Every key is always present, including nulls."""
    return {
        "policy_version": POLICY_VERSION,
        "query_sha256": hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
        "scope_index": scope_index,
        "k": k,
        "seed_doc_id": seed_doc_id,
        "seed_content_hash": seed_content_hash,
        "seed_position": seed_position,
        "candidate_doc_id": candidate_doc_id,
        "candidate_content_hash": candidate_content_hash,
        "edge_source_id": edge_source_id,
        "edge_target_id": edge_target_id,
        "edge_source_path": edge_source_path,
        "edge_target_path": edge_target_path,
        "relationship_type": relationship_type,
    }


def admission_id(payload: dict) -> str:
    """`ga1:<sha256>` over compact UTF-8 JSON with sorted keys."""
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{POLICY_VERSION}:{digest}"


def _eligible_seeds(
    results: Sequence[QueryResult],
) -> list[tuple[int, QueryResult]]:
    """First three base rows, keeping one-based positions when one is ineligible."""
    base = [
        row
        for row in results
        if row.expansion_depth == 0 and row.signal in BASE_SIGNALS
    ]
    eligible: list[tuple[int, QueryResult]] = []
    for position, seed in enumerate(base[:3], start=1):
        if seed.citation_class == "not_citable":
            continue
        if not _present(seed.doc_id) or not _present(seed.path) or not _present(
            seed.content_hash
        ):
            continue
        eligible.append((position, seed))
    return eligible


def _candidate_shape_ok(
    candidate: QueryResult,
    prefix_doc_ids: set[str],
    prefix_hashes: set[str],
) -> bool:
    if candidate.signal != "expanded" or candidate.expansion_depth != 1:
        return False
    if candidate.citation_class == "not_citable":
        return False
    if not _present(candidate.doc_id) or not _present(candidate.path) or not _present(
        candidate.content_hash
    ):
        return False
    if candidate.doc_id in prefix_doc_ids:
        return False
    if candidate.content_hash in prefix_hashes:
        return False
    if measured_chunk_tokens(candidate.chunk_text) > TOKEN_LIMIT:
        return False
    return True


def _bind_seed_edge(
    conn: sqlite3.Connection,
    candidate: QueryResult,
    seeds: Sequence[tuple[int, QueryResult]],
) -> tuple[int, QueryResult, NeighborResult] | None:
    """Lowest eligible seed, then that seed's first consistent neighbor-order edge."""
    for position, seed in seeds:
        matched = _first_target_edge(conn, seed.doc_id, candidate.doc_id)
        if matched is None:
            continue
        if not _edge_consistent(matched, seed, candidate):
            continue
        return position, seed, matched
    return None


def _first_target_edge(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> NeighborResult | None:
    for edge in list_neighbors(conn, source_id):
        if edge.target_id == target_id:
            return edge
    return None


def _edge_consistent(
    edge: NeighborResult, seed: QueryResult, candidate: QueryResult
) -> bool:
    return bool(
        edge.source_id == seed.doc_id
        and edge.target_id == candidate.doc_id
        and _present(edge.source_path)
        and _present(edge.target_path)
        and edge.source_path == seed.path
        and edge.target_path == candidate.path
    )


def _present(value: str | None) -> bool:
    return isinstance(value, str) and value != ""
