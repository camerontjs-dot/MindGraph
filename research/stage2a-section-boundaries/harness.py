#!/usr/bin/env python3
"""Stage 2A representation experiment: current chunks vs heading-boundary chunks.

This is research apparatus only. It does not modify MindGraph ingestion or indexes.
The primary comparison is semantic-only document ranking under one fixed embedder.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
from pathlib import Path
from typing import Any

import numpy as np

from mindgraph.embedders import (
    format_passage_text,
    format_query_text,
    load_sentence_embedder,
    resolve_embedder,
)
from mindgraph.parser import chunk_truth

ATX_HEADING = re.compile(r"(?m)^[ \t]*#{1,6}[ \t]+(.+?)[ \t]*$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def load_fixture(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != "mindgraph-stage2-section-boundary/v1":
        raise ValueError("unexpected fixture schema_version")
    documents = data.get("documents")
    cases = data.get("cases")
    if not isinstance(documents, list) or not documents:
        raise ValueError("fixture.documents must be a non-empty list")
    if not isinstance(cases, list) or not cases:
        raise ValueError("fixture.cases must be a non-empty list")
    ids = [d.get("id") for d in documents]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        raise ValueError("document ids must be unique non-empty strings")
    for case in cases:
        if case.get("expected_doc") not in ids:
            raise ValueError(f"unknown expected_doc for case {case.get('id')!r}")
        for doc_id in case.get("forbidden_docs", []):
            if doc_id not in ids:
                raise ValueError(f"unknown forbidden doc {doc_id!r}")
        if case.get("kind") not in {"section_sensitive", "control"}:
            raise ValueError(f"invalid case kind for {case.get('id')!r}")
    return data, sha256_bytes(raw)


def split_heading_sections(text: str) -> list[str]:
    """Split raw Truth text at ATX headings, keeping each heading with its section."""
    matches = list(ATX_HEADING.finditer(text))
    if not matches:
        return [text]
    sections: list[str] = []
    prefix = text[: matches[0].start()].strip()
    if prefix:
        sections.append(prefix)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.start() : end].strip()
        if section:
            sections.append(section)
    return sections or [text]


def current_chunks(text: str, max_chars: int) -> list[str]:
    return chunk_truth(text, max_chars=max_chars)


def heading_boundary_chunks(text: str, max_chars: int) -> list[str]:
    out: list[str] = []
    for section in split_heading_sections(text):
        out.extend(chunk_truth(section, max_chars=max_chars))
    return out


def heading_labels(text: str) -> list[str]:
    return [m.group(1).strip() for m in ATX_HEADING.finditer(text)]


def cross_heading_chunk_count(chunks: list[str], labels: list[str]) -> int:
    count = 0
    lowered_labels = [label.casefold() for label in labels if label]
    for chunk in chunks:
        lowered = chunk.casefold()
        matches = sum(1 for label in lowered_labels if label in lowered)
        if matches >= 2:
            count += 1
    return count


def build_arm(
    fixture: dict[str, Any],
    *,
    arm: str,
    model_key: str,
) -> dict[str, Any]:
    max_chars = int(fixture.get("max_chars", 1000))
    spec = resolve_embedder(model_key)
    rows: list[dict[str, Any]] = []
    cross_heading = 0

    for doc in fixture["documents"]:
        truth = str(doc["truth_text"])
        labels = heading_labels(truth)
        chunks = (
            current_chunks(truth, max_chars)
            if arm == "baseline"
            else heading_boundary_chunks(truth, max_chars)
        )
        cross_heading += cross_heading_chunk_count(chunks, labels)
        for index, chunk in enumerate(chunks):
            rows.append(
                {
                    "doc_id": doc["id"],
                    "chunk_index": index,
                    "source_text": chunk,
                    "embedding_text": format_passage_text(
                        spec,
                        chunk,
                        template="mainframe",
                        title=doc.get("title"),
                        domain=doc.get("domain"),
                        doc_type=doc.get("doc_type"),
                    ),
                }
            )

    return {
        "arm": arm,
        "max_chars": max_chars,
        "rows": rows,
        "row_count": len(rows),
        "cross_heading_chunk_count": cross_heading,
        "representation_sha256": canonical_hash(
            [
                {
                    "doc_id": row["doc_id"],
                    "chunk_index": row["chunk_index"],
                    "source_text": row["source_text"],
                    "embedding_text": row["embedding_text"],
                }
                for row in rows
            ]
        ),
    }


def encode(model, texts: list[str]) -> np.ndarray:
    try:
        vectors = model.encode(
            texts, convert_to_numpy=True, show_progress_bar=False
        )
    except TypeError:
        vectors = model.encode(texts, convert_to_numpy=True)
    arr = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def rank_arm(
    fixture: dict[str, Any],
    arm: dict[str, Any],
    model,
    model_key: str,
) -> dict[str, Any]:
    spec = resolve_embedder(model_key)
    passage_vectors = encode(
        model, [row["embedding_text"] for row in arm["rows"]]
    )
    query_texts = [
        format_query_text(spec, case["query"], template="mainframe")
        for case in fixture["cases"]
    ]
    query_vectors = encode(model, query_texts)

    cases_out: list[dict[str, Any]] = []
    for case, qvec in zip(fixture["cases"], query_vectors):
        scores = passage_vectors @ qvec
        best_by_doc: dict[str, tuple[float, int]] = {}
        for row_index, (row, score) in enumerate(zip(arm["rows"], scores)):
            doc_id = row["doc_id"]
            current = best_by_doc.get(doc_id)
            candidate = (float(score), row_index)
            if current is None or candidate[0] > current[0]:
                best_by_doc[doc_id] = candidate

        ranked = sorted(best_by_doc, key=lambda d: (-best_by_doc[d][0], d))
        ranks = {doc_id: index + 1 for index, doc_id in enumerate(ranked)}
        expected = case["expected_doc"]
        forbidden = list(case.get("forbidden_docs", []))
        cases_out.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "expected_doc": expected,
                "expected_rank": ranks[expected],
                "top5": expected in ranked[:5],
                "top3_docs": ranked[:3],
                "forbidden_in_top3": any(d in ranked[:3] for d in forbidden),
                "forbidden_docs": forbidden,
            }
        )

    return {
        "arm": arm["arm"],
        "row_count": arm["row_count"],
        "cross_heading_chunk_count": arm["cross_heading_chunk_count"],
        "representation_sha256": arm["representation_sha256"],
        "cases": cases_out,
        "metrics": metrics(cases_out),
    }


def metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
        if not selected:
            return {
                "count": 0,
                "recall_at_5": None,
                "mrr": None,
                "hard_negative_top3_rate": None,
            }
        with_forbidden = [c for c in selected if c["forbidden_docs"]]
        return {
            "count": len(selected),
            "recall_at_5": sum(1 for c in selected if c["top5"]) / len(selected),
            "mrr": sum(1.0 / c["expected_rank"] for c in selected) / len(selected),
            "hard_negative_top3_rate": (
                sum(1 for c in with_forbidden if c["forbidden_in_top3"])
                / len(with_forbidden)
                if with_forbidden
                else 0.0
            ),
        }

    return {
        "all": summarize(cases),
        "section_sensitive": summarize(
            [c for c in cases if c["kind"] == "section_sensitive"]
        ),
        "control": summarize([c for c in cases if c["kind"] == "control"]),
    }


def decision_rule(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    b = baseline["metrics"]
    c = candidate["metrics"]
    control_regressions = [
        case["id"]
        for case in candidate["cases"]
        for base_case in baseline["cases"]
        if case["id"] == base_case["id"]
        and case["kind"] == "control"
        and base_case["expected_rank"] <= 5
        and case["expected_rank"] > 5
    ]
    checks = {
        "recall_at_5_non_decrease": c["all"]["recall_at_5"] >= b["all"]["recall_at_5"],
        "no_control_top5_regression": not control_regressions,
        "hard_negative_top3_non_increase": (
            c["all"]["hard_negative_top3_rate"]
            <= b["all"]["hard_negative_top3_rate"]
        ),
        "section_sensitive_mrr_strictly_higher": (
            c["section_sensitive"]["mrr"] > b["section_sensitive"]["mrr"]
        ),
    }
    return {
        "checks": checks,
        "control_regressions": control_regressions,
        "satisfies_panel_rule": all(checks.values()),
    }


def runtime_manifest(model_key: str) -> dict[str, Any]:
    spec = resolve_embedder(model_key)
    packages = {}
    for name in ("sentence-transformers", "torch", "numpy"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "embedder_key": spec.key,
        "model_id": spec.model_id,
        "dimensions": spec.dimensions,
        "packages": packages,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--embedder", default="minilm")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    fixture, fixture_hash = load_fixture(args.fixture)
    baseline_arm = build_arm(fixture, arm="baseline", model_key=args.embedder)
    candidate_arm = build_arm(
        fixture, arm="heading_boundary", model_key=args.embedder
    )

    report: dict[str, Any] = {
        "schema_version": "mindgraph-stage2-section-boundary-result/v1",
        "fixture_sha256": fixture_hash,
        "runtime": runtime_manifest(args.embedder),
        "baseline_representation": {
            k: baseline_arm[k]
            for k in ("row_count", "cross_heading_chunk_count", "representation_sha256")
        },
        "candidate_representation": {
            k: candidate_arm[k]
            for k in ("row_count", "cross_heading_chunk_count", "representation_sha256")
        },
    }

    if not args.validate_only:
        model = load_sentence_embedder(resolve_embedder(args.embedder))
        baseline = rank_arm(fixture, baseline_arm, model, args.embedder)
        candidate = rank_arm(fixture, candidate_arm, model, args.embedder)
        report["baseline"] = baseline
        report["candidate"] = candidate
        report["decision_rule"] = decision_rule(baseline, candidate)

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
