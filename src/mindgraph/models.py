from typing import Literal

from pydantic import BaseModel, Field

Signal = Literal["lexical", "semantic", "fused", "expanded", "associated"]
#: How far a document may be trusted as a source, independent of how well its
#: text matches a query. See `query._citation_assessment`.
CitationClass = Literal["citable", "unverified", "not_citable"]
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
    #: Absolute ingest root on the indexing machine, e.g. `/home/me/notes`.
    #: Excluded from serialization on purpose: it is an internal join key, and
    #: it is the only field on this model that is definitionally a host path.
    #: `path`, `source_path`, and `display_path` are all relative and already
    #: tell a consumer where the document lives, so nothing downstream needs
    #: the root. Query-time governance reads it in-process as an attribute
    #: (see `_result_source_path`), which `exclude` does not affect.
    #: Excluding it here rather than at each `model_dump()` call site means a
    #: serialization path added later cannot reintroduce the leak.
    source_root: str | None = Field(default=None, exclude=True)
    source_path: str | None = None
    display_path: str | None = None
    content_hash: str | None = None
    eligibility_run_id: str | None = None
    signal: Signal
    rrf_score: float
    lexical_rank: int | None
    semantic_rank: int | None
    semantic_distance: float | None = None
    weak_fit: bool = False
    #: Machine-readable citability, so a consumer can partition on trust
    #: without parsing `provenance_warning`. Ranking is trust-blind by design —
    #: a fabricated document is written to be on topic and scores accordingly —
    #: so this is the field that keeps a quarantined top hit out of a citable
    #: result set. `unverified` is a nomination, not a bar.
    citation_class: CitationClass = "citable"
    query_scope_warning: QueryScopeWarning | None = None
    #: Set when the source document is quarantined or otherwise not citable.
    #: Travels on EVERY chunk, which is the whole point: on 2026-08-09, 103
    #: captures with fabricated citations were found in `10_knowledge/`. Each
    #: carried a `needs-audit` tag and later a body banner — but a banner only
    #: appears in chunk 0, and a frontmatter tag never appears in chunk text at
    #: all. A query landing on chunk 3 returned authoritative-looking prose with
    #: no indication the source did not exist. A trust flag that is invisible at
    #: query time is not a control.
    provenance_warning: str | None = None
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


class GraphAdmission(BaseModel):
    """One opt-in graph-derived nomination.

    `result` is the expanded `QueryResult` already produced by retrieval.
    Nothing here assigns that row a fused rank. `freshness` stays UNKNOWN
    until a later decision stores a real freshness fact.
    """

    kind: Literal["graph_derived"] = "graph_derived"
    admission_id: str
    result: QueryResult
    seed_doc_id: str
    seed_content_hash: str
    seed_position: int = Field(ge=1, le=3)
    edge_source_id: str
    edge_target_id: str
    edge_source_path: str
    edge_target_path: str
    relationship_type: str | None = None
    freshness: Literal["UNKNOWN"] = "UNKNOWN"
    raw_status: str | None = None
    chunk_token_count: int = Field(ge=0)
    token_limit: int = Field(ge=1)


class Nomination(BaseModel):
    """One canonical nomination for an ordinary retrieval result.

    Stage 1A (issue #21): a stable structured projection of an existing
    lexical / semantic / fused (or appended expanded / associated) row
    without changing its ranking or meaning.

    The nomination carries enough information for a human or agent to decide
    whether to expand it, without receiving the full source chunk up front.
    `preview` is a deterministic exact extract (not a generated summary).
    `freshness` stays UNKNOWN until a later decision stores a real
    freshness fact. A nomination is not verification; expansion adds
    context, not authority.
    """

    kind: Literal["source_section"] = "source_section"
    nomination_id: str
    expansion_handle: str
    title: str
    preview: str
    preview_truncated: bool = False
    preview_chars: int = Field(ge=0)
    chunk_token_count: int = Field(ge=0)
    doc_id: str
    path: str
    source_path: str | None = None
    display_path: str | None = None
    content_hash: str | None = None
    chunk_index: int = Field(ge=0)
    citation_class: CitationClass = "citable"
    provenance_warning: str | None = None
    trust_profile: str | None = None
    index_id: str | None = None
    namespace: str | None = None
    doc_type: str | None = None
    domain: str | None = None
    freshness: Literal["UNKNOWN"] = "UNKNOWN"
    raw_status: str | None = None
    signal: Signal
    retrieval_reasons: list[str] = Field(default_factory=list)
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    rrf_score: float = 0.0
    semantic_distance: float | None = None
    weak_fit: bool = False
    expansion_depth: int = 0
    association_depth: int = 0
    query_scope_warning: QueryScopeWarning | None = None
    scope_index: str | None = None
    representation_level: Literal["chunk"] = "chunk"
    authority_note: Literal["nomination_only"] = "nomination_only"


class NominationExpansion(BaseModel):
    """Source-backed material for one expansion handle.

    Returned only by an explicit expansion request against the same
    identified index state. `content_hash_match` is True when the handle
    hash equals the stored hash, False is never returned with text (a
    mismatch fails closed instead), and None when the handle carried no
    hash. Expansion increases context, not epistemic authority.
    """

    kind: Literal["source_section_expansion"] = "source_section_expansion"
    expansion_handle: str
    doc_id: str
    path: str
    title: str
    chunk_index: int = Field(ge=0)
    chunk_text: str
    content_hash: str | None = None
    content_hash_match: bool | None = None
    freshness: Literal["UNKNOWN"] = "UNKNOWN"
    raw_status: str | None = None
    citation_class: CitationClass = "citable"
    provenance_warning: str | None = None
    trust_profile: str | None = None
    index_id: str | None = None
    namespace: str | None = None
    doc_type: str | None = None
    domain: str | None = None
    source_path: str | None = None
    display_path: str | None = None
    scope_index: str | None = None
    authority_note: Literal["expansion_adds_context_not_authority"] = (
        "expansion_adds_context_not_authority"
    )
