from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from mindgraph import cli, db
from mindgraph.intent import (
    IntentResolution,
    compile_intent_corpus,
    open_intent_store,
)
from mindgraph.models import QueryResult
from mindgraph import query as query_mod
from mindgraph.routing import (
    CapabilityRegistry,
    MindGraphQueryRetriever,
    RefusalRule,
    RetrieverDescriptor,
    RetrieverRegistration,
    RouteRequest,
    RouterPolicy,
    RoutingError,
    decide_route,
    open_query_store_read_only,
    orchestrate,
)


ROUTING_FIXTURE = Path(__file__).parent / "fixtures" / "phase3_routing_cases.yaml"
INTENT_FIXTURE = Path(__file__).parent / "fixtures" / "intent_graph_cases.yaml"


def load_routing_fixture() -> dict:
    return yaml.safe_load(ROUTING_FIXTURE.read_text(encoding="utf-8"))


def make_result(
    retriever_id: str,
    *,
    trust_profile: str,
    score: float,
    path: str | None = None,
) -> QueryResult:
    display_path = path or f"{retriever_id}/result.md"
    return QueryResult(
        doc_id=f"{retriever_id}-doc",
        chunk_index=0,
        path=display_path,
        title=f"{retriever_id} result",
        index_id=retriever_id,
        trust_profile=trust_profile,
        namespace=retriever_id,
        source_root=f"/fixture/{retriever_id}",
        source_path="result.md",
        display_path=display_path,
        signal="fused",
        rrf_score=score,
        lexical_rank=1,
        semantic_rank=1,
        semantic_distance=0.25,
        chunk_text=f"fixture row from {retriever_id}",
    )


class StubRetriever:
    def __init__(self, results: list[QueryResult] | None = None) -> None:
        self.results = results or []
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query_text: str, *, final_top_k: int) -> list[QueryResult]:
        self.calls.append((query_text, final_top_k))
        return list(self.results[:final_top_k])


class FailingRetriever:
    def __init__(self, message: str) -> None:
        self.message = message
        self.calls = 0

    def retrieve(self, query_text: str, *, final_top_k: int) -> list[QueryResult]:
        self.calls += 1
        raise RuntimeError(self.message)


def descriptor(
    retriever_id: str,
    capability_ref: str,
    trust_profile: str,
    *,
    available: bool = True,
) -> RetrieverDescriptor:
    return RetrieverDescriptor(
        retriever_id=retriever_id,
        capability_ref=capability_ref,
        trust_profile=trust_profile,
        source_surface=f"fixture://{retriever_id}",
        available=available,
        unavailable_reason=None if available else "fixture_unavailable",
    )


def make_registry(
    *,
    durable: object | None = None,
    projects: object | None = None,
    extras: tuple[RetrieverRegistration, ...] = (),
) -> CapabilityRegistry:
    durable_executor = durable if durable is not None else StubRetriever()
    project_executor = projects if projects is not None else StubRetriever()
    return CapabilityRegistry(
        (
            RetrieverRegistration(
                descriptor(
                    "mindgraph-projects",
                    "retriever://mindgraph-projects",
                    "project_status",
                ),
                project_executor,
            ),
            RetrieverRegistration(
                descriptor(
                    "mindgraph-durable",
                    "retriever://mindgraph-durable",
                    "durable_knowledge",
                ),
                durable_executor,
            ),
            *extras,
        )
    )


@pytest.fixture
def policy() -> RouterPolicy:
    return RouterPolicy.model_validate(load_routing_fixture()["policy"])


def unavailable_resolution(reason: str = "intent_unavailable") -> IntentResolution:
    return IntentResolution(
        graph_id="unavailable",
        graph_version="unavailable",
        source_hash="unavailable",
        outcome="fallback",
        resolution_method="none",
        refusal_reason=reason,
    )


def resolved(
    *,
    hints: tuple[str, ...] = (),
    rejected: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
) -> IntentResolution:
    return IntentResolution(
        graph_id="mainframe.core",
        graph_version="2026-06-30.1",
        source_hash="fixture-hash",
        outcome="resolved",
        resolution_method="explicit",
        matched_goal_ids=("goal.fixture",),
        intent_path=("goal.fixture",),
        capability_hints=hints,
        rejected_capability_hints=rejected,
        warnings=warnings,
    )


