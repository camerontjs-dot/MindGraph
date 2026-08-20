"""Trust classification and partitioning.

Ranking is trust-blind: a fabricated document is written to be on topic, so it
scores like any other and can take the top rank. On 2026-08-20, live queries
returned quarantined documents from the 2026-08-09 fabricated-citations
incident at rank 1, carrying only a prose warning. These cover the structural
signal that lets a consumer act on that without parsing English.
"""

from mindgraph import query as query_mod
from mindgraph.models import QueryResult


def _result(doc_id: str, citation_class: str = "citable") -> QueryResult:
    return QueryResult(
        doc_id=doc_id,
        chunk_index=0,
        path=f"notes/{doc_id}.md",
        title=doc_id,
        signal="fused",
        rrf_score=0.03,
        lexical_rank=1,
        semantic_rank=1,
        citation_class=citation_class,
        chunk_text="body",
    )


class TestCitationAssessment:
    def test_quarantined_status_is_not_citable(self):
        cls, warning = query_mod._citation_assessment({"status": "quarantined"})
        assert cls == "not_citable"
        assert warning.startswith("NOT CITABLE")

    def test_retracted_and_superseded_are_not_citable(self):
        for status in ("retracted", "superseded"):
            assert query_mod._citation_assessment({"status": status})[0] == "not_citable"

    def test_fabricated_citation_tag_is_not_citable(self):
        cls, _ = query_mod._citation_assessment({"tags": ["fabricated-citation"]})
        assert cls == "not_citable"

    def test_tags_parse_from_a_bare_string(self):
        cls, _ = query_mod._citation_assessment({"tags": "[quarantined, other]"})
        assert cls == "not_citable"

    def test_needs_audit_is_unverified_not_barred(self):
        cls, warning = query_mod._citation_assessment({"tags": ["needs-audit"]})
        assert cls == "unverified"
        assert warning.startswith("UNVERIFIED")

    def test_clean_frontmatter_is_citable_with_no_warning(self):
        assert query_mod._citation_assessment({"status": "active"}) == ("citable", None)

    def test_warning_wrapper_agrees_with_classification(self):
        for metadata in (
            {"status": "quarantined"},
            {"tags": ["needs-audit"]},
            {"status": "active"},
        ):
            cls, warning = query_mod._citation_assessment(metadata)
            assert query_mod._provenance_warning(metadata) == warning
            assert (warning is None) == (cls == "citable")


class TestPartition:
    def test_not_citable_is_separated_from_candidates(self):
        results = [
            _result("fabricated", "not_citable"),
            _result("good"),
            _result("capture", "unverified"),
        ]
        candidates, excluded = query_mod.partition_by_citation(results)
        assert [r.doc_id for r in candidates] == ["good", "capture"]
        assert [r.doc_id for r in excluded] == ["fabricated"]

    def test_partition_preserves_rank_order_and_drops_nothing(self):
        results = [_result(f"d{i}", "not_citable" if i % 2 else "citable") for i in range(6)]
        candidates, excluded = query_mod.partition_by_citation(results)
        assert len(candidates) + len(excluded) == len(results)
        assert [r.doc_id for r in candidates] == ["d0", "d2", "d4"]
        assert [r.doc_id for r in excluded] == ["d1", "d3", "d5"]

    def test_a_top_ranked_fabricated_document_cannot_lead_the_candidates(self):
        # The live failure: the highest-scoring hit was quarantined.
        results = [_result("top_hit_fabricated", "not_citable"), _result("real")]
        candidates, _ = query_mod.partition_by_citation(results)
        assert candidates[0].doc_id == "real"

    def test_counts_report_all_three_classes(self):
        results = [
            _result("a"),
            _result("b", "unverified"),
            _result("c", "not_citable"),
            _result("d", "not_citable"),
        ]
        assert query_mod.citation_counts(results) == {
            "citable": 1,
            "unverified": 1,
            "not_citable": 2,
        }

    def test_counts_are_zero_filled_for_an_empty_result_set(self):
        assert query_mod.citation_counts([]) == {
            "citable": 0,
            "unverified": 0,
            "not_citable": 0,
        }


class TestSerialization:
    def test_citation_class_survives_model_dump(self):
        row = _result("x", "not_citable").model_dump()
        assert row["citation_class"] == "not_citable"

    def test_citation_class_defaults_to_citable(self):
        assert _result("x").model_dump()["citation_class"] == "citable"
