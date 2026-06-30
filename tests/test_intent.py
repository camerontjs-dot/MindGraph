from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from mindgraph.intent import (
    IntentGraphValidationError,
    IntentStoreError,
    IntentVersionConflict,
    TraversalLimits,
    compile_intent_corpus,
    open_intent_store,
    resolve_intent,
    validate_intent_corpus,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "intent_graph_cases.yaml"


def load_catalog() -> dict:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def catalog() -> dict:
    return load_catalog()


def phase1_document(catalog: dict) -> dict:
    return copy.deepcopy(catalog["documents"]["phase1"])


def predecessor_document(catalog: dict) -> dict:
    return copy.deepcopy(catalog["documents"]["predecessor"])


def write_corpus(root: Path, documents: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for index, document in enumerate(documents, start=1):
        version = document["graph"]["version"].replace(".", "-")
        path = root / f"{index:02d}-{version}.yaml"
        path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    return root


def compile_document(tmp_path: Path, document: dict, name: str = "intent.sqlite"):
    source = write_corpus(tmp_path / f"source-{name}", [document])
    destination = tmp_path / name
    result = compile_intent_corpus(source, destination)
    return destination, result


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expect_error(source: Path, code: str):
    with pytest.raises(IntentGraphValidationError) as raised:
        validate_intent_corpus(source)
    assert raised.value.code == code
    return raised.value


def goal(node_id: str, label: str | None = None) -> dict:
    return {
        "id": node_id,
        "kind": "goal",
        "label": label or node_id,
        "aliases": [],
        "status": "active",
        "source_refs": [
            "plan://30_projects/mindgraph/plans/mindgraph-remodel-plan.md"
        ],
    }


def edge(source: str, target: str, relation: str) -> dict:
    return {
        "source_id": source,
        "target_id": target,
        "relation": relation,
        "status": "active",
        "source_refs": [
            "plan://30_projects/mindgraph/plans/mindgraph-remodel-plan.md"
        ],
    }


def successor_from(document: dict, version: str, supersedes: str) -> dict:
    result = copy.deepcopy(document)
    result["graph"]["version"] = version
    result["graph"]["supersedes"] = supersedes
    result["graph"]["created_at"] = "2026-06-29T00:00:00Z"
    result["graph"]["reviewed_at"] = "2026-06-29T00:00:00Z"
    return result


def test_strict_source_schema_rejects_extra_fields(tmp_path, catalog):
    document = phase1_document(catalog)
    document["unexpected"] = True
    error = expect_error(write_corpus(tmp_path / "source", [document]), "intent_schema_invalid")
    assert "unexpected" in (error.location or "")


def test_valid_compile_has_expected_schema_and_metadata(tmp_path, catalog):
    destination, result = compile_document(tmp_path, phase1_document(catalog))

    assert result.graph_id == "mainframe.core"
    assert result.current_version == "2026-06-29.1"
    assert result.version_count == 1
    assert result.node_count == 13
    assert result.edge_count == 12
    assert result.binding_count == 4
    assert result.rule_count == 1
    assert destination.stat().st_mode & 0o777 == 0o600

    conn = open_intent_store(destination)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert tables == {
            "schema_meta",
            "graph_versions",
            "intent_nodes",
            "intent_aliases",
            "intent_edges",
            "intent_bindings",
            "intent_rules",
        }
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_canonical_hash_ignores_nonsemantic_list_and_yaml_order(tmp_path, catalog):
    first = phase1_document(catalog)
    second = copy.deepcopy(first)
    second["nodes"].reverse()
    second["edges"].reverse()
    second["bindings"].reverse()
    second["rules"].reverse()
    for node in second["nodes"]:
        node["aliases"].reverse()
        node["source_refs"].reverse()

    first_result = compile_intent_corpus(
        write_corpus(tmp_path / "one", [first]), tmp_path / "one.sqlite"
    )
    second_result = compile_intent_corpus(
        write_corpus(tmp_path / "two", [second]), tmp_path / "two.sqlite"
    )
    assert first_result.corpus_hash == second_result.corpus_hash
    assert first_result.version_hashes == second_result.version_hashes
    assert (tmp_path / "one.sqlite").read_bytes() == (tmp_path / "two.sqlite").read_bytes()


def test_semantic_source_change_changes_hash(tmp_path, catalog):
    first = phase1_document(catalog)
    second = copy.deepcopy(first)
    second["nodes"][0]["label"] = "A changed goal label"
    one = compile_intent_corpus(
        write_corpus(tmp_path / "one", [first]), tmp_path / "one.sqlite"
    )
    two = compile_intent_corpus(
        write_corpus(tmp_path / "two", [second]), tmp_path / "two.sqlite"
    )
    assert one.corpus_hash != two.corpus_hash


def test_invalid_compile_preserves_existing_destination_bytes(tmp_path, catalog):
    destination, _ = compile_document(tmp_path, phase1_document(catalog))
    before = destination.read_bytes()
    invalid = phase1_document(catalog)
    invalid["edges"].append(edge("goal.missing", "goal.deep-root", "requires"))

    with pytest.raises(IntentGraphValidationError):
        compile_intent_corpus(
            write_corpus(tmp_path / "invalid", [invalid]), destination
        )
    assert destination.read_bytes() == before


def test_corrupt_existing_store_is_not_replaced(tmp_path, catalog):
    document = phase1_document(catalog)
    destination, _ = compile_document(tmp_path, document)
    conn = sqlite3.connect(destination)
    try:
        conn.execute(
            "UPDATE schema_meta SET value = 'missing-version' "
            "WHERE key = 'current_version'"
        )
        conn.commit()
    finally:
        conn.close()
    before = destination.read_bytes()

    with pytest.raises(IntentStoreError) as raised:
        compile_intent_corpus(
            write_corpus(tmp_path / "replacement", [document]), destination
        )
    assert raised.value.code == "intent_store_invalid"
    assert destination.read_bytes() == before


def test_duplicate_alias_missing_target_relation_and_kind_errors(tmp_path, catalog):
    cases: list[tuple[str, dict, str]] = []

    duplicate = phase1_document(catalog)
    duplicate["nodes"].append(copy.deepcopy(duplicate["nodes"][0]))
    cases.append(("duplicate", duplicate, "intent_duplicate_id"))

    alias = phase1_document(catalog)
    alias["nodes"][0]["aliases"] = ["Run the route contract checks."]
    cases.append(("alias", alias, "intent_alias_ambiguous"))

    missing = phase1_document(catalog)
    missing["edges"].append(edge("goal.mindgraph-remodel", "goal.missing", "requires"))
    cases.append(("missing", missing, "intent_missing_target"))

    relation = phase1_document(catalog)
    relation["edges"].append(edge("goal.deep-root", "goal.deep-1", "causes"))
    cases.append(("relation", relation, "intent_relation_invalid"))

    kind = phase1_document(catalog)
    kind["edges"].append(
        edge("goal.deep-root", "capability.fixture-evaluator", "requires")
    )
    cases.append(("kind", kind, "intent_kind_mismatch"))

    for name, document, code in cases:
        expect_error(write_corpus(tmp_path / name, [document]), code)


@pytest.mark.parametrize("relation", ["requires", "decomposes_to", "next_step"])
def test_all_controlled_cycles_are_rejected_with_witness(
    tmp_path, catalog, relation
):
    document = phase1_document(catalog)
    document["nodes"].extend(
        [goal("goal.cyclic-a", "Cyclic A"), goal("goal.cyclic-b", "Cyclic B")]
    )
    document["edges"].extend(
        [
            edge("goal.cyclic-a", "goal.cyclic-b", relation),
            edge("goal.cyclic-b", "goal.cyclic-a", relation),
        ]
    )
    error = expect_error(
        write_corpus(tmp_path / relation, [document]), "intent_cycle_detected"
    )
    assert error.witness == (
        "goal.cyclic-a",
        "goal.cyclic-b",
        "goal.cyclic-a",
    )


def test_binding_availability_contract(tmp_path, catalog):
    optional = phase1_document(catalog)
    validate_intent_corpus(write_corpus(tmp_path / "optional", [optional]))

    required = phase1_document(catalog)
    required["bindings"][0]["availability"] = "unavailable"
    expect_error(
        write_corpus(tmp_path / "required", [required]),
        "intent_binding_unavailable",
    )


def test_version_lineage_append_only_and_effective_status(tmp_path, catalog):
    predecessor = predecessor_document(catalog)
    source_v1 = write_corpus(tmp_path / "v1", [predecessor])
    destination = tmp_path / "intent.sqlite"
    compile_intent_corpus(source_v1, destination)

    successor = successor_from(
        phase1_document(catalog), "2026-06-29.1", "2026-06-28.1"
    )
    result = compile_intent_corpus(
        write_corpus(tmp_path / "v2", [predecessor, successor]), destination
    )
    assert result.version_count == 2
    assert result.current_version == "2026-06-29.1"
    conn = open_intent_store(destination)
    try:
        statuses = [
            tuple(row)
            for row in conn.execute(
                "SELECT version, effective_status FROM graph_versions ORDER BY version"
            )
        ]
        assert statuses == [
            ("2026-06-28.1", "superseded"),
            ("2026-06-29.1", "approved"),
        ]
    finally:
        conn.close()


def test_approved_version_mutation_and_removal_are_rejected(tmp_path, catalog):
    predecessor = predecessor_document(catalog)
    successor = successor_from(
        phase1_document(catalog), "2026-06-29.1", "2026-06-28.1"
    )
    destination = tmp_path / "intent.sqlite"
    compile_intent_corpus(
        write_corpus(tmp_path / "complete", [predecessor, successor]), destination
    )
    before = destination.read_bytes()

    changed = copy.deepcopy(predecessor)
    changed["nodes"][0]["label"] = "Changed approved history"
    with pytest.raises(IntentVersionConflict) as conflict:
        compile_intent_corpus(
            write_corpus(tmp_path / "changed", [changed, successor]), destination
        )
    assert conflict.value.code == "intent_version_conflict"
    assert destination.read_bytes() == before

    successor_as_root = copy.deepcopy(successor)
    successor_as_root["graph"]["supersedes"] = None
    with pytest.raises(IntentVersionConflict) as removed:
        compile_intent_corpus(
            write_corpus(tmp_path / "removed", [successor_as_root]), destination
        )
    assert removed.value.code == "intent_version_missing"
    assert destination.read_bytes() == before


def test_missing_predecessor_and_fork_are_rejected(tmp_path, catalog):
    missing = successor_from(
        phase1_document(catalog), "2026-06-29.1", "2026-06-27.1"
    )
    expect_error(
        write_corpus(tmp_path / "missing", [missing]), "intent_version_missing"
    )

    root = predecessor_document(catalog)
    first = successor_from(root, "2026-06-29.1", "2026-06-28.1")
    second = successor_from(root, "2026-06-29.2", "2026-06-28.1")
    expect_error(
        write_corpus(tmp_path / "fork", [root, first, second]),
        "intent_version_fork",
    )


def test_open_is_read_only_and_missing_file_is_not_created(tmp_path, catalog):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(IntentStoreError):
        open_intent_store(missing)
    assert not missing.exists()

    destination, _ = compile_document(tmp_path, phase1_document(catalog))
    conn = open_intent_store(destination)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM intent_nodes")
    finally:
        conn.close()


def test_explicit_resolution_returns_exact_prerequisites_trace_and_hints(
    tmp_path, catalog
):
    destination, _ = compile_document(tmp_path, phase1_document(catalog))
    conn = open_intent_store(destination)
    try:
        resolution = resolve_intent(
            conn,
            "Validate the retrieval remodel before implementation.",
            intent_id="goal.mindgraph-remodel",
            allowed_capability_refs=frozenset(
                {
                    "retriever://mindgraph-projects",
                    "capability://fixture-evaluator",
                }
            ),
        )
    finally:
        conn.close()

    assert resolution.outcome == "resolved"
    assert resolution.resolution_method == "explicit"
    assert resolution.intent_path == (
        "goal.mindgraph-remodel",
        "goal.route-contract-evaluation",
        "goal.intent-schema-validation",
    )
    assert resolution.prerequisite_goal_ids == (
        "goal.route-contract-evaluation",
        "goal.intent-schema-validation",
    )
    assert [edge.model_dump() for edge in resolution.edge_path] == [
        {
            "source_id": "goal.mindgraph-remodel",
            "target_id": "goal.route-contract-evaluation",
            "relation": "requires",
        },
        {
            "source_id": "goal.route-contract-evaluation",
            "target_id": "goal.intent-schema-validation",
            "relation": "requires",
        },
    ]
    assert set(resolution.capability_hints) == {
        "retriever://mindgraph-projects",
        "capability://fixture-evaluator",
    }
    assert resolution.constraint_ids == ("constraint.review-required",)
    assert "blocked_by:constraint.review-required" in resolution.warnings


def test_alias_rule_and_no_match_resolution(tmp_path, catalog):
    document = phase1_document(catalog)
    document["edges"] = [
        item
        for item in document["edges"]
        if not (
            item["source_id"] == "goal.route-contract-evaluation"
            and item["relation"] == "requires"
        )
    ]
    destination, _ = compile_document(tmp_path, document)
    conn = open_intent_store(destination)
    try:
        alias = resolve_intent(
            conn,
            "  RUN   THE ROUTE CONTRACT CHECKS. ",
            allowed_capability_refs=frozenset({"capability://fixture-evaluator"}),
        )
        rule = resolve_intent(
            conn,
            "Review the next project status",
            scope="PROJECT_STATUS",
            allowed_capability_refs=frozenset({"retriever://mindgraph-projects"}),
        )
        missing = resolve_intent(conn, "Help me with an unknown goal")
    finally:
        conn.close()

    assert alias.resolution_method == "alias"
    assert alias.intent_path == ("goal.route-contract-evaluation",)
    assert rule.resolution_method == "rule"
    assert rule.matched_goal_ids == ("goal.project-status-review",)
    assert missing.outcome == "fallback"
    assert missing.refusal_reason == "intent_no_match"
    assert missing.as_contract_result("missing")["behaviors"] == [
        "do not query all stores",
        "require explicit scope",
    ]


def test_tied_top_rules_for_different_goals_refuse(tmp_path, catalog):
    document = phase1_document(catalog)
    document["rules"].append(
        {
            "id": "rule.project-status-remodel",
            "priority": 100,
            "goal_id": "goal.mindgraph-remodel",
            "match": {
                "scope": "project_status",
                "all_terms": ["project", "status"],
                "any_terms": ["review"],
            },
            "source_refs": ["decision://30_projects/mindgraph/decisions.md"],
        }
    )
    destination, _ = compile_document(tmp_path, document)
    conn = open_intent_store(destination)
    try:
        resolution = resolve_intent(
            conn, "Review the project status", scope="project_status"
        )
    finally:
        conn.close()
    assert resolution.outcome == "refusal"
    assert resolution.refusal_reason == "intent_rule_ambiguous"


def test_capability_allowlist_and_optional_unavailable_binding(tmp_path, catalog):
    destination, _ = compile_document(tmp_path, phase1_document(catalog))
    conn = open_intent_store(destination)
    try:
        resolution = resolve_intent(
            conn,
            "Use the project review goal",
            intent_id="goal.project-status-review",
            allowed_capability_refs=frozenset({"retriever://mindgraph-projects"}),
        )
    finally:
        conn.close()
    assert resolution.capability_hints == ("retriever://mindgraph-projects",)
    assert set(resolution.rejected_capability_hints) == {
        "retriever://gmail",
        "capability://optional-unavailable",
    }
    assert "capability_not_allowed:retriever://gmail" in resolution.warnings
    assert (
        "binding_unavailable:capability://optional-unavailable" in resolution.warnings
    )
    assert "policy rejected untrusted capability hint" in resolution.as_contract_result(
        "intent06"
    )["behaviors"]


def test_depth_and_node_limits_are_inclusive_and_visible(tmp_path, catalog):
    destination, _ = compile_document(tmp_path, phase1_document(catalog))
    conn = open_intent_store(destination)
    try:
        depth = resolve_intent(
            conn, "deep", intent_id="goal.deep-root", limits=TraversalLimits(max_depth=2)
        )
        nodes = resolve_intent(
            conn,
            "deep",
            intent_id="goal.deep-root",
            limits=TraversalLimits(max_depth=32, max_nodes=2),
        )
    finally:
        conn.close()
    assert depth.intent_path == ("goal.deep-root", "goal.deep-1", "goal.deep-2")
    assert depth.truncation_reason == "max_depth"
    assert depth.as_contract_result("intent07")["reason"] == "intent_traversal_truncated"
    assert nodes.intent_path == ("goal.deep-root", "goal.deep-1")
    assert nodes.truncation_reason == "max_nodes"


def test_runtime_alias_ambiguity_fails_closed(tmp_path, catalog):
    destination, _ = compile_document(tmp_path, phase1_document(catalog))
    conn = sqlite3.connect(destination)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("ALTER TABLE intent_aliases RENAME TO valid_intent_aliases")
        conn.execute(
            """
            CREATE TABLE intent_aliases (
                graph_id TEXT, version TEXT, normalized_alias TEXT,
                node_id TEXT, alias TEXT
            )
            """
        )
        conn.executemany(
            "INSERT INTO intent_aliases VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "mainframe.core",
                    "2026-06-29.1",
                    "ambiguous",
                    "goal.mindgraph-remodel",
                    "Ambiguous",
                ),
                (
                    "mainframe.core",
                    "2026-06-29.1",
                    "ambiguous",
                    "goal.project-status-review",
                    "Ambiguous",
                ),
            ],
        )
        conn.commit()
        resolution = resolve_intent(conn, "ambiguous")
    finally:
        conn.close()
    assert resolution.outcome == "refusal"
    assert resolution.refusal_reason == "intent_alias_ambiguous"
    assert resolution.capability_hints == ()


