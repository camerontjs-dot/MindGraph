"""Separable C-0 filtering and context allocation (DECISIONS.md § 2026-07-27).

These cover the two extracted primitives and the two evaluation-only entry
points. They prove behaviour parity and the ungoverned arm's containment; they
make no claim that either apparatus improves retrieval, and no held-out
evaluation is run here. ``tests/test_governance.py`` remains the untouched
regression proof for the governed path.
"""

from __future__ import annotations

import pytest

from mindgraph.models import QueryResult
from mindgraph.query import (
    QueryError,
    _allocate_context_budget,
    _filter_by_c0_eligibility,
    allocate_ungoverned_context,
    apply_dual_gate_governance,
    filter_by_c0_eligibility,
)


def _result(doc_id: str, *, path: str, content_hash: str, text: str = "passage") -> QueryResult:
    return QueryResult(
        doc_id=doc_id,
        chunk_index=0,
        path=path,
        title=doc_id,
        signal="fused",
        rrf_score=1.0,
        lexical_rank=1,
        semantic_rank=1,
        chunk_text=text,
        content_hash=content_hash,
    )


def _manifest(*records: dict) -> dict:
    return {"eligibility_run_id": "c0-test-run", "approved_inventory": list(records)}


def _approved(doc_id: str, path: str, content_hash: str) -> dict:
    return {
        "doc_id": doc_id,
        "path": path,
        "sha256": f"sha256:{content_hash}",
        "status": "Approved",
    }


def _series(count: int, *, texts: list[str] | None = None) -> list[QueryResult]:
    """``count`` distinct results, doc-1..doc-N, each with a valid hex hash."""
    return [
        _result(
            f"doc-{index}",
            path=f"/vault/10_knowledge/{index}.md",
            content_hash=f"{index:x}" * 64,
            text="passage" if texts is None else texts[index - 1],
        )
        for index in range(1, count + 1)
    ]


def _inventory_for(results: list[QueryResult]) -> list[dict]:
    return [
        _approved(result.doc_id, result.path, result.content_hash or "")
        for result in results
    ]


# --- composition equivalence -------------------------------------------------


@pytest.mark.parametrize(
    ("max_seats", "max_chars", "quiet_keywords"),
    [
        (3, 4000, None),
        (2, 4000, ["signal"]),
        (5, 4000, ["absent-keyword"]),
        (3, 12, None),
        (0, 4000, None),
    ],
)
def test_governed_helper_equals_filter_then_allocate(
    max_seats: int, max_chars: int, quiet_keywords: list[str] | None
) -> None:
    results = _series(4, texts=["alpha", "beta", "gamma", "delta signal"])
    manifest = _manifest(*_inventory_for(results))

    composed = _allocate_context_budget(
        _filter_by_c0_eligibility(results, manifest),
        max_seats,
        max_chars,
        quiet_keywords,
    )
    governed = apply_dual_gate_governance(
        results,
        max_seats,
        max_chars,
        quiet_keywords,
        eligibility_manifest=manifest,
    )
    assert governed == composed


def test_manifest_validation_still_precedes_argument_validation() -> None:
    """Error precedence is unchanged by the split: manifest errors win."""
    results = _series(1)
    with pytest.raises(QueryError, match="requires a C-0"):
        apply_dual_gate_governance(results, -1, -1, eligibility_manifest=None)
    with pytest.raises(QueryError, match="requires a C-0"):
        apply_dual_gate_governance(results, 0, 4000, eligibility_manifest=None)
    # With a valid manifest the negative-argument check is what raises.
    with pytest.raises(QueryError, match="must be non-negative"):
        apply_dual_gate_governance(
            results, -1, 4000, eligibility_manifest=_manifest(*_inventory_for(results))
        )


# --- C-0-only arm ------------------------------------------------------------


def test_filter_applies_no_seat_or_character_cap() -> None:
    results = _series(5, texts=["x" * 5000] * 5)
    filtered = filter_by_c0_eligibility(
        results, eligibility_manifest=_manifest(*_inventory_for(results))
    )
    assert [result.doc_id for result in filtered] == [
        "doc-1",
        "doc-2",
        "doc-3",
        "doc-4",
        "doc-5",
    ]
    assert all(result.chunk_text == "x" * 5000 for result in filtered)
    assert all(result.eligibility_run_id == "c0-test-run" for result in filtered)


def test_public_filter_matches_private_filter() -> None:
    results = _series(3)
    manifest = _manifest(*_inventory_for(results[:2]))
    assert filter_by_c0_eligibility(
        results, eligibility_manifest=manifest
    ) == _filter_by_c0_eligibility(results, manifest)


# --- Speaker-only arm --------------------------------------------------------


def test_ungoverned_admits_a_result_with_no_manifest_record() -> None:
    results = _series(2)
    manifest_without_doc_2 = _manifest(*_inventory_for(results[:1]))

    governed = apply_dual_gate_governance(
        results, eligibility_manifest=manifest_without_doc_2
    )
    assert [result.doc_id for result in governed] == ["doc-1"]

    ungoverned = allocate_ungoverned_context(results, evaluation_use_only=True)
    assert [result.doc_id for result in ungoverned] == ["doc-1", "doc-2"]


def test_ungoverned_never_emits_eligibility_provenance() -> None:
    results = _series(2)
    already_governed = [
        result.model_copy(update={"eligibility_run_id": "c0-earlier-run"})
        for result in results
    ]
    ungoverned = allocate_ungoverned_context(already_governed, evaluation_use_only=True)
    assert [result.doc_id for result in ungoverned] == ["doc-1", "doc-2"]
    assert all(result.eligibility_run_id is None for result in ungoverned)
    # The inputs are not mutated on the way through.
    assert all(result.eligibility_run_id == "c0-earlier-run" for result in already_governed)


