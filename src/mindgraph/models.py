from typing import Literal

from pydantic import BaseModel, Field

Signal = Literal["lexical", "semantic", "fused", "expanded", "associated"]
QueryScopeIntent = Literal["inbox_state", "live_state", "project_status"]


class GraphEdge(BaseModel):
    source_id: str
    target_id: str
    relationship_type: str | None = None


class ParsedDocument(BaseModel):
    id: str
    title: str
    path: str
    content_hash: str
    index_id: str | None = None
    trust_profile: str | None = None
    namespace: str | None = None
    source_root: str | None = None
    source_path: str | None = None
    display_path: str | None = None
    metadata: dict = Field(default_factory=dict)
    truth_text: str
    timeline_text: str | None = None


class QueryScopeWarning(BaseModel):
    """Query-level warning copied onto result rows.

    The warning means the query appears to ask for lifecycle state that may not
    belong in the current DB. It is advisory metadata, not a ranking signal.
    """

    intent: QueryScopeIntent
    recommended_trust_profile: str
    message: str


class QueryResult(BaseModel):
    """One ranked retrieval result. See DECISIONS.md § Phase 2 query path.

    `doc_type`/`domain`/`status` are surfaced from the document's frontmatter
    (`type`/`domain`/`status`) so a consumer can tell a raw capture from a
    curated note without a second lookup. They are null when the source has no
    such frontmatter key. They do not affect ranking — see the Phase 7 ADR.
    """

    doc_id: str
    chunk_index: int
    path: str
    title: str
    doc_type: str | None = None
    domain: str | None = None
    status: str | None = None
    index_id: str | None = None
    trust_profile: str | None = None
    namespace: str | None = None
    source_root: str | None = None
    source_path: str | None = None
    display_path: str | None = None
    signal: Signal
    rrf_score: float
    lexical_rank: int | None
    semantic_rank: int | None
    semantic_distance: float | None = None
    weak_fit: bool = False
    query_scope_warning: QueryScopeWarning | None = None
    chunk_text: str
    expansion_depth: int = 0
    association_depth: int = 0


class NeighborResult(BaseModel):
    """One outbound edge from a source document. Dangling edges have null paths."""

    source_id: str
    target_id: str
    relationship_type: str | None
    source_path: str | None
    target_path: str | None