def test_runtime_cycle_fails_closed_without_capability_hints(tmp_path, catalog):
    destination, _ = compile_document(tmp_path, phase1_document(catalog))
    conn = sqlite3.connect(destination)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO intent_edges VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "mainframe.core",
                "2026-06-29.1",
                "goal.intent-schema-validation",
                "goal.mindgraph-remodel",
                "requires",
                "active",
                "[]",
            ),
        )
        conn.commit()
        resolution = resolve_intent(
            conn,
            "cycle",
            intent_id="goal.mindgraph-remodel",
            allowed_capability_refs=frozenset(
                {"retriever://mindgraph-projects", "capability://fixture-evaluator"}
            ),
        )
    finally:
        conn.close()
    assert resolution.outcome == "refusal"
    assert resolution.refusal_reason == "intent_cycle_detected"
    assert resolution.intent_path == (
        "goal.intent-schema-validation",
        "goal.mindgraph-remodel",
        "goal.route-contract-evaluation",
        "goal.intent-schema-validation",
    )
    assert resolution.capability_hints == ()


def _invalid_candidate(
    probe_id: str,
    error: IntentGraphValidationError,
    *,
    method: str,
    matched: list[str],
    path: list[str],
) -> dict:
    return {
        "id": probe_id,
        "outcome": "refusal",
        "reason": error.code,
        "resolution_method": method,
        "matched_goal_ids": matched,
        "prerequisite_goal_ids": [],
        "intent_path": path,
        "capability_hints": [],
        "behaviors": [],
        "fields": [
            "graph_id",
            "graph_version",
            "intent_path",
            "matched_goal_ids",
            "resolution_method",
        ],
        "graph_id": error.graph_id or "mainframe.core",
        "graph_version": error.graph_version or "invalid",
    }


