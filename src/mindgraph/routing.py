"""Deterministic intent-aware routing over separately ranked retrievers.

This module adds an orchestration surface without changing the legacy query or
MCP result lists. MainFrame-specific paths and policy stay with the caller.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol
from urllib.parse import quote, urlsplit

import sqlite_vec
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from mindgraph.embedders import EmbedTemplate, EmbedderSpec, format_query_text
from mindgraph.intent import (
    IntentGraphError,
    IntentResolution,
    TraversalLimits,
    normalize_text,
    resolve_intent,
)
from mindgraph.models import QueryResult
from mindgraph import query as query_mod


SCHEMA_VERSION = "1"
STABLE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
TOKEN_RE = re.compile(r"\w+", re.UNICODE)

RouteMode = Literal["auto", "durable", "projects", "federated"]
DecisionOutcome = Literal["selected", "fallback", "refusal"]
EnvelopeOutcome = Literal["success", "partial", "refusal", "fallback"]


class RoutingError(ValueError):
    """Invalid routing configuration with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def __str__(self) -> str:
        return f"{self.code}: {super().__str__()}"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return value


def _stable_id(value: str) -> str:
    value = value.strip()
    if not STABLE_ID_RE.fullmatch(value):
        raise ValueError(
            "must be a lowercase dot/underscore/hyphen-separated stable ID"
        )
    return value


def _capability_ref(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"capability", "retriever"} or not parsed.netloc:
        raise ValueError("must be a capability:// or retriever:// reference")
    return value


