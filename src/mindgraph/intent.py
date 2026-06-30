"""Deterministic compilation and read-only traversal for V1 intent graphs.

Reviewed YAML is the source of truth. SQLite files produced here are generated
control-plane artifacts; they are deliberately separate from MindGraph's
document indexes and contain no embeddings or retrieved content.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mindgraph.exceptions import MindgraphError


SCHEMA_VERSION = "1"
NODE_KINDS = frozenset({"goal", "capability", "constraint"})
RELATIONS = frozenset(
    {"decomposes_to", "requires", "next_step", "blocked_by", "routes_to"}
)
ACYCLIC_RELATIONS = frozenset({"decomposes_to", "requires", "next_step"})
BINDING_SCHEMES = frozenset(
    {"knowledge", "project", "retriever", "capability", "policy"}
)
ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9]*(?:[.-][a-z0-9]+)*$")
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
REQUIRED_TABLES = frozenset(
    {
        "schema_meta",
        "graph_versions",
        "intent_nodes",
        "intent_aliases",
        "intent_edges",
        "intent_bindings",
        "intent_rules",
    }
)


class IntentGraphError(MindgraphError):
    """Base intent error with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        location: str | None = None,
        witness: tuple[str, ...] = (),
        graph_id: str | None = None,
        graph_version: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.location = location
        self.witness = witness
        self.graph_id = graph_id
        self.graph_version = graph_version

    def __str__(self) -> str:
        details = []
        if self.location:
            details.append(f"location={self.location}")
        if self.witness:
            details.append(f"witness={' -> '.join(self.witness)}")
        suffix = f" ({', '.join(details)})" if details else ""
        return f"{self.code}: {super().__str__()}{suffix}"


class IntentGraphValidationError(IntentGraphError):
    """Raised when reviewed source violates the V1 graph contract."""


class IntentVersionConflict(IntentGraphError):
    """Raised when compilation would mutate approved version history."""


class IntentStoreError(IntentGraphError):
    """Raised when a compiled store is missing or violates its schema."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_id(value: str) -> str:
    value = value.strip()
    if not ID_RE.fullmatch(value):
        raise ValueError("must be a lowercase dot/hyphen-separated stable ID")
    return value


def _require_nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return value


def _require_version(value: str) -> str:
    value = value.strip()
    if not VERSION_RE.fullmatch(value):
        raise ValueError("must be a lowercase dot/hyphen-separated version")
    return value


def _require_reference(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("must be a stable scheme://reference")
    return value


class GraphMetadata(_StrictModel):
    id: str
    version: str
    status: Literal["approved"]
    created_at: str
    reviewed_at: str
    reviewed_by: tuple[str, ...]
    source_refs: tuple[str, ...]
    supersedes: str | None = None

    _id = field_validator("id")(_require_id)
    _version = field_validator("version")(_require_version)
    _times = field_validator("created_at", "reviewed_at")(_require_nonempty)

    @field_validator("reviewed_by")
    @classmethod
    def validate_reviewers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({_require_nonempty(item) for item in value}))
        if not cleaned:
            raise ValueError("must contain at least one reviewer")
        return cleaned

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({_require_reference(item) for item in value}))
        if not cleaned:
            raise ValueError("must contain at least one source reference")
        return cleaned

    @field_validator("supersedes")
    @classmethod
    def validate_supersedes(cls, value: str | None) -> str | None:
        return _require_version(value) if value is not None else None


class IntentNodeSource(_StrictModel):
    id: str
    kind: Literal["goal", "capability", "constraint"]
    label: str
    aliases: tuple[str, ...] = ()
    status: Literal["active", "deprecated"] = "active"
    source_refs: tuple[str, ...]

    _id = field_validator("id")(_require_id)
    _label = field_validator("label")(_require_nonempty)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_require_nonempty(item) for item in value)

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({_require_reference(item) for item in value}))
        if not cleaned:
            raise ValueError("must contain at least one source reference")
        return cleaned


class IntentEdgeSource(_StrictModel):
    source_id: str
    target_id: str
    relation: str
    status: Literal["active", "deprecated"] = "active"
    source_refs: tuple[str, ...]

    _ids = field_validator("source_id", "target_id")(_require_id)
    _relation = field_validator("relation")(_require_nonempty)

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({_require_reference(item) for item in value}))
        if not cleaned:
            raise ValueError("must contain at least one source reference")
        return cleaned


class IntentBindingSource(_StrictModel):
    id: str
    node_id: str
    ref: str
    required: bool = False
    availability: Literal["available", "unavailable"] = "available"
    source_refs: tuple[str, ...]

    _ids = field_validator("id", "node_id")(_require_id)

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        value = _require_reference(value)
        if urlsplit(value).scheme not in BINDING_SCHEMES:
            raise ValueError(
                "binding scheme must be one of " + ", ".join(sorted(BINDING_SCHEMES))
            )
        return value

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({_require_reference(item) for item in value}))
        if not cleaned:
            raise ValueError("must contain at least one source reference")
        return cleaned


class IntentRuleMatch(_StrictModel):
    scope: str | None = None
    all_terms: tuple[str, ...] = ()
    any_terms: tuple[str, ...] = ()

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str | None) -> str | None:
        return normalize_text(value) if value is not None else None

    @field_validator("all_terms", "any_terms")
    @classmethod
    def validate_terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({normalize_text(item) for item in value}))
        if any(not item for item in cleaned):
            raise ValueError("rule terms must not be empty")
        return cleaned


class IntentRuleSource(_StrictModel):
    id: str
    priority: int = Field(ge=0, le=1000)
    goal_id: str
    match: IntentRuleMatch
    source_refs: tuple[str, ...]

    _ids = field_validator("id", "goal_id")(_require_id)

    @field_validator("source_refs")
    @classmethod
    def validate_source_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(sorted({_require_reference(item) for item in value}))
        if not cleaned:
            raise ValueError("must contain at least one source reference")
        return cleaned


class IntentGraphDocument(_StrictModel):
    schema_version: Literal["1"]
    graph: GraphMetadata
    nodes: tuple[IntentNodeSource, ...]
    edges: tuple[IntentEdgeSource, ...] = ()
    bindings: tuple[IntentBindingSource, ...] = ()
    rules: tuple[IntentRuleSource, ...] = ()


class VersionHash(_StrictModel):
    graph_id: str
    version: str
    source_hash: str


class CompileResult(_StrictModel):
    destination: Path
    graph_id: str
    current_version: str
    corpus_hash: str
    version_hashes: tuple[VersionHash, ...]
    version_count: int
    node_count: int
    edge_count: int
    binding_count: int
    rule_count: int
    replaced: bool


class TraversalLimits(_StrictModel):
    max_depth: int = Field(default=2, ge=0, le=32)
    max_nodes: int = Field(default=64, ge=1, le=1000)


class IntentTraceEdge(_StrictModel):
    source_id: str
    target_id: str
    relation: str


class IntentResolution(_StrictModel):
    schema_version: Literal["1"] = "1"
    graph_id: str
    graph_version: str
    source_hash: str
    outcome: Literal["resolved", "fallback", "refusal"]
    resolution_method: Literal["explicit", "alias", "rule", "none"]
    matched_goal_ids: tuple[str, ...] = ()
    prerequisite_goal_ids: tuple[str, ...] = ()
    intent_path: tuple[str, ...] = ()
    edge_path: tuple[IntentTraceEdge, ...] = ()
    capability_hints: tuple[str, ...] = ()
    rejected_capability_hints: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    truncation_reason: Literal["max_depth", "max_nodes"] | None = None
    refusal_reason: str | None = None

    def as_contract_result(
        self, result_id: str, behaviors: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Return the additive candidate shape used by the Phase 1 evaluator."""
        behavior_set = set(behaviors)
        if self.refusal_reason == "intent_no_match":
            behavior_set.update({"require explicit scope", "do not query all stores"})
        if self.truncation_reason:
            behavior_set.update({"return truncation warning", "preserve visited path"})
        if self.rejected_capability_hints:
            behavior_set.add("policy rejected untrusted capability hint")

        payload: dict[str, Any] = {
            "id": result_id,
            "outcome": self.outcome,
            "resolution_method": self.resolution_method,
            "matched_goal_ids": list(self.matched_goal_ids),
            "prerequisite_goal_ids": list(self.prerequisite_goal_ids),
            "intent_path": list(self.intent_path),
            "capability_hints": list(self.capability_hints),
            "behaviors": sorted(behavior_set),
            "fields": sorted(type(self).model_fields),
            "graph_id": self.graph_id,
            "graph_version": self.graph_version,
        }
        reason = self.refusal_reason
        if self.truncation_reason:
            reason = "intent_traversal_truncated"
        if reason:
            payload["reason"] = reason
        return payload


