#!/usr/bin/env python3
"""Stage 2A replication harness with enforced baseline headroom.

Research apparatus only. Candidate scoring is refused unless a frozen baseline
report for the same fixture/runtime satisfies the fixture's preregistered
headroom gate.
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
    if data.get("schema_version") != "mindgraph-stage2-section-boundary-replication/v1":
        raise ValueError("unexpected fixture schema_version")
    if data.get("panel_type") not in {"public_mechanism", "private_replication"}:
        raise ValueError("panel_type must be public_mechanism or private_replication")
    documents = data.get("documents")
    cases = data.get("cases")
    gate = data.get("headroom_gate")
    if not isinstance(documents, list) or not documents:
        raise ValueError("documents must be a non-empty list")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    if not isinstance(gate, dict):
        raise ValueError("headroom_gate is required")
    ids = [d.get("id") for d in documents]
    if any(not x for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("document ids must be unique non-empty strings")
    section_count = 0
    control_count = 0
    for case in cases:
        if case.get("kind") == "section_sensitive":
            section_count += 1
        elif case.get("kind") == "control":
            control_count += 1
        else:
            raise ValueError(f"invalid case kind {case.get('kind')!r}")
        if case.get("expected_doc") not in ids:
            raise ValueError(f"unknown expected_doc for {case.get('id')!r}")
        for doc_id in case.get("forbidden_docs", []):
            if doc_id not in ids:
                raise ValueError(f"unknown forbidden doc {doc_id!r}")
    if section_count < int(gate.get("min_section_sensitive", 0)):
        raise ValueError("fixture does not meet declared section-sensitive count")
    if control_count < int(gate.get("min_controls", 0)):
        raise ValueError("fixture does not meet declared control count")
    return data, sha256_bytes(raw)


def split_heading_sections(text: str) -> list[str]:
    matches = list(ATX_HEADING.finditer(text))
    if not matches:
        return [text]
    sections: list[str] = []
    prefix = text[: matches[0].start()].strip()
    if prefix:
        sections.append(prefix)
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.start():end].strip()
        if section:
            sections.append(section)
    return sections or [text]


def heading_labels(text: str) -> list[str]:
    return [m.group(1).strip() for m in ATX_HEADING.finditer(text)]


def cross_heading_chunk_count(chunks: list[str], labels: list[str]) -> int:
    lowered_labels = [label.casefold() for label in labels if label]
    return sum(
        1
        for chunk in chunks
        if sum(1 for label in lowered_labels if label in chunk.casefold()) >= 2
    )


def build_arm(fixture: dict[str, Any], *, arm: str, model_key: str) -> dict[str, Any]:
    max_chars = int(fixture.get("max_chars", 1000))
    spec = resolve_embedder(model_key)
    rows: list[dict[str, Any]] = []
    cross_heading = 0
    for doc in fixture["documents"]:
        truth = str(doc["truth_text"])
        labels = heading_labels(truth)
        if arm == "baseline":
            chunks = chunk_truth(truth, max_chars=max_chars)
        elif arm == "heading_boundary":
            chunks = []
            for section in split_heading_sections(truth):
                chunks.extend(chunk_truth(section, max_chars=max_chars))
        else:
            raise ValueError(f"unknown arm {arm!r}")
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
        "rows": rows,
        "row_count": len(rows),
        "cross_heading_chunk_count": cross_heading,
        "representation_sha256": canonical_hash(rows),
    }


def encode(model, texts: list[str]) -> np.ndarray:
    try:
        vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    except TypeError:
        vectors = model.encode(texts, convert_to_numpy=True)
    arr = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    def one(selected: list[dict[str, Any]]) -> dict[str, Any]:
        if not selected:
            return {"count": 0, "recall_at_5": None, "mrr": None, "hard_negative_top3_rate": None}
        hn = [c for c in selected if c["forbidden_docs"]]
        return {
            "count": len(selected),
            "recall_at_5": sum(c["expected_rank"] <= 5 for c in selected) / len(selected),
            "mrr": sum(1.0 / c["expected_rank"] for c in selected) / len(selected),
            "hard_negative_top3_rate": (
                sum(c["forbidden_in_top3"] for c in hn) / len(hn) if hn else 0.0
            ),
        }
    return {
        "all": one(cases),
        "section_sensitive": one([c for c in cases if c["kind"] == "section_sensitive"]),
        "control": one([c for c in cases if c["kind"] == "control"]),
    }


def rank_arm(fixture: dict[str, Any], arm: dict[str, Any], model, model_key: str) -> dict[str, Any]:
    spec = resolve_embedder(model_key)
    passage_vectors = encode(model, [row["embedding_text"] for row in arm["rows"]])
    query_vectors = encode(
        model,
        [format_query_text(spec, case["query"], template="mainframe") for case in fixture["cases"]],
    )
    out = []
    for case, qvec in zip(fixture["cases"], query_vectors):
        scores = passage_vectors @ qvec
        best: dict[str, float] = {}
        for row, score in zip(arm["rows"], scores):
            doc_id = row["doc_id"]
            best[doc_id] = max(best.get(doc_id, float("-inf")), float(score))
        ranked = sorted(best, key=lambda d: (-best[d], d))
        rank = {doc_id: i + 1 for i, doc_id in enumerate(ranked)}
        forbidden = list(case.get("forbidden_docs", []))
        out.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "expected_doc": case["expected_doc"],
                "expected_rank": rank[case["expected_doc"]],
                "top3_docs": ranked[:3],
                "forbidden_docs": forbidden,
                "forbidden_in_top3": any(d in ranked[:3] for d in forbidden),
            }
        )
    return {
        "arm": arm["arm"],
        "row_count": arm["row_count"],
        "cross_heading_chunk_count": arm["cross_heading_chunk_count"],
        "representation_sha256": arm["representation_sha256"],
        "cases": out,
        "metrics": summarize(out),
    }


def runtime_manifest(model_key: str, runtime_id: str, model) -> dict[str, Any]:
    spec = resolve_embedder(model_key)
    packages = {}
    for name in ("sentence-transformers", "torch", "numpy"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "declared_runtime_id": runtime_id,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "embedder_key": spec.key,
        "model_id": spec.model_id,
        "dimensions": spec.dimensions,
        "device": str(getattr(model, "device", "unknown")),
        "packages": packages,
    }


def baseline_headroom(fixture: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    gate = fixture["headroom_gate"]
    section = [c for c in result["cases"] if c["kind"] == "section_sensitive"]
    controls = [c for c in result["cases"] if c["kind"] == "control"]
    non_rank1_top5 = sum(2 <= c["expected_rank"] <= 5 for c in section)
    checks = {
        "recall_at_5_is_one": result["metrics"]["all"]["recall_at_5"] == 1.0,
        "enough_non_rank1_section_cases": non_rank1_top5 >= int(gate["min_non_rank1_top5"]),
        "controls_inside_declared_rank": all(
            c["expected_rank"] <= int(gate.get("max_control_rank", 5)) for c in controls
        ),
        "has_cross_heading_baseline_chunks": result["cross_heading_chunk_count"] > 0,
    }
    return {
        "checks": checks,
        "non_rank1_section_cases_inside_top5": non_rank1_top5,
        "satisfies": all(checks.values()),
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    b = baseline["metrics"]
    c = candidate["metrics"]
    base_cases = {x["id"]: x for x in baseline["cases"]}
    control_regressions = [
        case["id"]
        for case in candidate["cases"]
        if case["kind"] == "control"
        and base_cases[case["id"]]["expected_rank"] <= 5
        and case["expected_rank"] > 5
    ]
    checks = {
        "recall_at_5_non_decrease": c["all"]["recall_at_5"] >= b["all"]["recall_at_5"],
        "no_control_top5_regression": not control_regressions,
        "hard_negative_top3_non_increase": (
            c["all"]["hard_negative_top3_rate"] <= b["all"]["hard_negative_top3_rate"]
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


def write_report(path: Path | None, report: dict[str, Any]) -> None:
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if path:
        path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", type=Path, required=True)
    p.add_argument("--mode", choices=["validate", "baseline", "candidate"], required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--embedder", default="minilm")
    p.add_argument("--runtime-id", default="")
    p.add_argument("--baseline-report", type=Path)
    args = p.parse_args()

    fixture, fixture_hash = load_fixture(args.fixture)
    if args.mode == "validate":
        report = {
            "schema_version": "mindgraph-stage2-section-boundary-replication-validation/v1",
            "fixture_sha256": fixture_hash,
            "panel_type": fixture["panel_type"],
            "headroom_gate": fixture["headroom_gate"],
        }
        write_report(args.output, report)
        return

    if not args.runtime_id.strip():
        raise SystemExit("--runtime-id is required for model execution")

    model = load_sentence_embedder(resolve_embedder(args.embedder))
    runtime = runtime_manifest(args.embedder, args.runtime_id.strip(), model)

    if args.mode == "baseline":
        arm = build_arm(fixture, arm="baseline", model_key=args.embedder)
        result = rank_arm(fixture, arm, model, args.embedder)
        report = {
            "schema_version": "mindgraph-stage2-section-boundary-replication-result/v1",
            "fixture_sha256": fixture_hash,
            "panel_type": fixture["panel_type"],
            "runtime": runtime,
            "result": result,
            "headroom": baseline_headroom(fixture, result),
        }
        write_report(args.output, report)
        return

    if args.baseline_report is None:
        raise SystemExit("--baseline-report is required for candidate mode")
    baseline_report = json.loads(args.baseline_report.read_text(encoding="utf-8"))
    if baseline_report.get("fixture_sha256") != fixture_hash:
        raise SystemExit("baseline report fixture hash does not match")
    if baseline_report.get("runtime") != runtime:
        raise SystemExit("runtime manifest differs from frozen baseline report")
    if not baseline_report.get("headroom", {}).get("satisfies"):
        raise SystemExit("baseline headroom gate is not satisfied; candidate scoring refused")

    arm = build_arm(fixture, arm="heading_boundary", model_key=args.embedder)
    candidate = rank_arm(fixture, arm, model, args.embedder)
    baseline = baseline_report["result"]
    report = {
        "schema_version": "mindgraph-stage2-section-boundary-replication-result/v1",
        "fixture_sha256": fixture_hash,
        "panel_type": fixture["panel_type"],
        "runtime": runtime,
        "baseline_report_sha256": sha256_bytes(args.baseline_report.read_bytes()),
        "result": candidate,
        "comparison": compare(baseline, candidate),
    }
    write_report(args.output, report)


if __name__ == "__main__":
    main()