@pytest.fixture
def intent_conn(tmp_path: Path):
    catalog = yaml.safe_load(INTENT_FIXTURE.read_text(encoding="utf-8"))
    source = tmp_path / "intent-source"
    source.mkdir()
    (source / "mainframe-core.yaml").write_text(
        yaml.safe_dump(catalog["documents"]["phase1"], sort_keys=False),
        encoding="utf-8",
    )
    destination = tmp_path / "intent.sqlite"
    compile_intent_corpus(source, destination)
    conn = open_intent_store(destination)
    try:
        yield conn
    finally:
        conn.close()


class TinyEmbedder:
    def encode(self, texts, convert_to_numpy=True, show_progress_bar=False):
        output = np.zeros((len(texts), 384), dtype=np.float32)
        for index, text in enumerate(texts):
            lowered = text.lower()
            if "zebra" in lowered or "striped horse" in lowered:
                output[index, 0] = 1.0
            if "compiler" in lowered:
                output[index, 1] = 1.0
        return output


@pytest.fixture
def indexed_db(tmp_path: Path, monkeypatch):
    embedder = TinyEmbedder()
    monkeypatch.setattr(cli, "_load_embedder", lambda *_a, **_k: embedder)
    notes = tmp_path / "vault"
    notes.mkdir()
    (notes / "zebra.md").write_text(
        "A zebra is a striped horse on the savannah.\n", encoding="utf-8"
    )
    (notes / "compiler.md").write_text(
        "A compiler translates programming languages.\n", encoding="utf-8"
    )
    destination = tmp_path / "documents.sqlite"
    stats = cli._ingest_directory(
        notes,
        str(destination),
        index_id="fixture-durable",
        trust_profile="durable_knowledge",
        namespace="fixture",
        source_root=notes,
        display_prefix="10_knowledge/fixture",
    )
    assert stats["failed"] == 0
    return destination, embedder


def test_wire_contracts_are_strict_and_duplicate_capabilities_fail():
    with pytest.raises(ValidationError):
        RouteRequest(query_id="route.strict", query_text="test", surprise=True)
    with pytest.raises(ValidationError):
        RouteRequest(
            query_id="route.duplicate",
            query_text="test",
            allowed_capability_refs=(
                "retriever://mindgraph-durable",
                "retriever://mindgraph-durable",
            ),
        )
    with pytest.raises(ValidationError):
        RefusalRule(rule_id="privacy.empty", reason_code="privacy_refusal")

    privacy = RefusalRule(
        rule_id="privacy.raw-transcript",
        reason_code="privacy_refusal",
        phrases=("raw transcript",),
    )
    assert privacy.matches("Find the raw transcript, please.") is True
    assert privacy.matches("Find the straw transcript, please.") is False


def test_registry_rejects_duplicate_ids_capabilities_and_executor_mismatch():
    first = RetrieverRegistration(
        descriptor("one", "retriever://one", "durable_knowledge"), StubRetriever()
    )
    duplicate_id = RetrieverRegistration(
        descriptor("one", "retriever://two", "project_status"), StubRetriever()
    )
    with pytest.raises(RoutingError, match="duplicate retriever ID"):
        CapabilityRegistry((first, duplicate_id))

    duplicate_capability = RetrieverRegistration(
        descriptor("two", "retriever://one", "project_status"), StubRetriever()
    )
    with pytest.raises(RoutingError, match="duplicate capability ref"):
        CapabilityRegistry((first, duplicate_capability))

    with pytest.raises(RoutingError, match="has no executor"):
        RetrieverRegistration(
            descriptor("missing", "retriever://missing", "project_status")
        )
    with pytest.raises(RoutingError, match="has an executor"):
        RetrieverRegistration(
            descriptor(
                "future", "retriever://future", "future_trust", available=False
            ),
            StubRetriever(),
        )

    registry = CapabilityRegistry((first,))
    with pytest.raises(FrozenInstanceError):
        registry._registrations = ()
    with pytest.raises(TypeError):
        registry._by_id["other"] = first


