"""Embedder registry and text formatting tests."""

import pytest

from mindgraph import embedders


def test_resolve_default_embedder():
    spec = embedders.resolve_embedder(None)
    assert spec.key == "minilm"
    assert spec.dimensions == 384


def test_resolve_bge_and_e5_prefixes():
    bge = embedders.resolve_embedder("bge-small")
    assert bge.query_prefix == "query: "
    e5 = embedders.resolve_embedder("e5-small")
    assert e5.passage_prefix == "passage: "


def test_unknown_embedder_raises():
    with pytest.raises(ValueError, match="Unknown embedder"):
        embedders.resolve_embedder("not-a-model")


def test_mainframe_template_prefixes():
    spec = embedders.resolve_embedder("minilm")
    query = embedders.format_query_text(
        spec, "regulated crosswalk", template="mainframe"
    )
    assert query.startswith("[intent=query]")
    passage = embedders.format_passage_text(
        spec,
        "chunk body",
        template="mainframe",
        title="GxP Note",
        domain="regulated-systems",
        doc_type="note",
    )
    assert "[domain=regulated-systems]" in passage
    assert "[type=note]" in passage
    assert "GxP Note" in passage