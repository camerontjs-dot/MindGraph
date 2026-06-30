import pytest

from mindgraph.exceptions import ParseError
from mindgraph.parser import (
    LinkResolver,
    canonical_trailing_slug,
    chunk_truth,
    compute_doc_id,
    extract_document_graph_edges,
    extract_graph_edges,
    extract_metadata_link_targets,
    normalize_link_label,
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
    @pytest.fixture
    def link_resolver(self):
        docs = [
            parse_document(
                "agents/source.md",
                b"---\ntitle: Source\n---\nBody",
            ),
            parse_document(
                "agents/foo.md",
                b"---\ntitle: Agent Foo\n---\nBody",
            ),
            parse_document(
                "ai-business/cross-domain.md",
                b"---\ntitle: Cross Domain Note\n---\nBody",
            ),
            parse_document(
                "knowledge-systems/title-target.md",
                b"---\ntitle: Display Title\n---\nBody",
            ),
            parse_document(
                "finance/duplicate.md",
                b"---\ntitle: Shared Title\n---\nBody",
            ),
            parse_document(
                "agents/duplicate.md",
                b"---\ntitle: Shared Title\n---\nBody",
            ),
        ]
        return LinkResolver.from_documents(docs)

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

    def test_resolves_explicit_path_target(self, link_resolver):
        edges = extract_graph_edges(
            "[[agents/foo]] and [[agents/foo.md]]",
            source_id="src",
            link_resolver=link_resolver,
            source_path="agents/source.md",
        )

        assert [e.target_id for e in edges] == [
            compute_doc_id("agents/foo.md"),
            compute_doc_id("agents/foo.md"),
        ]

    def test_resolves_same_directory_bare_target_first(self, link_resolver):
        edges = extract_graph_edges(
            "[[foo]]",
            source_id="src",
            link_resolver=link_resolver,
            source_path="agents/source.md",
        )

        assert edges[0].target_id == compute_doc_id("agents/foo.md")

    def test_resolves_unique_cross_domain_stem(self, link_resolver):
        edges = extract_graph_edges(
            "[[cross-domain]]",
            source_id="src",
            link_resolver=link_resolver,
            source_path="agents/source.md",
        )

        assert edges[0].target_id == compute_doc_id("ai-business/cross-domain.md")

    def test_resolves_unique_title(self, link_resolver):
        edges = extract_graph_edges(
            "[[Display Title]]",
            source_id="src",
            link_resolver=link_resolver,
            source_path="agents/source.md",
        )

        assert edges[0].target_id == compute_doc_id("knowledge-systems/title-target.md")

    def test_ambiguous_target_remains_dangling(self, link_resolver):
        edges = extract_graph_edges(
            "[[duplicate]] and [[Shared Title]]",
            source_id="src",
            link_resolver=link_resolver,
            source_path="knowledge-systems/source.md",
        )

        assert [e.target_id for e in edges] == [
            compute_doc_id("duplicate.md"),
            compute_doc_id("Shared Title.md"),
        ]

    def test_missing_target_remains_dangling(self, link_resolver):
        edges = extract_graph_edges(
            "[[missing]]",
            source_id="src",
            link_resolver=link_resolver,
            source_path="agents/source.md",
        )

        assert edges[0].target_id == compute_doc_id("missing.md")

    def test_resolves_unique_canonical_trailing_slug(self):
        docs = [
            parse_document(
                "regulated-systems/2026-06-13__regulated-systems__raw__gxp-pharma-source-catalog.md",
                b"---\ntitle: Catalog\n---\nBody",
            ),
            parse_document(
                "regulated-systems/source.md",
                b"---\ntitle: Source\n---\nBody",
            ),
        ]
        resolver = LinkResolver.from_documents(docs)
        edges = extract_graph_edges(
            "[[gxp-pharma-source-catalog]]",
            source_id="src",
            link_resolver=resolver,
            source_path="regulated-systems/source.md",
        )

        assert edges[0].target_id == docs[0].id

    def test_ambiguous_trailing_slug_remains_dangling(self):
        docs = [
            parse_document(
                "regulated-systems/2026-06-13__regulated-systems__note__shared-slug.md",
                b"---\ntitle: One\n---\nBody",
            ),
            parse_document(
                "knowledge-systems/2026-06-14__knowledge-systems__note__shared-slug.md",
                b"---\ntitle: Two\n---\nBody",
            ),
        ]
        resolver = LinkResolver.from_documents(docs)
        edges = extract_graph_edges(
            "[[shared-slug]]",
            source_id="src",
            link_resolver=resolver,
            source_path="regulated-systems/source.md",
        )

        assert edges[0].target_id == compute_doc_id("shared-slug.md")


class TestCanonicalTrailingSlug:
    def test_extracts_slug_from_canonical_stem(self):
        stem = "2026-06-13__regulated-systems__raw__gxp-pharma-source-catalog"
        assert canonical_trailing_slug(stem) == "gxp-pharma-source-catalog"

    def test_non_canonical_stem_returns_none(self):
        assert canonical_trailing_slug("legacy-short-name") is None


class TestMetadataLinks:
    def test_extract_metadata_link_targets_dedupes(self):
        metadata = {
            "links": [
                "hybrid-memory-in-practice",
                "[[hybrid-memory-in-practice]]",
                "data-integrity-alcoa",
            ]
        }
        assert extract_metadata_link_targets(metadata) == [
            "hybrid-memory-in-practice",
            "data-integrity-alcoa",
        ]

    def test_normalize_link_label_strips_wrapper_and_alias(self):
        assert normalize_link_label("[[target|Display]]") == "target"


class TestExtractDocumentGraphEdges:
    def test_indexes_frontmatter_and_body_without_duplicates(self):
        docs = [
            parse_document(
                "knowledge-systems/2026-06-13__knowledge-systems__note__hybrid-memory-in-practice.md",
                b"---\ntitle: Hybrid\n---\nBody",
            ),
            parse_document(
                "knowledge-systems/2026-06-04__knowledge-systems__note__knowledge-graph-rag-architecture.md",
                b"---\ntitle: KG RAG\n---\nBody",
            ),
            parse_document(
                "knowledge-systems/source.md",
                (
                    "---\n"
                    "title: Source\n"
                    'links: ["hybrid-memory-in-practice", "knowledge-graph-rag-architecture"]\n'
                    "---\n"
                    "See also [[hybrid-memory-in-practice]] (extends).\n"
                ).encode("utf-8"),
            ),
        ]
        resolver = LinkResolver.from_documents(docs)
        edges = extract_document_graph_edges(docs[2], link_resolver=resolver)

        assert len(edges) == 2
        assert {edge.target_id for edge in edges} == {docs[0].id, docs[1].id}
        hybrid_edge = next(edge for edge in edges if edge.target_id == docs[0].id)
        assert hybrid_edge.relationship_type == "extends"


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

    def test_oversized_paragraph_with_no_boundary_is_hard_split(self):
        # A paragraph larger than max_chars with no whitespace boundary is
        # hard-split so each chunk respects the bound (the old behavior let a
        # single ~200KB chunk through, ~99.5% invisible to the embedder).
        p = "x" * 2000
        chunks = chunk_truth(p, max_chars=1000)
        assert len(chunks) >= 2
        assert all(len(c) <= 1000 for c in chunks)
        assert "".join(chunks) == p  # no content lost

    def test_oversized_paragraph_splits_on_sentence_boundary(self):
        # Two sentences in one paragraph that together exceed max_chars but each
        # fit should split at the sentence boundary, not mid-word.
        s1 = "A" * 600 + "."
        s2 = "B" * 600 + "."
        chunks = chunk_truth(f"{s1} {s2}", max_chars=1000)
        assert chunks == [s1, s2]

    def test_no_chunk_exceeds_max_chars(self):
        # Invariant: whatever the input shape, every chunk respects the bound.
        body = "\n\n".join(["word " * 300, "y" * 5000, "short tail."])
        chunks = chunk_truth(body, max_chars=1000)
        assert chunks
        assert all(len(c) <= 1000 for c in chunks)


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