@pytest.mark.parametrize(
    "case_id",
    ["explicit_durable", "explicit_projects", "explicit_federated"],
)
def test_explicit_routes_follow_reviewed_fixture(case_id: str, policy: RouterPolicy):
    case = load_routing_fixture()["requests"][case_id]
    decision = decide_route(
        RouteRequest.model_validate(case["request"]),
        unavailable_resolution(),
        make_registry(),
        policy,
    )
    assert decision.outcome == case["expected_outcome"]
    assert list(decision.selected_retriever_ids) == case["expected_retrievers"]
    assert decision.reason_codes == (case["expected_reason"],)


def test_explicit_federation_requires_policy_permission(policy: RouterPolicy):
    request = RouteRequest(
        query_id="route.federation-denied",
        query_text="Use both stores",
        mode="federated",
    )
    decision = decide_route(
        request,
        unavailable_resolution(),
        make_registry(),
        policy.model_copy(update={"allow_federated": False}),
    )
    assert decision.outcome == "refusal"
    assert decision.selected_retriever_ids == ()
    assert decision.reason_codes == ("federation_not_allowed",)


def test_automatic_intent_hint_selects_only_allowed_project_store(
    policy: RouterPolicy, intent_conn
):
    case = load_routing_fixture()["requests"]["automatic_project_hint"]
    project = StubRetriever(
        [
            make_result(
                "mindgraph-projects", trust_profile="project_status", score=0.4
            )
        ]
    )
    envelope = orchestrate(
        RouteRequest.model_validate(case["request"]),
        intent_conn=intent_conn,
        registry=make_registry(projects=project),
        policy=policy,
    )
    assert envelope.outcome == "success"
    assert envelope.decision.selected_retriever_ids == ("mindgraph-projects",)
    assert envelope.decision.reason_codes == (case["expected_reason"],)
    assert [batch.retriever_id for batch in envelope.batches] == [
        "mindgraph-projects"
    ]
    assert project.calls == [(case["request"]["query_text"], 10)]


def test_missing_intent_store_preserves_explicit_scope_and_safe_default(
    policy: RouterPolicy,
):
    durable = StubRetriever(
        [make_result("mindgraph-durable", trust_profile="durable_knowledge", score=0.2)]
    )
    registry = make_registry(durable=durable)
    explicit = orchestrate(
        RouteRequest(
            query_id="route.no-intent-explicit",
            query_text="durable method",
            mode="durable",
        ),
        intent_conn=None,
        registry=registry,
        policy=policy,
    )
    fallback = orchestrate(
        RouteRequest(
            query_id="route.no-intent-auto", query_text="unclassified", mode="auto"
        ),
        intent_conn=None,
        registry=registry,
        policy=policy,
    )
    assert explicit.outcome == "success"
    assert explicit.decision.reason_codes == ("explicit_durable_scope",)
    assert fallback.outcome == "fallback"
    assert fallback.decision.selected_retriever_ids == ("mindgraph-durable",)
    assert fallback.decision.reason_codes == ("safe_default",)


def test_missing_or_unmatched_intent_without_default_refuses(policy: RouterPolicy):
    no_default = policy.model_copy(update={"safe_default_retriever_id": None})
    registry = make_registry()
    unavailable = decide_route(
        RouteRequest(query_id="route.intent-missing", query_text="unknown"),
        unavailable_resolution(),
        registry,
        no_default,
    )
    no_match = decide_route(
        RouteRequest(query_id="route.intent-no-match", query_text="unknown"),
        unavailable_resolution("intent_no_match"),
        registry,
        no_default,
    )
    assert unavailable.reason_codes == ("intent_unavailable",)
    assert no_match.reason_codes == ("intent_no_match",)
    assert unavailable.selected_retriever_ids == no_match.selected_retriever_ids == ()