def _unique_tuple(value: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    if len(value) != len(set(value)):
        raise ValueError(f"{label} may not contain duplicates")
    return value


class RouteLimits(_StrictModel):
    final_top_k: int = Field(default=10, ge=1, le=1000)
    total_timeout_ms: int = Field(default=30000, ge=1, le=600000)


class RouteRequest(_StrictModel):
    schema_version: Literal["1"] = "1"
    query_id: str
    query_text: str
    mode: RouteMode = "auto"
    intent_id: str | None = None
    scope: str | None = None
    allowed_capability_refs: tuple[str, ...] = ()
    limits: RouteLimits = RouteLimits()

    _query_id = field_validator("query_id")(_stable_id)
    _query_text = field_validator("query_text")(_nonempty)

    @field_validator("intent_id", "scope")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _nonempty(value) if value is not None else None

    @field_validator("allowed_capability_refs")
    @classmethod
    def validate_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(_capability_ref(item) for item in value)
        return _unique_tuple(checked, label="allowed_capability_refs")


class RefusalRule(_StrictModel):
    rule_id: str
    reason_code: str
    all_terms: tuple[str, ...] = ()
    any_terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()

    _rule_id = field_validator("rule_id")(_stable_id)
    _reason_code = field_validator("reason_code")(_stable_id)

    @field_validator("all_terms", "any_terms", "phrases")
    @classmethod
    def validate_match_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(normalize_text(_nonempty(item)) for item in value)
        return _unique_tuple(normalized, label="refusal rule match values")

    @model_validator(mode="after")
    def validate_has_match(self) -> RefusalRule:
        if not (self.all_terms or self.any_terms or self.phrases):
            raise ValueError("refusal rule must declare a match condition")
        return self

    def matches(self, query_text: str) -> bool:
        normalized = normalize_text(query_text)
        token_text = " ".join(TOKEN_RE.findall(normalized))

        def contains(value: str) -> bool:
            needle = " ".join(TOKEN_RE.findall(value))
            return bool(needle) and f" {needle} " in f" {token_text} "

        if any(not contains(term) for term in self.all_terms):
            return False
        if self.any_terms and not any(contains(term) for term in self.any_terms):
            return False
        if self.phrases and not any(contains(phrase) for phrase in self.phrases):
            return False
        return True


class RouterPolicy(_StrictModel):
    policy_version: str
    durable_retriever_id: str
    project_retriever_id: str
    allow_federated: bool = False
    safe_default_retriever_id: str | None = None
    refusal_rules: tuple[RefusalRule, ...] = ()

    _policy_version = field_validator("policy_version")(_stable_id)
    _retriever_ids = field_validator(
        "durable_retriever_id", "project_retriever_id"
    )(_stable_id)

    @field_validator("safe_default_retriever_id")
    @classmethod
    def validate_safe_default(cls, value: str | None) -> str | None:
        return _stable_id(value) if value is not None else None

    @model_validator(mode="after")
    def validate_rules(self) -> RouterPolicy:
        rule_ids = tuple(rule.rule_id for rule in self.refusal_rules)
        _unique_tuple(rule_ids, label="refusal rule IDs")
        return self


class RetrieverDescriptor(_StrictModel):
    retriever_id: str
    capability_ref: str
    trust_profile: str
    source_surface: str
    available: bool = True
    unavailable_reason: str | None = None

    _retriever_id = field_validator("retriever_id")(_stable_id)
    _capability = field_validator("capability_ref")(_capability_ref)
    _labels = field_validator("trust_profile", "source_surface")(_nonempty)

    @field_validator("unavailable_reason")
    @classmethod
    def validate_unavailable_reason(cls, value: str | None) -> str | None:
        return _stable_id(value) if value is not None else None

    @model_validator(mode="after")
    def validate_availability(self) -> RetrieverDescriptor:
        if self.available and self.unavailable_reason is not None:
            raise ValueError("available retriever may not declare unavailable_reason")
        if not self.available and self.unavailable_reason is None:
            raise ValueError("unavailable retriever must declare unavailable_reason")
        return self


class RetrieverExecutor(Protocol):
    def retrieve(self, query_text: str, *, final_top_k: int) -> list[QueryResult]: ...


@dataclass(frozen=True)
class RetrieverRegistration:
    descriptor: RetrieverDescriptor
    executor: RetrieverExecutor | None = None

    def __post_init__(self) -> None:
        if self.descriptor.available and self.executor is None:
            raise RoutingError(
                "routing_registry_invalid",
                f"available retriever {self.descriptor.retriever_id} has no executor",
            )
        if not self.descriptor.available and self.executor is not None:
            raise RoutingError(
                "routing_registry_invalid",
                f"unavailable retriever {self.descriptor.retriever_id} has an executor",
            )


@dataclass(frozen=True, init=False)
class CapabilityRegistry:
    """Immutable deterministic lookup for caller-provided retrievers."""

    _registrations: tuple[RetrieverRegistration, ...]
    _by_id: Mapping[str, RetrieverRegistration]
    _by_capability: Mapping[str, RetrieverRegistration]

    def __init__(self, registrations: tuple[RetrieverRegistration, ...]) -> None:
        ordered = tuple(
            sorted(registrations, key=lambda item: item.descriptor.retriever_id)
        )
        ids = tuple(item.descriptor.retriever_id for item in ordered)
        capabilities = tuple(item.descriptor.capability_ref for item in ordered)
        if len(ids) != len(set(ids)):
            raise RoutingError(
                "routing_registry_invalid", "duplicate retriever ID in registry"
            )
        if len(capabilities) != len(set(capabilities)):
            raise RoutingError(
                "routing_registry_invalid", "duplicate capability ref in registry"
            )
        object.__setattr__(self, "_registrations", ordered)
        object.__setattr__(
            self,
            "_by_id",
            MappingProxyType(
                {item.descriptor.retriever_id: item for item in ordered}
            ),
        )
        object.__setattr__(
            self,
            "_by_capability",
            MappingProxyType(
                {item.descriptor.capability_ref: item for item in ordered}
            ),
        )

    @property
    def registrations(self) -> tuple[RetrieverRegistration, ...]:
        return self._registrations

    @property
    def descriptors(self) -> tuple[RetrieverDescriptor, ...]:
        return tuple(item.descriptor for item in self._registrations)

    def by_id(self, retriever_id: str) -> RetrieverRegistration | None:
        return self._by_id.get(retriever_id)

    def by_capability(self, capability_ref: str) -> RetrieverRegistration | None:
        return self._by_capability.get(capability_ref)


class RouteRejection(_StrictModel):
    target_id: str
    reason_code: str

    _target = field_validator("target_id")(_nonempty)
    _reason = field_validator("reason_code")(_stable_id)


class RouteDecision(_StrictModel):
    schema_version: Literal["1"] = "1"
    policy_version: str
    outcome: DecisionOutcome
    selected_retriever_ids: tuple[str, ...] = ()
    rejections: tuple[RouteRejection, ...] = ()
    reason_codes: tuple[str, ...] = ()
    intent_resolution: IntentResolution
    effective_limits: RouteLimits

    _policy_version = field_validator("policy_version")(_stable_id)

    @field_validator("selected_retriever_ids", "reason_codes")
    @classmethod
    def validate_stable_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(_stable_id(item) for item in value)
        return _unique_tuple(checked, label="route decision values")

    @model_validator(mode="after")
    def validate_decision_state(self) -> RouteDecision:
        if not self.reason_codes:
            raise ValueError("route decision must contain a reason code")
        if self.selected_retriever_ids != tuple(sorted(self.selected_retriever_ids)):
            raise ValueError("selected retriever IDs must be sorted")
        if self.outcome == "refusal" and self.selected_retriever_ids:
            raise ValueError("refusal may not select retrievers")
        if self.outcome == "selected" and not self.selected_retriever_ids:
            raise ValueError("selected outcome requires at least one retriever")
        if self.outcome == "fallback" and len(self.selected_retriever_ids) != 1:
            raise ValueError("fallback must select exactly one safe retriever")
        rejection_keys = tuple(
            (item.target_id, item.reason_code) for item in self.rejections
        )
        if len(rejection_keys) != len(set(rejection_keys)):
            raise ValueError("route rejections may not contain duplicates")
        if rejection_keys != tuple(sorted(rejection_keys)):
            raise ValueError("route rejections must be sorted")
        return self


class RankedNomination(_StrictModel):
    local_rank: int = Field(ge=1)
    result: QueryResult


class RetrieverBatch(_StrictModel):
    retriever_id: str
    trust_profile: str
    source_surface: str
    assembly_reason: str
    rows: tuple[RankedNomination, ...] = ()

    _retriever_id = field_validator("retriever_id")(_stable_id)
    _labels = field_validator(
        "trust_profile", "source_surface", "assembly_reason"
    )(_nonempty)

    @model_validator(mode="after")
    def validate_local_ranks(self) -> RetrieverBatch:
        ranks = tuple(row.local_rank for row in self.rows)
        if ranks != tuple(range(1, len(self.rows) + 1)):
            raise ValueError("batch local ranks must be contiguous and one-based")
        return self


class RetrieverFailure(_StrictModel):
    retriever_id: str
    reason_code: Literal["retriever_failed"] = "retriever_failed"
    error_type: str
    message: str

    _retriever_id = field_validator("retriever_id")(_stable_id)
    _error = field_validator("error_type", "message")(_nonempty)


class RetrievalEnvelope(_StrictModel):
    schema_version: Literal["1"] = "1"
    query_id: str
    outcome: EnvelopeOutcome
    decision: RouteDecision
    batches: tuple[RetrieverBatch, ...] = ()
    failures: tuple[RetrieverFailure, ...] = ()
    partial: bool = False
    warnings: tuple[str, ...] = ()

    _query_id = field_validator("query_id")(_stable_id)

    @model_validator(mode="after")
    def validate_partial_state(self) -> RetrievalEnvelope:
        if self.partial != bool(self.failures):
            raise ValueError("partial must exactly reflect whether failures exist")
        if self.outcome == "partial" and not self.failures:
            raise ValueError("partial outcome requires at least one failure")
        if self.failures and self.outcome != "partial":
            raise ValueError("retriever failures require partial outcome")
        batch_ids = tuple(batch.retriever_id for batch in self.batches)
        failure_ids = tuple(failure.retriever_id for failure in self.failures)
        if batch_ids != tuple(sorted(batch_ids)):
            raise ValueError("retriever batches must be sorted")
        if failure_ids != tuple(sorted(failure_ids)):
            raise ValueError("retriever failures must be sorted")
        if set(batch_ids) & set(failure_ids):
            raise ValueError("one retriever may not both succeed and fail")
        selected = set(self.decision.selected_retriever_ids)
        if set(batch_ids) | set(failure_ids) != selected:
            raise ValueError("batches and failures must account for selected retrievers")
        if self.decision.outcome == "refusal" and self.outcome != "refusal":
            raise ValueError("refusal decision requires refusal envelope")
        if self.outcome == "success" and self.decision.outcome != "selected":
            raise ValueError("success envelope requires selected decision")
        if self.outcome == "fallback" and self.decision.outcome != "fallback":
            raise ValueError("fallback envelope requires fallback decision")
        return self

    def as_contract_result(self, result_id: str) -> dict[str, Any]:
        """Return the additive candidate shape used by the Phase 1 evaluator."""
        selected = list(self.decision.selected_retriever_ids)
        trust_profiles = sorted({batch.trust_profile for batch in self.batches})
        source_paths: set[str] = set()
        for batch in self.batches:
            for row in batch.rows:
                path = row.result.display_path or row.result.path or row.result.source_path
                if path:
                    source_paths.add(path)

        behaviors: set[str] = set()
        if len(self.batches) > 1:
            behaviors.update(
                {
                    "group results by retriever and trust profile",
                    "preserve per-retriever ranks",
                    "explain why each retriever was called",
                }
            )
        outcome = self.outcome
        if outcome in {"success", "partial"}:
            contract_outcome = "success"
        else:
            contract_outcome = outcome
        if (
            contract_outcome == "refusal"
            and self.decision.reason_codes
            and self.decision.reason_codes[0]
            == "raw_transcripts_not_in_default_retrieval"
        ):
            contract_outcome = "refusal_or_explicit_consent_gate"
        fields = (
            ["retriever_id", "trust_profile", "source_surface", "assembly_reason"]
            if contract_outcome
            not in {"refusal", "refusal_or_explicit_consent_gate", "fallback"}
            else []
        )
        payload: dict[str, Any] = {
            "id": result_id,
            "outcome": contract_outcome,
            "selected_retrievers": selected,
            "trust_profiles": trust_profiles,
            "source_paths": sorted(source_paths),
            "behaviors": sorted(behaviors),
            "fields": fields,
        }
        if self.decision.reason_codes:
            payload["reason"] = self.decision.reason_codes[0]
        return payload


def _intent_unavailable_resolution(reason: str = "intent_unavailable") -> IntentResolution:
    return IntentResolution(
        graph_id="unavailable",
        graph_version="unavailable",
        source_hash="unavailable",
        outcome="fallback",
        resolution_method="none",
        refusal_reason=reason,
        warnings=(reason,),
    )


def _validate_policy_registry(
    registry: CapabilityRegistry, policy: RouterPolicy
) -> None:
    required = {policy.durable_retriever_id, policy.project_retriever_id}
    if policy.safe_default_retriever_id is not None:
        required.add(policy.safe_default_retriever_id)
    missing = sorted(item for item in required if registry.by_id(item) is None)
    if missing:
        raise RoutingError(
            "routing_policy_invalid",
            "policy references missing retrievers: " + ", ".join(missing),
        )


def _make_decision(
    *,
    policy: RouterPolicy,
    request: RouteRequest,
    resolution: IntentResolution,
    outcome: DecisionOutcome,
    selected: tuple[str, ...] = (),
    rejections: tuple[RouteRejection, ...] = (),
    reasons: tuple[str, ...] = (),
) -> RouteDecision:
    return RouteDecision(
        policy_version=policy.policy_version,
        outcome=outcome,
        selected_retriever_ids=tuple(sorted(selected)),
        rejections=tuple(sorted(rejections, key=lambda item: (item.target_id, item.reason_code))),
        reason_codes=_unique_tuple(reasons, label="route reason codes"),
        intent_resolution=resolution,
        effective_limits=request.limits,
    )


def _explicit_decision(
    request: RouteRequest,
    resolution: IntentResolution,
    registry: CapabilityRegistry,
    policy: RouterPolicy,
) -> RouteDecision:
    if request.mode == "federated" and not policy.allow_federated:
        return _make_decision(
            policy=policy,
            request=request,
            resolution=resolution,
            outcome="refusal",
            reasons=("federation_not_allowed",),
        )
    mode_ids = {
        "durable": (policy.durable_retriever_id,),
        "projects": (policy.project_retriever_id,),
        "federated": tuple(
            sorted({policy.durable_retriever_id, policy.project_retriever_id})
        ),
    }
    reasons = {
        "durable": ("explicit_durable_scope",),
        "projects": ("explicit_project_scope",),
        "federated": ("explicit_federated_scope",),
    }
    requested = mode_ids[request.mode]
    selected: list[str] = []
    rejected: list[RouteRejection] = []
    for retriever_id in requested:
        registration = registry.by_id(retriever_id)
        assert registration is not None  # checked by _validate_policy_registry
        if registration.descriptor.available:
            selected.append(retriever_id)
        else:
            rejected.append(
                RouteRejection(
                    target_id=retriever_id, reason_code="retriever_unavailable"
                )
            )
    if rejected:
        return _make_decision(
            policy=policy,
            request=request,
            resolution=resolution,
            outcome="refusal",
            rejections=tuple(rejected),
            reasons=("retriever_unavailable",),
        )
    return _make_decision(
        policy=policy,
        request=request,
        resolution=resolution,
        outcome="selected",
        selected=tuple(selected),
        reasons=reasons[request.mode],
    )


def _safe_default_decision(
    request: RouteRequest,
    resolution: IntentResolution,
    registry: CapabilityRegistry,
    policy: RouterPolicy,
) -> RouteDecision | None:
    retriever_id = policy.safe_default_retriever_id
    if retriever_id is None:
        return None
    registration = registry.by_id(retriever_id)
    assert registration is not None  # checked by _validate_policy_registry
    if not registration.descriptor.available:
        return _make_decision(
            policy=policy,
            request=request,
            resolution=resolution,
            outcome="refusal",
            rejections=(
                RouteRejection(
                    target_id=retriever_id, reason_code="retriever_unavailable"
                ),
            ),
            reasons=("retriever_unavailable",),
        )
    return _make_decision(
        policy=policy,
        request=request,
        resolution=resolution,
        outcome="fallback",
        selected=(retriever_id,),
        reasons=("safe_default",),
    )


def decide_route(
    request: RouteRequest,
    resolution: IntentResolution,
    registry: CapabilityRegistry,
    policy: RouterPolicy,
) -> RouteDecision:
    """Select retrievers deterministically without executing them."""
    _validate_policy_registry(registry, policy)
    for rule in policy.refusal_rules:
        if rule.matches(request.query_text):
            return _make_decision(
                policy=policy,
                request=request,
                resolution=resolution,
                outcome="refusal",
                reasons=(rule.reason_code,),
            )

    if request.mode != "auto":
        return _explicit_decision(request, resolution, registry, policy)

    if resolution.outcome == "refusal":
        reason = resolution.refusal_reason or "intent_invalid"
        if reason in {"intent_alias_ambiguous", "intent_rule_ambiguous"}:
            reason = "intent_ambiguous"
        elif reason != "intent_ambiguous":
            reason = "intent_invalid"
        return _make_decision(
            policy=policy,
            request=request,
            resolution=resolution,
            outcome="refusal",
            reasons=(reason,),
        )

    if resolution.outcome == "fallback":
        default = _safe_default_decision(request, resolution, registry, policy)
        if default is not None:
            return default
        reason = (
            "intent_unavailable"
            if resolution.refusal_reason == "intent_unavailable"
            else "intent_no_match"
        )
        return _make_decision(
            policy=policy,
            request=request,
            resolution=resolution,
            outcome="refusal",
            reasons=(reason,),
        )

    allowed = frozenset(request.allowed_capability_refs)
    selected: set[str] = set()
    rejected: list[RouteRejection] = []
    for capability_ref in resolution.rejected_capability_hints:
        reason = "capability_not_allowed"
        if f"binding_unavailable:{capability_ref}" in resolution.warnings:
            reason = "capability_unavailable"
        rejected.append(RouteRejection(target_id=capability_ref, reason_code=reason))
    for capability_ref in resolution.capability_hints:
        if capability_ref not in allowed:
            rejected.append(
                RouteRejection(
                    target_id=capability_ref, reason_code="capability_not_allowed"
                )
            )
            continue
        registration = registry.by_capability(capability_ref)
        if registration is None:
            rejected.append(
                RouteRejection(
                    target_id=capability_ref, reason_code="capability_unavailable"
                )
            )
            continue
        if not registration.descriptor.available:
            rejected.append(
                RouteRejection(
                    target_id=registration.descriptor.retriever_id,
                    reason_code="retriever_unavailable",
                )
            )
            continue
        selected.add(registration.descriptor.retriever_id)

    if len(selected) > 1 and not policy.allow_federated:
        return _make_decision(
            policy=policy,
            request=request,
            resolution=resolution,
            outcome="refusal",
            rejections=tuple(rejected),
            reasons=("federation_not_allowed",),
        )
    if selected:
        return _make_decision(
            policy=policy,
            request=request,
            resolution=resolution,
            outcome="selected",
            selected=tuple(selected),
            rejections=tuple(rejected),
            reasons=("intent_capability_hint",),
        )
    if rejected:
        reason_codes = {item.reason_code for item in rejected}
        reason = (
            "capability_unavailable"
            if reason_codes & {"capability_unavailable", "retriever_unavailable"}
            else "capability_not_allowed"
        )
        return _make_decision(
            policy=policy,
            request=request,
            resolution=resolution,
            outcome="refusal",
            rejections=tuple(rejected),
            reasons=(reason,),
        )
    default = _safe_default_decision(request, resolution, registry, policy)
    if default is not None:
        return default
    return _make_decision(
        policy=policy,
        request=request,
        resolution=resolution,
        outcome="refusal",
        reasons=("intent_no_match",),
    )


def open_query_store_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing document index without persisting connection changes."""
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise RoutingError(
            "retriever_unavailable", f"query store does not exist: {resolved}"
        )
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
        return conn
    except (sqlite3.Error, RuntimeError) as exc:
        try:
            conn.close()
        except (UnboundLocalError, sqlite3.Error):
            pass
        raise RoutingError(
            "retriever_unavailable", f"failed to open query store: {exc}"
        ) from exc


@dataclass(frozen=True)
class MindGraphQueryRetriever:
    descriptor: RetrieverDescriptor
    db_path: Path
    embedder: Any
    embedder_spec: EmbedderSpec | None = None
    embed_template: EmbedTemplate = "none"
    lexical_top_k: int = query_mod.DEFAULT_LEXICAL_TOP_K
    semantic_top_k: int = query_mod.DEFAULT_SEMANTIC_TOP_K
    expand: bool = False
    expand_depth: int = query_mod.DEFAULT_EXPAND_DEPTH
    expand_top_k: int = query_mod.DEFAULT_EXPAND_TOP_K
    associate: bool = False
    associate_top_k: int = query_mod.DEFAULT_ASSOCIATE_TOP_K
    associate_seed_k: int = query_mod.DEFAULT_ASSOCIATE_SEED_K

    def __post_init__(self) -> None:
        if not self.descriptor.available:
            raise RoutingError(
                "routing_registry_invalid",
                "MindGraphQueryRetriever requires an available descriptor",
            )

    def retrieve(self, query_text: str, *, final_top_k: int) -> list[QueryResult]:
        conn = open_query_store_read_only(self.db_path)
        try:
            formatted = (
                format_query_text(
                    self.embedder_spec, query_text, template=self.embed_template
                )
                if self.embedder_spec is not None
                else query_text
            )
            return query_mod.run_query(
                conn,
                formatted,
                self.embedder,
                lexical_top_k=self.lexical_top_k,
                semantic_top_k=self.semantic_top_k,
                final_top_k=final_top_k,
                expand=self.expand,
                expand_depth=self.expand_depth,
                expand_top_k=self.expand_top_k,
                associate=self.associate,
                associate_top_k=self.associate_top_k,
                associate_seed_k=self.associate_seed_k,
                embedder_spec=self.embedder_spec,
                embed_template=self.embed_template,
            )
        finally:
            conn.close()

    def registration(self) -> RetrieverRegistration:
        return RetrieverRegistration(descriptor=self.descriptor, executor=self)


def orchestrate(
    request: RouteRequest,
    *,
    intent_conn: sqlite3.Connection | None,
    registry: CapabilityRegistry,
    policy: RouterPolicy,
) -> RetrievalEnvelope:
    """Resolve intent, decide a route, and return separately ranked batches."""
    if intent_conn is None:
        resolution = _intent_unavailable_resolution()
    else:
        try:
            resolution = resolve_intent(
                intent_conn,
                request.query_text,
                intent_id=request.intent_id,
                scope=request.scope,
                allowed_capability_refs=frozenset(request.allowed_capability_refs),
                limits=TraversalLimits(),
            )
        except (IntentGraphError, sqlite3.Error):
            resolution = _intent_unavailable_resolution()

    decision = decide_route(request, resolution, registry, policy)
    if not decision.selected_retriever_ids:
        return RetrievalEnvelope(
            query_id=request.query_id,
            outcome=decision.outcome,
            decision=decision,
            warnings=tuple(sorted(set(resolution.warnings))),
        )

    batches: list[RetrieverBatch] = []
    failures: list[RetrieverFailure] = []
    for retriever_id in decision.selected_retriever_ids:
        registration = registry.by_id(retriever_id)
        assert registration is not None and registration.executor is not None
        try:
            results = registration.executor.retrieve(
                request.query_text, final_top_k=request.limits.final_top_k
            )
            mismatched = sorted(
                {
                    result.trust_profile
                    for result in results
                    if result.trust_profile is not None
                    and result.trust_profile
                    != registration.descriptor.trust_profile
                }
            )
            if mismatched:
                raise RoutingError(
                    "retriever_provenance_mismatch",
                    f"{retriever_id} returned unexpected trust profiles: "
                    + ", ".join(mismatched),
                )
            rows = tuple(
                RankedNomination(local_rank=index, result=result)
                for index, result in enumerate(results, start=1)
            )
            batches.append(
                RetrieverBatch(
                    retriever_id=retriever_id,
                    trust_profile=registration.descriptor.trust_profile,
                    source_surface=registration.descriptor.source_surface,
                    assembly_reason=decision.reason_codes[0],
                    rows=rows,
                )
            )
        except Exception as exc:  # noqa: BLE001 - failures belong in the envelope
            failures.append(
                RetrieverFailure(
                    retriever_id=retriever_id,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )

    if failures:
        outcome: EnvelopeOutcome = "partial"
    elif decision.outcome == "fallback":
        outcome = "fallback"
    else:
        outcome = "success"
    warnings = set(resolution.warnings)
    if failures:
        warnings.add("retriever_failed")
    return RetrievalEnvelope(
        query_id=request.query_id,
        outcome=outcome,
        decision=decision,
        batches=tuple(batches),
        failures=tuple(failures),
        partial=bool(failures),
        warnings=tuple(sorted(warnings)),
    )
