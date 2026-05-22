import hashlib
import re
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


def compute_content_hash(body_bytes: bytes) -> str:
    return hashlib.sha256(body_bytes).hexdigest()


def _normalize_link_target(target: str) -> str:
    target = target.strip()
    if not target.endswith(".md"):
        target = target + ".md"
    return target


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


def extract_graph_edges(text: str, source_id: str) -> list[GraphEdge]:
    """Find `[[link]]` and `[[link]] (relationship)` patterns and return edges."""
    edges: list[GraphEdge] = []
    for match in LINK_PATTERN.finditer(text):
        target_raw, relationship = match.groups()
        target_id = compute_doc_id(_normalize_link_target(target_raw))
        edges.append(
            GraphEdge(
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship.strip() if relationship else None,
            )
        )
    return edges


def chunk_truth(truth_text: str, max_chars: int = 1000) -> list[str]:
    """Pack paragraphs into chunks bounded by max_chars. Paragraphs are kept whole."""
    if not truth_text.strip():
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", truth_text) if p.strip()]
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