@pytest.mark.parametrize(
    ("refusal_reason", "expected"),
    [
        ("intent_alias_ambiguous", "intent_ambiguous"),
        ("intent_rule_ambiguous", "intent_ambiguous"),
        ("intent_cycle_detected", "intent_invalid"),
        ("intent_store_invalid", "intent_invalid"),
    ],
)
def test_ambiguous_cycle_and_corrupt_intent_never_route(
    refusal_reason: str, expected: str, policy: RouterPolicy
):
    resolution = IntentResolution(
        graph_id="mainframe.core",
        graph_version="corrupt",
        source_hash="corrupt",
        outcome="refusal",
        resolution_method="none",
        refusal_reason=refusal_reason,
    )
    decision = decide_route(
        RouteRequest(query_id="route.intent-refusal", query_text="test"),
        resolution,
        make_registry(),
        policy,
    )
    assert decision.outcome == "refusal"
    assert decision.selected_retriever_ids == ()
    assert decision.reason_codes == (expected,)


def test_disallowed_or_unavailable_capability_is_not_replaced_by_default(
    policy: RouterPolicy,
):
    registry = make_registry(
        extras=(
            RetrieverRegistration(
                descriptor(
                    "future-live",
                    "retriever://future-live",
                    "time_bound_live_state",
                    available=False,
                )
            ),
        )
    )
    disallowed = decide_route(
        RouteRequest(query_id="route.disallowed", query_text="test"),
        resolved(
            rejected=("retriever://mindgraph-projects",),
            warnings=(
                "capability_not_allowed:retriever://mindgraph-projects",
            ),
        ),
        registry,
        policy,
    )
    unavailable = decide_route(
        RouteRequest(
            query_id="route.unavailable",
            query_text="test",
            allowed_capability_refs=("retriever://future-live",),
        ),
        resolved(hints=("retriever://future-live",)),
        registry,
        policy,
    )
    assert disallowed.reason_codes == ("capability_not_allowed",)
    assert unavailable.reason_codes == ("capability_unavailable",)
    assert disallowed.selected_retriever_ids == unavailable.selected_retriever_ids == ()


def test_raw_transcript_policy_refuses_before_retriever_runs(policy: RouterPolicy):
    case = load_routing_fixture()["requests"]["raw_transcript_refusal"]
    durable = StubRetriever()
    envelope = orchestrate(
        RouteRequest.model_validate(case["request"]),
        intent_conn=None,
        registry=make_registry(durable=durable),
        policy=policy,
    )
    assert envelope.outcome == "refusal"
    assert envelope.decision.reason_codes == (case["expected_reason"],)
    assert envelope.batches == ()
    assert durable.calls == []
    candidate = envelope.as_contract_result("route07_raw_transcript_request")
    assert candidate["outcome"] == "refusal_or_explicit_consent_gate"
    assert candidate["reason"] == "raw_transcripts_not_in_default_retrieval"


def test_grouped_federation_preserves_local_order_without_global_rerank(
    policy: RouterPolicy,
):
    durable = StubRetriever(
        [
            make_result(
                "mindgraph-durable", trust_profile="durable_knowledge", score=0.01
            ),
            make_result(
                "mindgraph-durable-2",
                trust_profile="durable_knowledge",
                score=0.005,
            ),
        ]
    )
    projects = StubRetriever(
        [make_result("mindgraph-projects", trust_profile="project_status", score=0.99)]
    )
    envelope = orchestrate(
        RouteRequest(
            query_id="route.grouped",
            query_text="plan across both",
            mode="federated",
        ),
        intent_conn=None,
        registry=make_registry(durable=durable, projects=projects),
        policy=policy,
    )
    assert envelope.outcome == "success"
    assert [batch.retriever_id for batch in envelope.batches] == [
        "mindgraph-durable",
        "mindgraph-projects",
    ]
    assert [row.local_rank for row in envelope.batches[0].rows] == [1, 2]
    assert envelope.batches[0].rows[0].result.rrf_score == 0.01
    assert envelope.batches[1].rows[0].result.rrf_score == 0.99
    candidate = envelope.as_contract_result("route.grouped")
    assert candidate["selected_retrievers"] == [
        "mindgraph-durable",
        "mindgraph-projects",
    ]
    assert "group results by retriever and trust profile" in candidate["behaviors"]