@dataclass(frozen=True)
class _ValidatedVersion:
    path: Path
    document: IntentGraphDocument
    canonical_bytes: bytes
    source_hash: str
    effective_status: str = "approved"


@dataclass(frozen=True)
class _ValidatedCorpus:
    graph_id: str
    current_version: str
    corpus_hash: str
    versions: tuple[_ValidatedVersion, ...]


def normalize_text(value: str) -> str:
    """Normalize exact-match text without discarding its word content."""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.strip().split()).casefold()


def _tokens(value: str) -> frozenset[str]:
    return frozenset(TOKEN_RE.findall(normalize_text(value)))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_payload(document: IntentGraphDocument) -> dict[str, Any]:
    payload = document.model_dump(mode="json")
    payload["graph"]["reviewed_by"] = sorted(payload["graph"]["reviewed_by"])
    payload["graph"]["source_refs"] = sorted(payload["graph"]["source_refs"])
    for node in payload["nodes"]:
        node["aliases"] = sorted(node["aliases"], key=normalize_text)
        node["source_refs"] = sorted(node["source_refs"])
    for edge in payload["edges"]:
        edge["source_refs"] = sorted(edge["source_refs"])
    for binding in payload["bindings"]:
        binding["source_refs"] = sorted(binding["source_refs"])
    for rule in payload["rules"]:
        rule["match"]["all_terms"] = sorted(rule["match"]["all_terms"])
        rule["match"]["any_terms"] = sorted(rule["match"]["any_terms"])
        rule["source_refs"] = sorted(rule["source_refs"])
    payload["nodes"] = sorted(payload["nodes"], key=lambda item: item["id"])
    payload["edges"] = sorted(
        payload["edges"],
        key=lambda item: (item["source_id"], item["target_id"], item["relation"]),
    )
    payload["bindings"] = sorted(payload["bindings"], key=lambda item: item["id"])
    payload["rules"] = sorted(payload["rules"], key=lambda item: item["id"])
    return payload


def _canonical_bytes(document: IntentGraphDocument) -> bytes:
    return (_json(_canonical_payload(document)) + "\n").encode("utf-8")


