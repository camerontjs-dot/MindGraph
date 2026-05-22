import pytest

from mindgraph.exceptions import ParseError
from mindgraph.parser import (
    chunk_truth,
    compute_doc_id,
    extract_graph_edges,
    parse_document,
    parse_frontmatter,
    split_page_model,
)


class TestParseFrontmatter:
    def test_with_frontmatter(self):
        text = "---\ntitle: Hello\ntags: [a, b]\n---\nBody here"
        meta, body = parse_frontmatter(text)
        assert meta == {"title": "Hello", "tags": ["a", "b"]}
        assert body == "Body here"

    def test_without_frontmatter(self):
        text = "Just body, no frontmatter"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_empty_frontmatter(self):
        text = "---\n\n---\nBody"
        meta, body = parse_frontmatter(text)
        assert meta == {}
        assert body == "Body"

    def test_malformed_yaml_raises(self):
        text = "---\ntitle: : :\n  bad indent\n---\nBody"
        with pytest.raises(ParseError):
            parse_frontmatter(text)

    def test_non_mapping_frontmatter_raises(self):
        text = "---\n- just\n- a\n- list\n---\nBody"
        with pytest.raises(ParseError):
            parse_frontmatter(text)


class TestSplitPageModel:
    def test_truth_only(self):
        body = "# Heading\n\nSome content here."
        truth, timeline = split_page_model(body)
        assert truth == "# Heading\n\nSome content here."
        assert timeline is None

    def test_truth_and_timeline(self):
        body = "Truth content here.\n\n---\n## Timeline\n\n- 2026-01-01: thing\n- 2026-02-01: other"
        truth, timeline = split_page_model(body)
        assert truth == "Truth content here."
        assert timeline == "- 2026-01-01: thing\n- 2026-02-01: other"

    def test_internal_hr_not_split(self):
        """A plain `---` HR in the Truth section must not trigger the split."""
        body = "Section A\n\n---\n\nSection B\n\n---\n\nSection C"
        truth, timeline = split_page_model(body)
        assert truth == body.strip()
        assert timeline is None

    def test_internal_hr_then_real_timeline(self):
        """Internal HRs come first, then a real `## Timeline` block."""
        body = (
            "Section A\n\n---\n\nSection B\n\n"
            "---\n## Timeline\n- event"
        )
        truth, timeline = split_page_model(body)
        assert truth.startswith("Section A")
        assert "Section B" in truth
        assert "## Timeline" not in truth
        assert timeline == "- event"

    def test_case_insensitive_heading(self):
        body = "Truth\n\n---\n## TIMELINE\n- e"
        _, timeline = split_page_model(body)
        assert timeline == "- e"

    def test_blank_lines_between_dash_and_heading(self):
        body = "Truth\n\n---\n\n## Timeline\n- e"
        truth, timeline = split_page_model(body)
        assert truth == "Truth"
        assert timeline == "- e"


class TestExtractGraphEdges:
    def test_plain_link(self):
        edges = extract_graph_edges("see [[people/alice]]", source_id="src")
        assert len(edges) == 1
        assert edges[0].source_id == "src"
        assert edges[0].target_id == compute_doc_id("people/alice.md")
        assert edges[0].relationship_type is None

    def test_link_with_relationship(self):
        edges = extract_graph_edges(
            "see [[people/alice]] (works_at)", source_id="src"
        )
        assert len(edges) == 1
        assert edges[0].relationship_type == "works_at"

    def test_multiple_links_in_paragraph(self):
        text = "intro [[a]] middle [[b]] (rel) end [[c/d]]"
        edges = extract_graph_edges(text, source_id="src")
        assert len(edges) == 3
        assert edges[1].relationship_type == "rel"
        assert edges[2].target_id == compute_doc_id("c/d.md")

    def test_nested_brackets_ignored(self):
        """A `[[link[with]inner]]` shape should not produce a malformed edge."""
        edges = extract_graph_edges(
            "[[normal]] and [[link[inner]brackets]]", source_id="src"
        )
        assert len(edges) == 1
        assert edges[0].target_id == compute_doc_id("normal.md")

    def test_link_already_has_md_extension(self):
        edges = extract_graph_edges("[[notes/foo.md]]", source_id="src")
        assert edges[0].target_id == compute_doc_id("notes/foo.md")


class TestChunkTruth:
    def test_empty(self):
        assert chunk_truth("") == []
        assert chunk_truth("   \n\n  ") == []

    def test_single_short_paragraph(self):
        assert chunk_truth("hello world") == ["hello world"]

    def test_respects_max_chars(self):
        # Two paragraphs that together exceed max_chars must split.
        p1 = "a" * 600
        p2 = "b" * 600
        body = f"{p1}\n\n{p2}"
        chunks = chunk_truth(body, max_chars=1000)
        assert len(chunks) == 2
        assert chunks[0] == p1
        assert chunks[1] == p2

    def test_packs_paragraphs_until_limit(self):
        p1 = "a" * 400
        p2 = "b" * 400
        p3 = "c" * 400
        body = f"{p1}\n\n{p2}\n\n{p3}"
        chunks = chunk_truth(body, max_chars=1000)
        # First two pack together (400 + 2 + 400 = 802 ≤ 1000), third spills.
        assert len(chunks) == 2
        assert "a" * 400 in chunks[0]
        assert "b" * 400 in chunks[0]
        assert chunks[1] == p3

    def test_oversized_paragraph_kept_intact(self):
        # A paragraph alone larger than max_chars still becomes one chunk.
        p = "x" * 2000
        chunks = chunk_truth(p, max_chars=1000)
        assert chunks == [p]


class TestParseDocument:
    def test_end_to_end(self):
        body = (
            "---\n"
            "title: My Note\n"
            "domain: personal\n"
            "---\n"
            "Truth content with [[people/alice]] (knows).\n\n"
            "---\n## Timeline\n- 2026-01-01: created"
        )
        doc = parse_document("notes/my-note.md", body.encode("utf-8"))
        assert doc.id == compute_doc_id("notes/my-note.md")
        assert doc.title == "My Note"
        assert doc.metadata["domain"] == "personal"
        assert "people/alice" in doc.truth_text
        assert doc.timeline_text == "- 2026-01-01: created"
        assert doc.content_hash  # not empty

    def test_title_falls_back_to_filename(self):
        body = b"No frontmatter, just body."
        doc = parse_document("notes/example.md", body)
        assert doc.title == "example"
        assert doc.timeline_text is None

    def test_invalid_utf8_raises(self):
        with pytest.raises(ParseError):
            parse_document("notes/bad.md", b"\xff\xfe\x00invalid")
