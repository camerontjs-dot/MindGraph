"""Embedding model registry and text formatting for ingest/query."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

EmbedTemplate = Literal["none", "mainframe"]


@dataclass(frozen=True)
class EmbedderSpec:
    key: str
    model_id: str
    dimensions: int
    query_prefix: str = ""
    passage_prefix: str = ""


_REGISTRY: dict[str, EmbedderSpec] = {
    "minilm": EmbedderSpec("minilm", "all-MiniLM-L6-v2", 384),
    "all-minilm-l6-v2": EmbedderSpec(
        "all-minilm-l6-v2", "all-MiniLM-L6-v2", 384
    ),
    "bge-small": EmbedderSpec(
        "bge-small",
        "BAAI/bge-small-en-v1.5",
        384,
        query_prefix="query: ",
        passage_prefix="",
    ),
    "bge-small-en-v1.5": EmbedderSpec(
        "bge-small-en-v1.5",
        "BAAI/bge-small-en-v1.5",
        384,
        query_prefix="query: ",
        passage_prefix="",
    ),
    "e5-small": EmbedderSpec(
        "e5-small",
        "intfloat/e5-small-v2",
        384,
        query_prefix="query: ",
        passage_prefix="passage: ",
    ),
    "e5-small-v2": EmbedderSpec(
        "e5-small-v2",
        "intfloat/e5-small-v2",
        384,
        query_prefix="query: ",
        passage_prefix="passage: ",
    ),
}

DEFAULT_EMBEDDER_KEY = "minilm"


def resolve_embedder(name: str | None = None) -> EmbedderSpec:
    """Resolve an embedder key from CLI, env, or default."""
    raw = (name or os.environ.get("MINDGRAPH_EMBEDDER") or DEFAULT_EMBEDDER_KEY).strip()
    key = raw.lower()
    if key not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"Unknown embedder {raw!r}; known keys: {known}")
    return _REGISTRY[key]


def resolve_embed_template(name: str | None = None) -> EmbedTemplate:
    raw = (
        name or os.environ.get("MINDGRAPH_EMBED_TEMPLATE") or "none"
    ).strip().lower()
    if raw in ("none", ""):
        return "none"
    if raw == "mainframe":
        return "mainframe"
    raise ValueError(
        f"Unknown embed template {raw!r}; known: none, mainframe"
    )


def load_sentence_embedder(spec: EmbedderSpec):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(spec.model_id)


def format_query_text(spec: EmbedderSpec, text: str, *, template: EmbedTemplate) -> str:
    body = text.strip()
    if template == "mainframe":
        body = f"[intent=query] {body}"
    if spec.query_prefix:
        body = f"{spec.query_prefix}{body}"
    return body


def format_passage_text(
    spec: EmbedderSpec,
    text: str,
    *,
    template: EmbedTemplate,
    title: str | None = None,
    domain: str | None = None,
    doc_type: str | None = None,
) -> str:
    body = text.strip()
    if template == "mainframe":
        parts: list[str] = []
        if domain:
            parts.append(f"[domain={domain}]")
        if doc_type:
            parts.append(f"[type={doc_type}]")
        if title:
            parts.append(title)
        prefix = " ".join(parts)
        body = f"{prefix} — {body}" if prefix else body
    if spec.passage_prefix:
        body = f"{spec.passage_prefix}{body}"
    return body