def _validation_error(
    code: str,
    message: str,
    *,
    document: IntentGraphDocument | None = None,
    location: str | None = None,
    witness: tuple[str, ...] = (),
) -> IntentGraphValidationError:
    return IntentGraphValidationError(
        code,
        message,
        location=location,
        witness=witness,
        graph_id=document.graph.id if document else None,
        graph_version=document.graph.version if document else None,
    )


def _load_document(path: Path) -> IntentGraphDocument:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise IntentGraphValidationError(
            "intent_schema_invalid", f"cannot read YAML source: {exc}", location=path.name
        ) from exc
    if not isinstance(raw, dict):
        raise IntentGraphValidationError(
            "intent_schema_invalid", "source must be a YAML mapping", location=path.name
        )
    try:
        return IntentGraphDocument.model_validate(raw)
    except ValidationError as exc:
        errors = sorted(
            exc.errors(),
            key=lambda item: (tuple(str(part) for part in item["loc"]), item["type"]),
        )
        first = errors[0]
        field = ".".join(str(part) for part in first["loc"])
        raise IntentGraphValidationError(
            "intent_schema_invalid",
            first["msg"],
            location=f"{path.name}:{field}",
        ) from exc


def _duplicate(values: list[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _cycle_witness(adjacency: dict[str, tuple[str, ...]]) -> tuple[str, ...] | None:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(node: str) -> tuple[str, ...] | None:
        visited.add(node)
        active.append(node)
        active_set.add(node)
        for target in adjacency.get(node, ()):
            if target in active_set:
                start = active.index(target)
                return tuple(active[start:] + [target])
            if target not in visited:
                witness = visit(target)
                if witness:
                    return witness
        active.pop()
        active_set.remove(node)
        return None

    for node in sorted(adjacency):
        if node not in visited:
            witness = visit(node)
            if witness:
                return witness
    return None


def _validate_document(document: IntentGraphDocument, path: Path) -> None:
    node_ids = [node.id for node in sorted(document.nodes, key=lambda item: item.id)]
    duplicate = _duplicate(node_ids)
    if duplicate:
        raise _validation_error(
            "intent_duplicate_id",
            f"duplicate node ID {duplicate}",
            document=document,
            location=path.name,
            witness=(duplicate,),
        )
    if not node_ids:
        raise _validation_error(
            "intent_schema_invalid",
            "graph version must contain at least one node",
            document=document,
            location=path.name,
        )

    node_map = {node.id: node for node in document.nodes}
    for node in sorted(document.nodes, key=lambda item: item.id):
        if not node.id.startswith(f"{node.kind}."):
            raise _validation_error(
                "intent_kind_mismatch",
                f"node {node.id} must use the {node.kind}. prefix",
                document=document,
                location=f"{path.name}:nodes.{node.id}",
                witness=(node.id,),
            )
        if node.kind != "goal" and node.aliases:
            raise _validation_error(
                "intent_kind_mismatch",
                f"only goals may declare aliases: {node.id}",
                document=document,
                location=f"{path.name}:nodes.{node.id}.aliases",
                witness=(node.id,),
            )

    alias_owners: dict[str, str] = {}
    for node in sorted(document.nodes, key=lambda item: item.id):
        if node.kind != "goal" or node.status != "active":
            continue
        for alias in (node.label, *node.aliases):
            normalized = normalize_text(alias)
            owner = alias_owners.get(normalized)
            if owner and owner != node.id:
                raise _validation_error(
                    "intent_alias_ambiguous",
                    f"normalized label/alias {normalized!r} identifies multiple goals",
                    document=document,
                    location=f"{path.name}:nodes",
                    witness=(owner, node.id),
                )
            if owner == node.id:
                raise _validation_error(
                    "intent_alias_ambiguous",
                    f"goal {node.id} repeats normalized label/alias {normalized!r}",
                    document=document,
                    location=f"{path.name}:nodes.{node.id}.aliases",
                    witness=(node.id,),
                )
            alias_owners[normalized] = node.id

    edge_keys = [
        (edge.source_id, edge.target_id, edge.relation)
        for edge in sorted(
            document.edges,
            key=lambda item: (item.source_id, item.target_id, item.relation),
        )
    ]
    duplicate_edge = _duplicate(["\x1f".join(key) for key in edge_keys])
    if duplicate_edge:
        raise _validation_error(
            "intent_duplicate_id",
            "duplicate edge",
            document=document,
            location=f"{path.name}:edges",
            witness=tuple(duplicate_edge.split("\x1f")),
        )

    expected_kinds = {
        "decomposes_to": ("goal", "goal"),
        "requires": ("goal", "goal"),
        "next_step": ("goal", "goal"),
        "blocked_by": ("goal", "constraint"),
        "routes_to": ("goal", "capability"),
    }
    for edge in sorted(
        document.edges,
        key=lambda item: (item.source_id, item.target_id, item.relation),
    ):
        if edge.relation not in RELATIONS:
            raise _validation_error(
                "intent_relation_invalid",
                f"unknown relation {edge.relation}",
                document=document,
                location=f"{path.name}:edges",
                witness=(edge.source_id, edge.target_id),
            )
        missing = [item for item in (edge.source_id, edge.target_id) if item not in node_map]
        if missing:
            raise _validation_error(
                "intent_missing_target",
                f"edge references missing node {missing[0]}",
                document=document,
                location=f"{path.name}:edges",
                witness=(edge.source_id, edge.target_id),
            )
        actual = (node_map[edge.source_id].kind, node_map[edge.target_id].kind)
        if actual != expected_kinds[edge.relation]:
            raise _validation_error(
                "intent_kind_mismatch",
                f"{edge.relation} requires {expected_kinds[edge.relation]}, got {actual}",
                document=document,
                location=f"{path.name}:edges",
                witness=(edge.source_id, edge.target_id),
            )

    for relation in sorted(ACYCLIC_RELATIONS):
        adjacency: dict[str, list[str]] = {}
        for edge in document.edges:
            if edge.status == "active" and edge.relation == relation:
                adjacency.setdefault(edge.source_id, []).append(edge.target_id)
        ordered = {key: tuple(sorted(value)) for key, value in adjacency.items()}
        witness = _cycle_witness(ordered)
        if witness:
            raise _validation_error(
                "intent_cycle_detected",
                f"{relation} contains a cycle",
                document=document,
                location=f"{path.name}:edges",
                witness=witness,
            )

    binding_ids = [item.id for item in sorted(document.bindings, key=lambda item: item.id)]
    duplicate = _duplicate(binding_ids)
    if duplicate:
        raise _validation_error(
            "intent_duplicate_id",
            f"duplicate binding ID {duplicate}",
            document=document,
            location=f"{path.name}:bindings",
            witness=(duplicate,),
        )
    for binding in sorted(document.bindings, key=lambda item: item.id):
        if binding.node_id not in node_map:
            raise _validation_error(
                "intent_missing_target",
                f"binding references missing node {binding.node_id}",
                document=document,
                location=f"{path.name}:bindings.{binding.id}",
                witness=(binding.node_id,),
            )
        if binding.required and binding.availability == "unavailable":
            raise _validation_error(
                "intent_binding_unavailable",
                f"required binding {binding.ref} is unavailable",
                document=document,
                location=f"{path.name}:bindings.{binding.id}",
                witness=(binding.node_id, binding.ref),
            )

    rule_ids = [item.id for item in sorted(document.rules, key=lambda item: item.id)]
    duplicate = _duplicate(rule_ids)
    if duplicate:
        raise _validation_error(
            "intent_duplicate_id",
            f"duplicate rule ID {duplicate}",
            document=document,
            location=f"{path.name}:rules",
            witness=(duplicate,),
        )
    for rule in sorted(document.rules, key=lambda item: item.id):
        goal = node_map.get(rule.goal_id)
        if goal is None:
            raise _validation_error(
                "intent_missing_target",
                f"rule references missing goal {rule.goal_id}",
                document=document,
                location=f"{path.name}:rules.{rule.id}",
                witness=(rule.goal_id,),
            )
        if goal.kind != "goal":
            raise _validation_error(
                "intent_kind_mismatch",
                f"rule target {rule.goal_id} is not a goal",
                document=document,
                location=f"{path.name}:rules.{rule.id}",
                witness=(rule.goal_id,),
            )
        match = rule.match
        if not match.scope and not match.all_terms and not match.any_terms:
            raise _validation_error(
                "intent_schema_invalid",
                f"rule {rule.id} has no match condition",
                document=document,
                location=f"{path.name}:rules.{rule.id}.match",
                witness=(rule.id,),
            )


def validate_intent_corpus(source_dir: Path) -> _ValidatedCorpus:
    """Load and validate one append-only graph-family corpus."""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise IntentGraphValidationError(
            "intent_schema_invalid",
            "source directory does not exist",
            location=str(source_dir),
        )
    paths = sorted(path for path in source_dir.glob("*.yaml") if path.is_file())
    if not paths:
        raise IntentGraphValidationError(
            "intent_schema_invalid",
            "source corpus contains no direct *.yaml files",
            location=str(source_dir),
        )

    versions: list[_ValidatedVersion] = []
    for path in paths:
        document = _load_document(path)
        _validate_document(document, path)
        canonical = _canonical_bytes(document)
        versions.append(
            _ValidatedVersion(
                path=path,
                document=document,
                canonical_bytes=canonical,
                source_hash=hashlib.sha256(canonical).hexdigest(),
            )
        )

    graph_ids = sorted({version.document.graph.id for version in versions})
    if len(graph_ids) != 1:
        raise IntentGraphValidationError(
            "intent_version_fork",
            "a source corpus must contain exactly one graph family",
            witness=tuple(graph_ids),
        )
    graph_id = graph_ids[0]

    by_version: dict[str, _ValidatedVersion] = {}
    for item in sorted(versions, key=lambda version: version.document.graph.version):
        version = item.document.graph.version
        if version in by_version:
            raise _validation_error(
                "intent_duplicate_id",
                f"duplicate graph version {version}",
                document=item.document,
                location=item.path.name,
                witness=(version,),
            )
        by_version[version] = item

    successors: dict[str, list[str]] = {version: [] for version in by_version}
    roots: list[str] = []
    for version, item in sorted(by_version.items()):
        predecessor = item.document.graph.supersedes
        if predecessor is None:
            roots.append(version)
            continue
        if predecessor not in by_version:
            raise _validation_error(
                "intent_version_missing",
                f"version {version} supersedes missing version {predecessor}",
                document=item.document,
                location=item.path.name,
                witness=(predecessor, version),
            )
        successors[predecessor].append(version)

    forks = sorted(version for version, items in successors.items() if len(items) > 1)
    if len(roots) != 1 or forks:
        witness = tuple(sorted(roots + forks))
        raise IntentGraphValidationError(
            "intent_version_fork",
            "version lineage must be one unbranched chain",
            graph_id=graph_id,
            witness=witness,
        )

    ordered_names: list[str] = []
    current = roots[0]
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        ordered_names.append(current)
        next_versions = successors[current]
        if not next_versions:
            break
        current = next_versions[0]
    if len(seen) != len(by_version):
        raise IntentGraphValidationError(
            "intent_version_fork",
            "version lineage contains a disconnected chain or cycle",
            graph_id=graph_id,
            witness=tuple(sorted(set(by_version) - seen)),
        )

    ordered: list[_ValidatedVersion] = []
    for name in ordered_names:
        item = by_version[name]
        ordered.append(
            _ValidatedVersion(
                path=item.path,
                document=item.document,
                canonical_bytes=item.canonical_bytes,
                source_hash=item.source_hash,
                effective_status="approved" if name == ordered_names[-1] else "superseded",
            )
        )
    corpus_payload = [
        {
            "graph_id": item.document.graph.id,
            "version": item.document.graph.version,
            "source_hash": item.source_hash,
        }
        for item in ordered
    ]
    corpus_hash = hashlib.sha256((_json(corpus_payload) + "\n").encode("utf-8")).hexdigest()
    return _ValidatedCorpus(
        graph_id=graph_id,
        current_version=ordered_names[-1],
        corpus_hash=corpus_hash,
        versions=tuple(ordered),
    )


def _existing_version_hashes(destination: Path) -> tuple[str, dict[str, str]]:
    try:
        conn = _connect_read_only(destination)
        try:
            _validate_store(conn)
            graph_row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'graph_id'"
            ).fetchone()
            rows = conn.execute(
                "SELECT version, source_hash FROM graph_versions ORDER BY version"
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, IntentStoreError) as exc:
        raise IntentStoreError(
            "intent_store_invalid",
            f"cannot inspect existing destination: {exc}",
            location=str(destination),
        ) from exc
    if graph_row is None:
        raise IntentStoreError(
            "intent_store_invalid",
            "existing destination has no graph_id",
            location=str(destination),
        )
    return graph_row["value"], {row["version"]: row["source_hash"] for row in rows}


def _check_existing_history(destination: Path, corpus: _ValidatedCorpus) -> None:
    if not destination.exists():
        return
    graph_id, existing = _existing_version_hashes(destination)
    if graph_id != corpus.graph_id:
        raise IntentVersionConflict(
            "intent_version_conflict",
            f"existing graph {graph_id} cannot be replaced by {corpus.graph_id}",
            location=str(destination),
            graph_id=corpus.graph_id,
        )
    proposed = {
        item.document.graph.version: item.source_hash for item in corpus.versions
    }
    for version, source_hash in sorted(existing.items()):
        if version not in proposed:
            raise IntentVersionConflict(
                "intent_version_missing",
                f"approved version {version} was removed from the source corpus",
                location=str(destination),
                witness=(version,),
                graph_id=graph_id,
                graph_version=version,
            )
        if proposed[version] != source_hash:
            raise IntentVersionConflict(
                "intent_version_conflict",
                f"approved version {version} changed source hash",
                location=str(destination),
                witness=(version,),
                graph_id=graph_id,
                graph_version=version,
            )


SCHEMA_SQL = """
CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE graph_versions (
    graph_id TEXT NOT NULL,
    version TEXT NOT NULL,
    effective_status TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    reviewed_by_json TEXT NOT NULL,
    supersedes_version TEXT,
    PRIMARY KEY (graph_id, version)
) WITHOUT ROWID;
CREATE TABLE intent_nodes (
    graph_id TEXT NOT NULL,
    version TEXT NOT NULL,
    node_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    normalized_label TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    status TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    PRIMARY KEY (graph_id, version, node_id),
    FOREIGN KEY (graph_id, version) REFERENCES graph_versions(graph_id, version)
) WITHOUT ROWID;
CREATE TABLE intent_aliases (
    graph_id TEXT NOT NULL,
    version TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    node_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    PRIMARY KEY (graph_id, version, normalized_alias),
    FOREIGN KEY (graph_id, version, node_id)
        REFERENCES intent_nodes(graph_id, version, node_id)
) WITHOUT ROWID;
CREATE TABLE intent_edges (
    graph_id TEXT NOT NULL,
    version TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    status TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    PRIMARY KEY (graph_id, version, source_id, target_id, relation),
    FOREIGN KEY (graph_id, version, source_id)
        REFERENCES intent_nodes(graph_id, version, node_id),
    FOREIGN KEY (graph_id, version, target_id)
        REFERENCES intent_nodes(graph_id, version, node_id)
) WITHOUT ROWID;
CREATE TABLE intent_bindings (
    graph_id TEXT NOT NULL,
    version TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    ref TEXT NOT NULL,
    required INTEGER NOT NULL,
    availability TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    PRIMARY KEY (graph_id, version, binding_id),
    FOREIGN KEY (graph_id, version, node_id)
        REFERENCES intent_nodes(graph_id, version, node_id)
) WITHOUT ROWID;
CREATE TABLE intent_rules (
    graph_id TEXT NOT NULL,
    version TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    priority INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    scope TEXT,
    all_terms_json TEXT NOT NULL,
    any_terms_json TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    PRIMARY KEY (graph_id, version, rule_id),
    FOREIGN KEY (graph_id, version, goal_id)
        REFERENCES intent_nodes(graph_id, version, node_id)
) WITHOUT ROWID;
"""


def _build_database(path: Path, corpus: _ValidatedCorpus) -> None:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA page_size = 4096")
        conn.execute("PRAGMA encoding = 'UTF-8'")
        conn.execute("PRAGMA auto_vacuum = NONE")
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_SQL)
        meta = {
            "schema_version": SCHEMA_VERSION,
            "graph_id": corpus.graph_id,
            "current_version": corpus.current_version,
            "corpus_hash": corpus.corpus_hash,
        }
        conn.executemany(
            "INSERT INTO schema_meta(key, value) VALUES (?, ?)", sorted(meta.items())
        )
        for item in corpus.versions:
            graph = item.document.graph
            conn.execute(
                """
                INSERT INTO graph_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    graph.id,
                    graph.version,
                    item.effective_status,
                    SCHEMA_VERSION,
                    item.source_hash,
                    item.path.name,
                    graph.created_at,
                    graph.reviewed_at,
                    _json(list(graph.reviewed_by)),
                    graph.supersedes,
                ),
            )
            for node in sorted(item.document.nodes, key=lambda value: value.id):
                aliases = sorted(node.aliases, key=normalize_text)
                conn.execute(
                    "INSERT INTO intent_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        graph.id,
                        graph.version,
                        node.id,
                        node.kind,
                        node.label,
                        normalize_text(node.label),
                        _json(aliases),
                        node.status,
                        _json(list(node.source_refs)),
                    ),
                )
                if node.kind == "goal" and node.status == "active":
                    for alias in (node.label, *aliases):
                        conn.execute(
                            "INSERT INTO intent_aliases VALUES (?, ?, ?, ?, ?)",
                            (graph.id, graph.version, normalize_text(alias), node.id, alias),
                        )
            for edge in sorted(
                item.document.edges,
                key=lambda value: (value.source_id, value.target_id, value.relation),
            ):
                conn.execute(
                    "INSERT INTO intent_edges VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        graph.id,
                        graph.version,
                        edge.source_id,
                        edge.target_id,
                        edge.relation,
                        edge.status,
                        _json(list(edge.source_refs)),
                    ),
                )
            for binding in sorted(item.document.bindings, key=lambda value: value.id):
                conn.execute(
                    "INSERT INTO intent_bindings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        graph.id,
                        graph.version,
                        binding.id,
                        binding.node_id,
                        binding.ref,
                        int(binding.required),
                        binding.availability,
                        _json(list(binding.source_refs)),
                    ),
                )
            for rule in sorted(item.document.rules, key=lambda value: value.id):
                conn.execute(
                    "INSERT INTO intent_rules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        graph.id,
                        graph.version,
                        rule.id,
                        rule.priority,
                        rule.goal_id,
                        rule.match.scope,
                        _json(list(rule.match.all_terms)),
                        _json(list(rule.match.any_terms)),
                        _json(list(rule.source_refs)),
                    ),
                )
        conn.commit()
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise IntentStoreError(
                "intent_store_invalid", "compiled store failed foreign_key_check"
            )
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise IntentStoreError(
                "intent_store_invalid", f"compiled store failed integrity_check: {integrity}"
            )
        conn.execute("VACUUM")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise IntentStoreError(
                "intent_store_invalid", f"vacuumed store failed integrity_check: {integrity}"
            )
    except Exception:
        conn.close()
        raise
    else:
        conn.close()


def _fsync(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def compile_intent_corpus(source_dir: Path, destination: Path) -> CompileResult:
    """Validate and atomically compile a reviewed intent corpus."""
    source_dir = Path(source_dir)
    destination = Path(destination)
    corpus = validate_intent_corpus(source_dir)
    _check_existing_history(destination, corpus)
    destination.parent.mkdir(parents=True, exist_ok=True)
    replaced = destination.exists()
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temp_path = Path(temp_name)
    temp_path.unlink()
    try:
        _build_database(temp_path, corpus)
        os.chmod(temp_path, 0o600)
        check = open_intent_store(temp_path)
        check.close()
        _fsync(temp_path)
        os.replace(temp_path, destination)
        _fsync(destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return CompileResult(
        destination=destination,
        graph_id=corpus.graph_id,
        current_version=corpus.current_version,
        corpus_hash=corpus.corpus_hash,
        version_hashes=tuple(
            VersionHash(
                graph_id=item.document.graph.id,
                version=item.document.graph.version,
                source_hash=item.source_hash,
            )
            for item in corpus.versions
        ),
        version_count=len(corpus.versions),
        node_count=sum(len(item.document.nodes) for item in corpus.versions),
        edge_count=sum(len(item.document.edges) for item in corpus.versions),
        binding_count=sum(len(item.document.bindings) for item in corpus.versions),
        rule_count=sum(len(item.document.rules) for item in corpus.versions),
        replaced=replaced,
    )


def _connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise IntentStoreError(
            "intent_store_invalid", "intent store does not exist", location=str(resolved)
        )
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA query_only = ON")
    return conn


def _validate_store(conn: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise IntentStoreError(
            "intent_store_invalid", f"intent store is missing tables: {', '.join(missing)}"
        )
    meta = {
        row["key"]: row["value"]
        for row in conn.execute("SELECT key, value FROM schema_meta").fetchall()
    }
    required_meta = {"schema_version", "graph_id", "current_version", "corpus_hash"}
    if required_meta - set(meta) or meta.get("schema_version") != SCHEMA_VERSION:
        raise IntentStoreError(
            "intent_store_invalid", "intent store metadata is missing or incompatible"
        )
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise IntentStoreError(
            "intent_store_invalid", f"intent store failed integrity_check: {integrity}"
        )
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise IntentStoreError(
            "intent_store_invalid", "intent store failed foreign_key_check"
        )
    heads = conn.execute(
        "SELECT version FROM graph_versions WHERE effective_status = 'approved'"
    ).fetchall()
    if len(heads) != 1 or heads[0]["version"] != meta["current_version"]:
        raise IntentStoreError(
            "intent_store_invalid", "intent store must contain exactly one current version"
        )


def open_intent_store(path: Path) -> sqlite3.Connection:
    """Open and validate a compiled intent store without creating or writing it."""
    conn = _connect_read_only(Path(path))
    try:
        _validate_store(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _metadata(conn: sqlite3.Connection) -> tuple[str, str, str]:
    meta = {
        row["key"]: row["value"]
        for row in conn.execute("SELECT key, value FROM schema_meta").fetchall()
    }
    version = meta["current_version"]
    row = conn.execute(
        "SELECT source_hash FROM graph_versions WHERE graph_id = ? AND version = ?",
        (meta["graph_id"], version),
    ).fetchone()
    if row is None:
        raise IntentStoreError(
            "intent_store_invalid", "current graph version is missing"
        )
    return meta["graph_id"], version, row["source_hash"]


def _base_resolution(
    graph_id: str,
    graph_version: str,
    source_hash: str,
    *,
    outcome: Literal["resolved", "fallback", "refusal"],
    method: Literal["explicit", "alias", "rule", "none"],
    goal_id: str | None = None,
    refusal_reason: str | None = None,
    path: tuple[str, ...] = (),
) -> IntentResolution:
    return IntentResolution(
        graph_id=graph_id,
        graph_version=graph_version,
        source_hash=source_hash,
        outcome=outcome,
        resolution_method=method,
        matched_goal_ids=(goal_id,) if goal_id else (),
        intent_path=path,
        refusal_reason=refusal_reason,
    )


def _resolve_rule(
    conn: sqlite3.Connection,
    graph_id: str,
    version: str,
    query_text: str,
    scope: str | None,
) -> tuple[str | None, bool]:
    query_tokens = _tokens(query_text)
    normalized_scope = normalize_text(scope) if scope is not None else None
    matches: list[tuple[int, str, str]] = []
    rows = conn.execute(
        """
        SELECT rule_id, priority, goal_id, scope, all_terms_json, any_terms_json
        FROM intent_rules WHERE graph_id = ? AND version = ?
        ORDER BY priority DESC, rule_id ASC
        """,
        (graph_id, version),
    ).fetchall()
    for row in rows:
        if row["scope"] is not None and row["scope"] != normalized_scope:
            continue
        all_terms = json.loads(row["all_terms_json"])
        any_terms = json.loads(row["any_terms_json"])
        if any(not _tokens(term).issubset(query_tokens) for term in all_terms):
            continue
        if any_terms and not any(_tokens(term).issubset(query_tokens) for term in any_terms):
            continue
        matches.append((row["priority"], row["rule_id"], row["goal_id"]))
    if not matches:
        return None, False
    top_priority = matches[0][0]
    top = [item for item in matches if item[0] == top_priority]
    goals = sorted({item[2] for item in top})
    if len(goals) > 1:
        return None, True
    return goals[0], False


def _runtime_cycle(
    conn: sqlite3.Connection, graph_id: str, version: str, relation: str
) -> tuple[str, ...] | None:
    rows = conn.execute(
        """
        SELECT source_id, target_id FROM intent_edges
        WHERE graph_id = ? AND version = ? AND relation = ? AND status = 'active'
        ORDER BY source_id, target_id
        """,
        (graph_id, version, relation),
    ).fetchall()
    adjacency: dict[str, list[str]] = {}
    for row in rows:
        adjacency.setdefault(row["source_id"], []).append(row["target_id"])
    return _cycle_witness(
        {source: tuple(sorted(targets)) for source, targets in adjacency.items()}
    )


def _traverse_relation(
    conn: sqlite3.Connection,
    graph_id: str,
    version: str,
    root: str,
    relation: Literal["decomposes_to", "requires", "next_step"],
    limits: TraversalLimits,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[IntentTraceEdge, ...],
    Literal["max_depth", "max_nodes"] | None,
]:
    path = [root]
    prerequisites: list[str] = []
    edge_path: list[IntentTraceEdge] = []
    visited = {root}
    truncation: Literal["max_depth", "max_nodes"] | None = None

    def visit(node: str, depth: int) -> None:
        nonlocal truncation
        rows = conn.execute(
            """
            SELECT target_id FROM intent_edges
            WHERE graph_id = ? AND version = ? AND source_id = ?
              AND relation = ? AND status = 'active'
            ORDER BY target_id
            """,
            (graph_id, version, node, relation),
        ).fetchall()
        for row in rows:
            target = row["target_id"]
            if target in visited:
                continue
            if depth >= limits.max_depth:
                truncation = truncation or "max_depth"
                continue
            if len(visited) >= limits.max_nodes:
                truncation = truncation or "max_nodes"
                continue
            visited.add(target)
            path.append(target)
            prerequisites.append(target)
            edge_path.append(
                IntentTraceEdge(source_id=node, target_id=target, relation=relation)
            )
            visit(target, depth + 1)

    visit(root, 0)
    return tuple(path), tuple(prerequisites), tuple(edge_path), truncation


def _collect_constraints(
    conn: sqlite3.Connection, graph_id: str, version: str, goals: tuple[str, ...]
) -> tuple[str, ...]:
    constraints: set[str] = set()
    for goal in goals:
        rows = conn.execute(
            """
            SELECT target_id FROM intent_edges
            WHERE graph_id = ? AND version = ? AND source_id = ?
              AND relation = 'blocked_by' AND status = 'active'
            ORDER BY target_id
            """,
            (graph_id, version, goal),
        ).fetchall()
        constraints.update(row["target_id"] for row in rows)
    return tuple(sorted(constraints))


def _collect_capabilities(
    conn: sqlite3.Connection,
    graph_id: str,
    version: str,
    goals: tuple[str, ...],
    allowed: frozenset[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    hints: set[str] = set()
    rejected: set[str] = set()
    warnings: set[str] = set()
    capability_nodes: set[str] = set()
    for goal in goals:
        rows = conn.execute(
            """
            SELECT target_id FROM intent_edges
            WHERE graph_id = ? AND version = ? AND source_id = ?
              AND relation = 'routes_to' AND status = 'active'
            ORDER BY target_id
            """,
            (graph_id, version, goal),
        ).fetchall()
        capability_nodes.update(row["target_id"] for row in rows)
    for node in sorted(capability_nodes):
        rows = conn.execute(
            """
            SELECT ref, availability FROM intent_bindings
            WHERE graph_id = ? AND version = ? AND node_id = ?
            ORDER BY ref
            """,
            (graph_id, version, node),
        ).fetchall()
        for row in rows:
            ref = row["ref"]
            if row["availability"] != "available":
                rejected.add(ref)
                warnings.add(f"binding_unavailable:{ref}")
            elif ref not in allowed:
                rejected.add(ref)
                warnings.add(f"capability_not_allowed:{ref}")
            else:
                hints.add(ref)
    return tuple(sorted(hints)), tuple(sorted(rejected)), tuple(sorted(warnings))


def resolve_intent(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    intent_id: str | None = None,
    scope: str | None = None,
    allowed_capability_refs: frozenset[str] = frozenset(),
    limits: TraversalLimits = TraversalLimits(),
) -> IntentResolution:
    """Resolve one reviewed root goal and return bounded prerequisite context."""
    graph_id, version, source_hash = _metadata(conn)
    method: Literal["explicit", "alias", "rule", "none"] = "none"
    goal_id: str | None = None

    if intent_id is not None:
        method = "explicit"
        row = conn.execute(
            """
            SELECT node_id FROM intent_nodes
            WHERE graph_id = ? AND version = ? AND node_id = ?
              AND kind = 'goal' AND status = 'active'
            """,
            (graph_id, version, intent_id),
        ).fetchone()
        goal_id = row["node_id"] if row else None
    else:
        normalized = normalize_text(query_text)
        rows = conn.execute(
            """
            SELECT node_id FROM intent_aliases
            WHERE graph_id = ? AND version = ? AND normalized_alias = ?
            ORDER BY node_id
            """,
            (graph_id, version, normalized),
        ).fetchall()
        alias_goals = sorted({row["node_id"] for row in rows})
        if len(alias_goals) > 1:
            return _base_resolution(
                graph_id,
                version,
                source_hash,
                outcome="refusal",
                method="none",
                refusal_reason="intent_alias_ambiguous",
            )
        if alias_goals:
            method = "alias"
            goal_id = alias_goals[0]
        else:
            goal_id, ambiguous = _resolve_rule(
                conn, graph_id, version, query_text, scope
            )
            if ambiguous:
                return _base_resolution(
                    graph_id,
                    version,
                    source_hash,
                    outcome="refusal",
                    method="rule",
                    refusal_reason="intent_rule_ambiguous",
                )
            if goal_id:
                method = "rule"

    if goal_id is None:
        return _base_resolution(
            graph_id,
            version,
            source_hash,
            outcome="fallback",
            method="none" if intent_id is None else "explicit",
            refusal_reason="intent_no_match",
        )

    for relation in sorted(ACYCLIC_RELATIONS):
        cycle = _runtime_cycle(conn, graph_id, version, relation)
        if cycle:
            return _base_resolution(
                graph_id,
                version,
                source_hash,
                outcome="refusal",
                method=method,
                goal_id=goal_id,
                refusal_reason="intent_cycle_detected",
                path=cycle,
            )

    path, prerequisites, edge_path, truncation = _traverse_relation(
        conn, graph_id, version, goal_id, "requires", limits
    )
    constraints = _collect_constraints(conn, graph_id, version, path)
    hints, rejected, capability_warnings = _collect_capabilities(
        conn, graph_id, version, path, allowed_capability_refs
    )
    warnings = set(capability_warnings)
    warnings.update(f"blocked_by:{item}" for item in constraints)
    if truncation:
        warnings.add("intent_traversal_truncated")
    return IntentResolution(
        graph_id=graph_id,
        graph_version=version,
        source_hash=source_hash,
        outcome="resolved",
        resolution_method=method,
        matched_goal_ids=(goal_id,),
        prerequisite_goal_ids=prerequisites,
        intent_path=path,
        edge_path=edge_path,
        capability_hints=hints,
        rejected_capability_hints=rejected,
        constraint_ids=constraints,
        warnings=tuple(sorted(warnings)),
        truncation_reason=truncation,
    )
