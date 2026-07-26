"""C-0 eligibility-manifest boundary tests for governed MindGraph context."""

from __future__ import annotations

import pytest

from mindgraph.models import QueryResult
from mindgraph.query import QueryError, apply_dual_gate_governance


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


def test_manifest_is_required_and_cannot_be_empty() -> None:
    result = _result("doc-1", path="/vault/10_knowledge/a.md", content_hash="a" * 64)
    with pytest.raises(QueryError, match="requires a C-0"):
        apply_dual_gate_governance([result], eligibility_manifest=None)
    with pytest.raises(QueryError, match="approved_inventory is empty"):
        apply_dual_gate_governance([result], eligibility_manifest=_manifest())


def test_status_alone_never_admits_a_result() -> None:
    result = _result("doc-1", path="/vault/10_knowledge/a.md", content_hash="a" * 64)
    result = result.model_copy(update={"status": "Approved"})
    assert apply_dual_gate_governance(
        [result],
        eligibility_manifest=_manifest(
            _approved("other", "/vault/10_knowledge/b.md", "b" * 64)
        ),
    ) == []


@pytest.mark.parametrize(
    ("record_path", "record_hash"),
    [
        ("/vault/10_knowledge/other.md", "a" * 64),
        ("/vault/10_knowledge/a.md", "b" * 64),
    ],
)
def test_path_or_hash_mismatch_is_excluded(record_path: str, record_hash: str) -> None:
    result = _result("doc-1", path="/vault/10_knowledge/a.md", content_hash="a" * 64)
    governed = apply_dual_gate_governance(
        [result],
        eligibility_manifest=_manifest(_approved("doc-1", record_path, record_hash)),
    )
    assert governed == []


def test_exact_manifest_match_preserves_consumed_run_identity() -> None:
    result = _result("doc-1", path="/vault/10_knowledge/a.md", content_hash="a" * 64)
    governed = apply_dual_gate_governance(
        [result],
        eligibility_manifest=_manifest(
            _approved("doc-1", "/vault/10_knowledge/a.md", "a" * 64)
        ),
    )
    assert len(governed) == 1
    assert governed[0].eligibility_run_id == "c0-test-run"


def test_seat_cap_applies_after_manifest_filtering() -> None:
    results = [
        _result(
            f"doc-{index}",
            path=f"/vault/10_knowledge/{index}.md",
            content_hash=f"{index:x}" * 64,
        )
        for index in range(1, 5)
    ]
    inventory = [
        _approved(result.doc_id, result.path, result.content_hash or "")
        for result in results
    ]
    governed = apply_dual_gate_governance(
        results,
        max_seats=3,
        eligibility_manifest=_manifest(*inventory),
    )
    assert [result.doc_id for result in governed] == ["doc-1", "doc-2", "doc-3"]
