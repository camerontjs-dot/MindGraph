import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mindgraph.exceptions import ParseError
from mindgraph.models import GraphEdge, ParsedDocument

LINK_PATTERN = re.compile(r"\[\[([^\[\]]+?)\]\](?:\s*\(([^)]+)\))?")

# `---` on its own line followed (possibly across blank lines) by a `## Timeline` heading.
TIMELINE_SPLIT_PATTERN = re.compile(
    r"^[ \t]*---[ \t]*\n(?:[ \t]*\n)*[ \t]*##[ \t]+Timeline[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)

FRONTMATTER_PATTERN = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def compute_doc_id(relative_path: str) -> str:
    """Stable short hash of a path string. Same input → same ID."""
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]


def compute_scoped_doc_id(index_id: str, namespace: str, source_path: str) -> str:
    """Stable short hash for a document inside a named index namespace."""
    scoped = f"{index_id}\0{namespace}\0{source_path}"
    return compute_doc_id(scoped)


def compute_content_hash(body_bytes: bytes) -> str:
    return hashlib.sha256(body_bytes).hexdigest()


def _normalize_link_target(target: str) -> str:
    target = target.strip()
    if not target.endswith(".md"):
        target = target + ".md"
    return target


def _normalize_lookup_key(value: str) -> str:
    return value.strip().casefold()


@dataclass
class LinkResolver:
    """Resolve wikilink labels against documents in one ingest scope."""

    paths: set[str] = field(default_factory=set)
    ids_by_path: dict[str, str] = field(default_factory=dict)
    stems: dict[str, set[str]] = field(default_factory=dict)
    titles: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def from_documents(cls, documents: Iterable[ParsedDocument]) -> "LinkResolver":
        resolver = cls()
        for doc in documents:
            resolver.add_document(doc)
        return resolver

    def add_document(self, doc: ParsedDocument) -> None:
        self.paths.add(doc.path)
        self.ids_by_path[doc.path] = doc.id
        self.stems.setdefault(_normalize_lookup_key(Path(doc.path).stem), set()).add(
            doc.path
        )
        self.titles.setdefault(_normalize_lookup_key(doc.title), set()).add(doc.path)

    def doc_id_for_path(self, path: str) -> str | None:
        return self.ids_by_path.get(path)

    def resolve(self, target: str, source_path: str | None = None) -> str | None:
        normalized = _normalize_link_target(target)
        if normalized in self.paths:
            return normalized

        if source_path is not None:
            sibling = str(Path(source_path).parent / normalized)
            if sibling in self.paths:
                return sibling

        if "/" not in normalized:
            stem_key = _normalize_lookup_key(Path(normalized).stem)
            stem_matches = self.stems.get(stem_key, set())
            if len(stem_matches) == 1:
                return next(iter(stem_matches))

        raw_key = _normalize_lookup_key(target)
        title_matches = self.titles.get(raw_key, set())
        if len(title_matches) == 1:
            return next(iter(title_matches))

        return None


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter if present. Returns (metadata, body)."""
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return {}, text
    raw_yaml, body = match.groups()
    try:
        metadata = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError as e:
        raise ParseError(f"Malformed YAML frontmatter: {e}") from e
    if not isinstance(metadata, dict):
        raise ParseError(
            f"Frontmatter must be a YAML mapping, got {type(metadata).__name__}"
        )
    return metadata, body


def split_page_model(body: str) -> tuple[str, str | None]:
    """Split body into (truth, timeline) on `---` followed by `## Timeline`.

    Plain `---` horizontal rules elsewhere in the body do not trigger the split.
    Returns (body, None) if no Timeline section is present.
    """
    match = TIMELINE_SPLIT_PATTERN.search(body)
    if not match:
        return body.strip(), None
    truth = body[: match.start()].strip()
    timeline = body[match.end():].strip()
    return truth, (timeline or None)


def extract_graph_edges(
    text: str,
    source_id: str,
    *,
    link_resolver: Callable[[str, str | None], str | None] | LinkResolver | None = None,
    source_path: str | None = None,
) -> list[GraphEdge]:
    """Find `[[link]]` and `[[link]] (relationship)` patterns and return edges."""
    edges: list[GraphEdge] = []
    for match in LINK_PATTERN.finditer(text):
        target_raw, relationship = match.groups()
        resolved_path = None
        resolved_id = None
        if isinstance(link_resolver, LinkResolver):
            resolved_path = link_resolver.resolve(target_raw, source_path)
            if resolved_path is not None:
                resolved_id = link_resolver.doc_id_for_path(resolved_path)
        elif link_resolver is not None:
            resolved_path = link_resolver(target_raw, source_path)
        target_id = resolved_id or compute_doc_id(
            resolved_path or _normalize_link_target(target_raw)
        )
        edges.append(
            GraphEdge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship.strip() if relationship else None,
            )
        )
    return edges


# Sentence boundary: whitespace that follows `.`, `!`, or `?`.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Last-resort fixed-width cut for a unit with no usable boundary."""
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _split_oversized(para: str, max_chars: int) -> list[str]:
    """Break a paragraph longer than max_chars into pieces each <= max_chars.

    Prefers line boundaries, then sentence boundaries, and finally a hard
    character cut for a single unit that still exceeds max_chars (e.g. a giant
    blob with no whitespace, like a raw arXiv capture). Surviving units are
    greedily re-packed up to max_chars.
    """
    if len(para) <= max_chars:
        return [para]

    units: list[str] = []
    for line in para.split("\n"):
        if len(line) <= max_chars:
            units.append(line)
            continue
        for sentence in _SENTENCE_BOUNDARY.split(line):
            if len(sentence) <= max_chars:
                units.append(sentence)
            else:
                units.extend(_hard_split(sentence, max_chars))

    pieces: list[str] = []
    current = ""
    for unit in units:
        if not unit:
            continue
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current = current + " " + unit
        else:
            pieces.append(current)
            current = unit
    if current:
        pieces.append(current)
    return pieces


def chunk_truth(truth_text: str, max_chars: int = 1000) -> list[str]:
    """Pack paragraphs into chunks bounded by max_chars.

    Paragraphs are the natural chunk unit, but a single paragraph longer than
    max_chars is first hard-split on sentence/line boundaries so no chunk can
    exceed the bound. This protects semantic recall (MiniLM truncates at ~256
    tokens, so an unsplit megachunk is mostly invisible to the embedder) and
    avoids dumping an oversized chunk into a `--json`/MCP response.
    """
    if not truth_text.strip():
        return []
    paragraphs: list[str] = []
    for raw in re.split(r"\n\s*\n", truth_text):
        para = raw.strip()
        if para:
            paragraphs.extend(_split_oversized(para, max_chars))
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if not current:
            current = para
        elif len(current) + 2 + len(para) <= max_chars:
            current = current + "\n\n" + para
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


def parse_document(relative_path: str, body_bytes: bytes) -> ParsedDocument:
    """Parse a Markdown file's bytes into a validated ParsedDocument."""
    try:
        text = body_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ParseError(f"File is not valid UTF-8: {e}", path=relative_path) from e

    metadata, body = parse_frontmatter(text)
    truth, timeline = split_page_model(body)

    title = metadata.get("title") or Path(relative_path).stem

    return ParsedDocument(
        id=compute_doc_id(relative_path),
        title=str(title),
        path=relative_path,
        content_hash=compute_content_hash(body_bytes),
        metadata=metadata,
        truth_text=truth,
        timeline_text=timeline,
    )
