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
CANONICAL_FILENAME_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}__")
WIKILINK_WRAPPER_PATTERN = re.compile(r"^\[\[(.+?)\]\]$")


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


def canonical_trailing_slug(stem: str) -> str | None:
    """Return the trailing slug from a canonical MainFrame filename stem.

    Canonical shape: ``YYYY-MM-DD__domain__type__slug`` (see ADR-033).
    """
    if not CANONICAL_FILENAME_PREFIX.match(stem):
        return None
    parts = stem.split("__")
    if len(parts) < 4:
        return None
    slug = parts[-1].strip()
    return slug or None


@dataclass
class LinkResolver:
    """Resolve wikilink labels against documents in one ingest scope."""

    paths: set[str] = field(default_factory=set)
    ids_by_path: dict[str, str] = field(default_factory=dict)
    stems: dict[str, set[str]] = field(default_factory=dict)
    titles: dict[str, set[str]] = field(default_factory=dict)
    slug_suffixes: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def from_documents(cls, documents: Iterable[ParsedDocument]) -> "LinkResolver":
        resolver = cls()
        for doc in documents:
            resolver.add_document(doc)
        return resolver

    def add_document(self, doc: ParsedDocument) -> None:
        self.paths.add(doc.path)
        self.ids_by_path[doc.path] = doc.id
        stem = Path(doc.path).stem
        self.stems.setdefault(_normalize_lookup_key(stem), set()).add(doc.path)
        self.titles.setdefault(_normalize_lookup_key(doc.title), set()).add(doc.path)
        trailing_slug = canonical_trailing_slug(stem)
        if trailing_slug is not None:
            self.slug_suffixes.setdefault(
                _normalize_lookup_key(trailing_slug), set()
            ).add(doc.path)

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

        if "/" not in normalized:
            suffix_key = _normalize_lookup_key(Path(normalized).stem)
            suffix_matches = self.slug_suffixes.get(suffix_key, set())
            if len(suffix_matches) == 1:
                return next(iter(suffix_matches))

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


def normalize_link_label(raw: str) -> str:
    """Normalize a link label from frontmatter or wikilink syntax."""
    target = raw.strip()
    if not target:
        return ""
    wrapper = WIKILINK_WRAPPER_PATTERN.match(target)
    if wrapper:
        target = wrapper.group(1).strip()
    if "|" in target:
        target = target.split("|", 1)[0].strip()
    return target


def extract_metadata_link_targets(metadata: dict) -> list[str]:
    """Return deduplicated link targets from frontmatter ``links:``."""
    raw_links = metadata.get("links")
    if raw_links is None:
        return []
    if isinstance(raw_links, str):
        candidates = [raw_links]
    elif isinstance(raw_links, list):
        candidates = [str(item) for item in raw_links if item is not None]
    else:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        target = normalize_link_label(raw)
        if not target:
            continue
        key = _normalize_lookup_key(target)
        if key in seen:
            continue
        seen.add(key)
        out.append(target)
    return out


def _edge_for_target(
    target_raw: str,
    source_id: str,
    *,
    link_resolver: Callable[[str, str | None], str | None] | LinkResolver | None,
    source_path: str | None,
    relationship_type: str | None = None,
) -> GraphEdge:
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
    return GraphEdge(
        source_id=source_id,
        target_id=target_id,
        relationship_type=relationship_type,
    )


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
        edges.append(
            _edge_for_target(
                target_raw,
                source_id,
                link_resolver=link_resolver,
                source_path=source_path,
                relationship_type=relationship.strip() if relationship else None,
            )
        )
    return edges


def extract_document_graph_edges(
    parsed: ParsedDocument,
    *,
    link_resolver: Callable[[str, str | None], str | None] | LinkResolver | None = None,
) -> list[GraphEdge]:
    """Collect graph edges from frontmatter ``links:`` and body wikilinks."""
    by_target: dict[str, GraphEdge] = {}

    for target in extract_metadata_link_targets(parsed.metadata):
        edge = _edge_for_target(
            target,
            parsed.id,
            link_resolver=link_resolver,
            source_path=parsed.path,
        )
        by_target.setdefault(edge.target_id, edge)

    for edge in extract_graph_edges(
        parsed.truth_text,
        parsed.id,
        link_resolver=link_resolver,
        source_path=parsed.path,
    ):
        existing = by_target.get(edge.target_id)
        if existing is None:
            by_target[edge.target_id] = edge
        elif existing.relationship_type is None and edge.relationship_type is not None:
            by_target[edge.target_id] = edge

    return list(by_target.values())


# Sentence boundary: whitespace that follows `.`, `!`, or `?`.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

