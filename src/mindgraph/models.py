from typing import Literal

from pydantic import BaseModel, Field

Signal = Literal["lexical", "semantic", "fused", "expanded"]


class GraphEdge(BaseModel):
    source_id: str
    target_id: str
    relationship_type: str | None = None


class ParsedDocument(BaseModel):
    id: str
    title: str
    path: str
    content_hash: str
    metadata: dict = Field(default_factory=dict)
    truth_text: str
    timeline_text: str | None = None


class QueryResult(BaseModel):
    """One ranked retrieval result. See DECISIONS.md § Phase 2 query path."""

    doc_id: str
    chunk_index: int
    path: str
    title: str
    signal: Signal
    rrf_score: float
    lexical_rank: int | None
    semantic_rank: int | None
    chunk_text: str
    expansion_depth: int = 0


class NeighborResult(BaseModel):
    """One outbound edge from a source document. Dangling edges have null paths."""

    source_id: str
    target_id: str
    relationship_type: str | None
    source_path: str | None
    target_path: str | None