def test_partial_failures_are_visible_when_one_or_all_retrievers_fail(
    policy: RouterPolicy,
):
    durable_result = make_result(
        "mindgraph-durable", trust_profile="durable_knowledge", score=0.1
    )
    one_failure = orchestrate(
        RouteRequest(
            query_id="route.partial-one", query_text="both", mode="federated"
        ),
        intent_conn=None,
        registry=make_registry(
            durable=StubRetriever([durable_result]),
            projects=FailingRetriever("project fixture failed"),
        ),
        policy=policy,
    )
    all_fail = orchestrate(
        RouteRequest(
            query_id="route.partial-all", query_text="both", mode="federated"
        ),
        intent_conn=None,
        registry=make_registry(
            durable=FailingRetriever("durable fixture failed"),
            projects=FailingRetriever("project fixture failed"),
        ),
        policy=policy,
    )
    assert one_failure.outcome == "partial"
    assert one_failure.partial is True
    assert [batch.retriever_id for batch in one_failure.batches] == [
        "mindgraph-durable"
    ]
    assert [failure.retriever_id for failure in one_failure.failures] == [
        "mindgraph-projects"
    ]
    assert all_fail.outcome == "partial"
    assert all_fail.batches == ()
    assert [failure.retriever_id for failure in all_fail.failures] == [
        "mindgraph-durable",
        "mindgraph-projects",
    ]


def test_mismatched_retriever_trust_is_isolated_as_a_failure(policy: RouterPolicy):
    mislabeled = StubRetriever(
        [
            make_result(
                "mindgraph-projects",
                trust_profile="durable_knowledge",
                score=0.9,
            )
        ]
    )
    envelope = orchestrate(
        RouteRequest(
            query_id="route.provenance-mismatch",
            query_text="project status",
            mode="projects",
        ),
        intent_conn=None,
        registry=make_registry(projects=mislabeled),
        policy=policy,
    )
    assert envelope.outcome == "partial"
    assert envelope.batches == ()
    assert envelope.failures[0].error_type == "RoutingError"
    assert "unexpected trust profiles" in envelope.failures[0].message


def test_decisions_and_envelopes_serialize_byte_deterministically(policy: RouterPolicy):
    durable = StubRetriever(
        [make_result("mindgraph-durable", trust_profile="durable_knowledge", score=0.1)]
    )
    request = RouteRequest(
        query_id="route.deterministic", query_text="durable", mode="durable"
    )
    registry = make_registry(durable=durable)
    first = orchestrate(
        request, intent_conn=None, registry=registry, policy=policy
    ).model_dump_json()
    second = orchestrate(
        request, intent_conn=None, registry=registry, policy=policy
    ).model_dump_json()
    assert first.encode("utf-8") == second.encode("utf-8")
    assert json.loads(first)["batches"][0]["retriever_id"] == "mindgraph-durable"


def test_read_only_adapter_matches_direct_query_and_does_not_change_store(indexed_db):
    db_path, embedder = indexed_db
    direct_conn = db.get_db(str(db_path))
    try:
        direct = query_mod.run_query(
            direct_conn, "zebra", embedder, final_top_k=5
        )
    finally:
        direct_conn.close()
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    query_store = open_query_store_read_only(db_path)
    try:
        assert query_store.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            query_store.execute("CREATE TABLE forbidden_write(id INTEGER)")
    finally:
        query_store.close()

    retriever = MindGraphQueryRetriever(
        descriptor=descriptor(
            "mindgraph-durable",
            "retriever://mindgraph-durable",
            "durable_knowledge",
        ),
        db_path=db_path,
        embedder=embedder,
    )
    adapted = retriever.retrieve("zebra", final_top_k=5)
    after = hashlib.sha256(db_path.read_bytes()).hexdigest()

    assert [item.model_dump() for item in adapted] == [
        item.model_dump() for item in direct
    ]
    assert before == after
    assert adapted[0].trust_profile == "durable_knowledge"
    assert adapted[0].display_path.startswith("10_knowledge/fixture/")


def test_policy_must_reference_registered_retrievers(policy: RouterPolicy):
    registry = CapabilityRegistry(
        (
            RetrieverRegistration(
                descriptor(
                    "mindgraph-durable",
                    "retriever://mindgraph-durable",
                    "durable_knowledge",
                ),
                StubRetriever(),
            ),
        )
    )
    with pytest.raises(RoutingError, match="policy references missing retrievers"):
        decide_route(
            RouteRequest(query_id="route.invalid-policy", query_text="test"),
            unavailable_resolution(),
            registry,
            policy,
        )