# ---------------------------------------------------------------------------
# Apparatus stripping (semantic lane only)
#
# Chunks feed the embedder; `documents_fts` is populated from the full
# `truth_text` (db.py). This keeps paths, commands, and filenames searchable
# in the lexical lane while preventing common document apparatus from being
# embedded as if it were an assertion.
# ---------------------------------------------------------------------------

_FENCED_CODE = re.compile(r"^[ \t]*(?:```|~~~).*?(?:^[ \t]*(?:```|~~~)[ \t]*$|\Z)", re.M | re.S)
_HEADING_MARKER = re.compile(r"^[ \t]*#{1,6}[ \t]+")
_BLOCKQUOTE_LINE = re.compile(r"^[ \t]*>")
_CALLOUT_LABEL = r"(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|DANGER|ERROR|INFO|SUCCESS|ATTENTION)"
_CALLOUT_START = re.compile(
    rf"^[ \t]*>[ \t]*(?:\[!{_CALLOUT_LABEL}\]|\*\*{_CALLOUT_LABEL}(?:[.:!])?\*\*)(?=[ \t]|$)",
    re.IGNORECASE,
)
_BOLD_KEY = re.compile(r"\*\*[^*\n]{1,40}:\*\*")
_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_WIKILINK = re.compile(r"\[\[[^\]]*\]\]")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_URLISH = re.compile(r"\S*(?:https?://|[\w.-]+/[\w./-]+|\.(?:md|py|json|jsonl|ya?ml|sqlite|sh|plist|html))\S*")
_LIST_MARKER = re.compile(r"^[ \t]*(?:[-*+]|\d+\.)[ \t]+")

_MIN_PROSE_WORDS = 5


def _residual_prose_words(line: str) -> int:
    """Words left once links, paths, inline code, and bold keys are removed.

    A reference line ("- [Title](path/to/note.md)") drops to nearly nothing;
    a sentence that merely cites a path keeps its argument.
    """
    stripped = _MD_LINK.sub(" ", line)
    stripped = _WIKILINK.sub(" ", stripped)
    stripped = _INLINE_CODE.sub(" ", stripped)
    stripped = _URLISH.sub(" ", stripped)
    stripped = _BOLD_KEY.sub(" ", stripped)
    stripped = re.sub(r"[^\w\s]", " ", stripped)
    return len([w for w in stripped.split() if any(c.isalpha() for c in w)])


def _is_apparatus_line(line: str) -> bool:
    """True when the line is structure or reference rather than an assertion."""
    s = line.strip()
    if not s:
        return False
    if s.startswith("|") or re.fullmatch(r"[|\s:-]+", s):
        return True                                   # table rows and rules
    if len(_BOLD_KEY.findall(s)) >= 2:
        return True                                   # metadata-like rows
    has_reference = bool(
        _MD_LINK.search(s) or _WIKILINK.search(s) or _URLISH.search(s) or _INLINE_CODE.search(s)
    )
    if has_reference and _residual_prose_words(s) < _MIN_PROSE_WORDS:
        return True                                   # link lists and command dumps
    if _LIST_MARKER.match(s) and _residual_prose_words(s) < 3:
        return True                                   # bare enumerations
    return False


def _is_callout_start(line: str) -> bool:
    """Return True only for an explicit Markdown alert/callout marker."""
    return bool(_CALLOUT_START.match(line))


def strip_apparatus(text: str) -> str:
    """Remove non-assertional markup so the embedder sees prose.

    Headings keep their words and lose their markers: `## Retrieval floor` is a
    topical anchor, `##` is not. Fenced code, tables, recognized Markdown
    alert/callout blocks, metadata rows, and reference lists are dropped
    outright. Ordinary blockquotes are retained because a quoted passage can
    be substantive evidence.

    Fail-safe: a document that is entirely apparatus (an index page, a pure
    table) returns unchanged rather than becoming unindexable. Losing the
    semantic lane for a document is worse than embedding its structure.
    """
    if not text.strip():
        return text

    without_code = _FENCED_CODE.sub("\n", text)
    kept: list[str] = []
    lines = without_code.splitlines()
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if _is_callout_start(line):
            # Drop the whole contiguous blockquote only after an explicit
            # alert marker. A later ordinary blockquote is not swallowed.
            line_index += 1
            while line_index < len(lines) and _BLOCKQUOTE_LINE.match(lines[line_index]):
                line_index += 1
            continue
        if _is_apparatus_line(line):
            line_index += 1
            continue
        kept.append(_HEADING_MARKER.sub("", line))
        line_index += 1

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned if cleaned else text



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

    Apparatus is stripped first (see `strip_apparatus`): chunks feed the
    embedder, while the lexical lane retains the original Truth body.
    """
    if not truth_text.strip():
        return []
    truth_text = strip_apparatus(truth_text)
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