def build_phase1_candidates(tmp_path: Path, catalog: dict | None = None) -> dict:
    """Build actual candidate rows for external Phase 1 evaluator verification."""
    catalog = catalog or load_catalog()
    document = phase1_document(catalog)
    destination, _ = compile_document(tmp_path, document, "phase1.sqlite")
    conn = open_intent_store(destination)
    try:
        intent01 = resolve_intent(
            conn,
            "Validate the retrieval remodel before implementation.",
            intent_id="goal.mindgraph-remodel",
            allowed_capability_refs=frozenset(
                {
                    "retriever://mindgraph-projects",
                    "capability://fixture-evaluator",
                }
            ),
        ).as_contract_result("intent01_explicit_goal_prerequisites")
        intent05 = resolve_intent(
            conn, "Handle a goal that is not in the approved graph."
        ).as_contract_result("intent05_missing_goal_fallback")
        intent06 = resolve_intent(
            conn,
            "Use a reviewed project goal that contains an untrusted Gmail hint.",
            intent_id="goal.project-status-review",
            allowed_capability_refs=frozenset({"retriever://mindgraph-projects"}),
        ).as_contract_result("intent06_untrusted_route_hint_rejected")
        intent07 = resolve_intent(
            conn,
            "Resolve a deep goal path with the V1 traversal limit.",
            intent_id="goal.deep-root",
        ).as_contract_result("intent07_bounded_traversal_truncation")
    finally:
        conn.close()

    alias_document = phase1_document(catalog)
    alias_document["edges"] = [
        item
        for item in alias_document["edges"]
        if not (
            item["source_id"] == "goal.route-contract-evaluation"
            and item["relation"] == "requires"
        )
    ]
    alias_destination, _ = compile_document(
        tmp_path, alias_document, "alias.sqlite"
    )
    alias_conn = open_intent_store(alias_destination)
    try:
        intent02 = resolve_intent(
            alias_conn,
            "Run the route contract checks.",
            allowed_capability_refs=frozenset({"capability://fixture-evaluator"}),
        ).as_contract_result("intent02_deterministic_alias_resolution")
    finally:
        alias_conn.close()

    alias_invalid = phase1_document(catalog)
    alias_invalid["nodes"][0]["aliases"] = ["Run the route contract checks."]
    with pytest.raises(IntentGraphValidationError) as alias_error:
        validate_intent_corpus(write_corpus(tmp_path / "ambiguous", [alias_invalid]))
    intent03 = _invalid_candidate(
        "intent03_ambiguous_alias_refusal",
        alias_error.value,
        method="none",
        matched=[],
        path=[],
    )

    cycle_invalid = phase1_document(catalog)
    cycle_invalid["nodes"].extend(
        [goal("goal.cyclic-a", "Cyclic A"), goal("goal.cyclic-b", "Cyclic B")]
    )
    cycle_invalid["edges"].extend(
        [
            edge("goal.cyclic-a", "goal.cyclic-b", "requires"),
            edge("goal.cyclic-b", "goal.cyclic-a", "requires"),
        ]
    )
    with pytest.raises(IntentGraphValidationError) as cycle_error:
        validate_intent_corpus(write_corpus(tmp_path / "cycle", [cycle_invalid]))
    intent04 = _invalid_candidate(
        "intent04_cycle_refusal",
        cycle_error.value,
        method="explicit",
        matched=["goal.cyclic-a"],
        path=list(cycle_error.value.witness[:-1]),
    )

    return {
        "schema_version": "1",
        "results": [
            intent01,
            intent02,
            intent03,
            intent04,
            intent05,
            intent06,
            intent07,
        ],
    }


def test_phase1_candidate_adapter_covers_all_seven_cases(tmp_path, catalog):
    candidate = build_phase1_candidates(tmp_path, catalog)
    results = {item["id"]: item for item in candidate["results"]}
    expected = catalog["phase1_expected"]
    assert set(results) == set(expected)

    for probe_id, expectation in expected.items():
        observed = results[probe_id]
        for key, value in expectation.items():
            if key == "forbidden_capability_hints":
                assert not set(value) & set(observed["capability_hints"])
            elif key == "capability_hints":
                assert set(observed[key]) == set(value)
            else:
                assert observed[key] == value

    first = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
    second = json.dumps(
        build_phase1_candidates(tmp_path / "repeat", catalog),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert first == second