def test_ungoverned_requires_an_explicit_acknowledgement() -> None:
    results = _series(1)
    with pytest.raises(TypeError):
        allocate_ungoverned_context(results)  # type: ignore[call-arg]
    for value in (False, None, 1, "True"):
        with pytest.raises(QueryError, match="evaluation_use_only=True"):
            allocate_ungoverned_context(results, evaluation_use_only=value)  # type: ignore[arg-type]


def test_ungoverned_refuses_a_manifest_keyword() -> None:
    results = _series(1)
    with pytest.raises(QueryError, match="takes no eligibility manifest"):
        allocate_ungoverned_context(
            results,
            evaluation_use_only=True,
            eligibility_manifest=_manifest(*_inventory_for(results)),
        )


def test_ungoverned_rejects_other_unexpected_keywords() -> None:
    results = _series(1)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        allocate_ungoverned_context(
            results, evaluation_use_only=True, not_a_parameter=True
        )


# --- allocator parity between the arms ---------------------------------------


_SHORT = "a" * 100
_LONG = "b" * 300


@pytest.mark.parametrize(
    ("texts", "max_seats", "max_chars", "quiet_keywords"),
    [
        # plain seat cap
        (["alpha", "beta", "gamma", "delta"], 3, 4000, None),
        # seat cap wider than the input
        (["alpha", "beta"], 5, 4000, None),
        # swap: last shortlisted item replaced by the first later keyword match
        (["alpha", "beta", "gamma", "delta signal", "epsilon signal"], 3, 4000, ["signal"]),
        # swap needs len(results) > max_seats, so this leaves the shortlist alone
        (["alpha", "beta", "gamma signal"], 3, 4000, ["signal"]),
        # falsy quiet_keywords never engages the swap
        (["alpha", "beta", "gamma", "delta signal"], 3, 4000, []),
        # keyword present but not in a later candidate
        (["alpha signal", "beta", "gamma", "delta"], 3, 4000, ["signal"]),
        # truncation boundary: remaining == 101 admits a shortened passage
        ([_SHORT, _LONG, "gamma"], 3, 201, None),
        # truncation boundary: remaining == 100 admits nothing
        ([_SHORT, _LONG, "gamma"], 3, 200, None),
        # first passage alone blows the budget
        ([_LONG, "beta"], 3, 150, None),
        # zero seats
        (["alpha", "beta"], 0, 4000, None),
    ],
)
def test_allocator_behaviour_is_identical_across_both_arms(
    texts: list[str],
    max_seats: int,
    max_chars: int,
    quiet_keywords: list[str] | None,
) -> None:
    results = _series(len(texts), texts=texts)
    manifest = _manifest(*_inventory_for(results))

    governed = apply_dual_gate_governance(
        results, max_seats, max_chars, quiet_keywords, eligibility_manifest=manifest
    )
    ungoverned = allocate_ungoverned_context(
        results,
        max_seats,
        max_chars,
        quiet_keywords,
        evaluation_use_only=True,
    )

    # Identical seats, order, and passage text; they differ only in provenance.
    assert [(item.doc_id, item.chunk_text) for item in governed] == [
        (item.doc_id, item.chunk_text) for item in ungoverned
    ]
    assert all(item.eligibility_run_id == "c0-test-run" for item in governed)
    assert all(item.eligibility_run_id is None for item in ungoverned)


def test_truncation_boundary_is_strictly_greater_than_one_hundred() -> None:
    results = _series(2, texts=[_SHORT, _LONG])

    admitted = allocate_ungoverned_context(
        results, 3, 201, evaluation_use_only=True
    )
    assert [item.doc_id for item in admitted] == ["doc-1", "doc-2"]
    assert admitted[1].chunk_text == _LONG[:101] + "..."

    excluded = allocate_ungoverned_context(
        results, 3, 200, evaluation_use_only=True
    )
    assert [item.doc_id for item in excluded] == ["doc-1"]


def test_swap_replaces_the_last_seat_with_the_first_later_match() -> None:
    texts = ["alpha", "beta", "gamma", "delta signal", "epsilon signal"]
    results = _series(5, texts=texts)
    allocated = allocate_ungoverned_context(
        results, 3, 4000, ["SIGNAL"], evaluation_use_only=True
    )
    assert [item.doc_id for item in allocated] == ["doc-1", "doc-2", "doc-4"]


# --- allocation is subtractive at document granularity -----------------------


@pytest.mark.parametrize(
    ("texts", "max_seats", "max_chars", "quiet_keywords"),
    [
        (["alpha", "beta", "gamma", "delta"], 3, 4000, None),
        (["alpha", "beta", "gamma", "delta signal"], 3, 4000, ["signal"]),
        ([_SHORT, _LONG, "gamma"], 3, 201, None),
        ([_SHORT, _LONG, "gamma"], 3, 200, None),
        (["alpha", "beta"], 0, 4000, None),
    ],
)
def test_allocation_is_an_order_preserving_document_subsequence(
    texts: list[str],
    max_seats: int,
    max_chars: int,
    quiet_keywords: list[str] | None,
) -> None:
    results = _series(len(texts), texts=texts)
    allocated = allocate_ungoverned_context(
        results, max_seats, max_chars, quiet_keywords, evaluation_use_only=True
    )

    input_ids = [item.doc_id for item in results]
    output_ids = [item.doc_id for item in allocated]

    # No doc_id is invented.
    assert set(output_ids) <= set(input_ids)
    # Order is preserved: output_ids is a subsequence of input_ids.
    remaining = iter(input_ids)
    assert all(doc_id in remaining for doc_id in output_ids)
