#!/usr/bin/env python3
"""Read-only MainFrame lexical/hybrid/graph retrieval ablation.

The experiment protocol is registered in MindGraph issue #54.
Private corpus bytes, query labels and raw rankings remain local.
"""
import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from urllib.parse import quote

import yaml

PRODUCT_SHA = "8df9ae7fccdb742558950ef9bdedb96cc74df6d0"
PRODUCT_TREE = "44111ff75c53a1862001b9d041944e81a2d1430a"
SCOPES = ("knowledge", "projects")
ARMS = ("A10", "B10", "B11", "C10plus1")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for buf in iter(lambda: f.read(1 << 20), b""):
            h.update(buf)
    return h.hexdigest()


def write_json(path, obj):
    path = Path(path)
    if path.exists():
        raise RuntimeError("NO_OVERWRITE: " + str(path))
    path.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def git(args, repo):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def checked_input(path):
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file():
        raise ValueError("not a file: " + str(p))
    return p


def backup_database(src_path, dest_path):
    src_path = checked_input(src_path)
    if dest_path.exists():
        raise RuntimeError("NO_OVERWRITE: " + str(dest_path))
    uri = "file:" + quote(str(src_path), safe="/") + "?mode=ro"
    reader = sqlite3.connect(uri, uri=True, timeout=30.0)
    try:
        dst = sqlite3.connect(str(dest_path))
        try:
            reader.backup(dst)
        finally:
            dst.close()
    finally:
        reader.close()


def load_cases(scope, path):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise ValueError("expected YAML queries list")
    cases = []
    for idx, obj in enumerate(data["queries"], 1):
        query = obj.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("empty query at " + str(idx))
        expected = obj.get("expected_paths") or []
        if not isinstance(expected, list) or not all(isinstance(x, str) for x in expected):
            raise ValueError("invalid gold path list")
        gold = []
        for raw in expected:
            raw = raw.strip().replace("\\", "/")
            if scope == "knowledge" and not raw.startswith("10_knowledge/"):
                raw = "10_knowledge/" + raw
            if raw.startswith("/") or ".." in Path(raw).parts:
                raise ValueError("invalid gold relative path")
            if raw not in gold:
                gold.append(raw)
        cases.append({
            "private_id": obj.get("id", f"{scope}-{idx:03d}"),
            "public_id": ("K" if scope == "knowledge" else "P") + f"{idx:02d}",
            "query": query,
            "class": obj.get("class", "positive" if gold else "negative"),
            "gold": gold,
            "negative": not bool(gold),
        })
    return cases


def prepare(args):
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "freeze.json"
    if manifest_path.exists():
        raise RuntimeError("existing freeze; no overwrite")
    repo = Path(args.repo).resolve(strict=True)
    head = git(["rev-parse", PRODUCT_SHA], repo)
    tree = git(["rev-parse", PRODUCT_SHA + "^{tree}"], repo)
    if head != PRODUCT_SHA or tree != PRODUCT_TREE:
        raise RuntimeError("PRODUCT_IDENTITY_MISMATCH")
    archive_path = out / "product.tar"
    if archive_path.exists() or (out / "product").exists():
        raise RuntimeError("existing product archive; no overwrite")
    with archive_path.open("wb") as f:
        subprocess.run(["git", "-C", str(repo), "archive", PRODUCT_SHA], stdout=f, check=True)
    product_root = out / "product"
    product_root.mkdir()
    with tarfile.open(archive_path, "r:") as tar:
        tar.extractall(product_root, filter="data")
    stores = {
        "knowledge": (checked_input(args.knowledge_db), checked_input(args.knowledge_gold)),
        "projects": (checked_input(args.projects_db), checked_input(args.projects_gold)),
    }
    manifest = {
        "protocol": "mindgraph-knowledge-projects-ablation-rc0",
        "issue": "https://github.com/camerontjs-dot/MindGraph/issues/54",
        "product": {"commit": PRODUCT_SHA, "tree": PRODUCT_TREE, "archive_sha256": digest(archive_path)},
        "harness_sha256": digest(__file__),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "arms": list(ARMS),
        "top_k": {"lexical_pool": 20, "semantic_pool": 20, "base": 10, "control": 11,
                  "graph_seed_k": 3, "graph_depth": 1, "graph_admit_max": 1},
        "embedder": {"key": "minilm", "query_template": "mainframe", "offline": True},
        "scopes": {},
        "claims": {
            "primary": "C10plus1 vs B11 source recall on frozen historical gold",
            "actor_completion": "NOT_RUN",
            "truth_verification": "NOT_CLAIMED",
            "live_index_freshness": "NOT_ESTABLISHED",
        },
    }
    all_cases = {}
    for scope in SCOPES:
        db_path, gold_path = stores[scope]
        snap = out / ("snapshot." + scope + ".sqlite")
        backup_database(db_path, snap)
        copied_gold = out / ("source." + scope + ".gold.yaml")
        shutil.copyfile(gold_path, copied_gold)
        cases = load_cases(scope, copied_gold)
        conn = sqlite3.connect("file:" + quote(str(snap), safe="/") + "?mode=ro", uri=True)
        try:
            docs = {row[0] for row in conn.execute("SELECT path FROM documents")}
            counts = {n: conn.execute("SELECT COUNT(*) FROM " + n).fetchone()[0]
                      for n in ("documents", "chunks", "edges")}
            index_meta = list(conn.execute("SELECT key,value FROM index_meta ORDER BY key"))
        finally:
            conn.close()
        for case in cases:
            case["index_present_gold"] = [g for g in case["gold"] if g in docs]
            case["index_missing_gold"] = [g for g in case["gold"] if g not in docs]
            case["all_gold_indexed"] = bool(case["gold"]) and not case["index_missing_gold"]
        all_cases[scope] = cases
        manifest["scopes"][scope] = {
            "source_db_name": db_path.name,
            "snapshot_sha256": digest(snap),
            "gold_sha256": digest(copied_gold),
            "counts": counts,
            "index_meta": index_meta,
            "cases": len(cases),
            "positives": sum(not c["negative"] for c in cases),
            "all_gold_indexed": sum(c["all_gold_indexed"] for c in cases),
            "gold_anchors_missing_from_index": sum(len(c["index_missing_gold"]) for c in cases),
        }
    write_json(out / "cases.frozen.json", all_cases)
    manifest["cases_sha256"] = digest(out / "cases.frozen.json")
    write_json(manifest_path, manifest)
    # Post-freeze products must not be edited. These are names/counts/hashes only.
    print(json.dumps({"freeze": "PRE_SCORE_FROZEN", "product": PRODUCT_SHA,
                      "harness": manifest["harness_sha256"],
                      "cases_sha256": manifest["cases_sha256"],
                      "scopes": manifest["scopes"]}, sort_keys=True))


def verify_freeze(out, manifest):
    if digest(__file__) != manifest["harness_sha256"]:
        raise RuntimeError("HARNESS_CHANGED_AFTER_FREEZE")
    if digest(out / "cases.frozen.json") != manifest["cases_sha256"]:
        raise RuntimeError("CASES_CHANGED_AFTER_FREEZE")
    if digest(out / "product.tar") != manifest["product"]["archive_sha256"]:
        raise RuntimeError("PRODUCT_ARCHIVE_CHANGED")
    for scope in SCOPES:
        info = manifest["scopes"][scope]
        if digest(out / ("snapshot." + scope + ".sqlite")) != info["snapshot_sha256"]:
            raise RuntimeError("SNAPSHOT_CHANGED: " + scope)
        if digest(out / ("source." + scope + ".gold.yaml")) != info["gold_sha256"]:
            raise RuntimeError("GOLD_CHANGED: " + scope)


def score(rows, gold):
    returned = [x.path for x in rows]
    ranks = {path: i + 1 for i, path in enumerate(returned)}
    hits = [p for p in gold if p in ranks]
    first_rank = min((ranks[p] for p in hits), default=None)
    return {
        "recall": len(hits) / len(gold) if gold else None,
        "all_found": bool(gold) and len(hits) == len(gold),
        "any_found": bool(hits),
        "first_rank": first_rank,
        "rr": (1 / first_rank) if first_rank is not None else 0.0,
        "citation_non_citable": sum(r.citation_class != "citable" for r in rows),
        "weak_fit": sum(bool(r.weak_fit) for r in rows),
        "warnings": sum(r.query_scope_warning is not None for r in rows),
        "chars": sum(len(r.chunk_text) for r in rows),
        "whitespace_tokens": sum(len(r.chunk_text.split()) for r in rows),
        "n_rows": len(rows),
        "n_distinct_docs": len(set(r.doc_id for r in rows)),
    }


def graph_admit_one(conn, query_mod, base):
    """Authored direct edge from top-three seed, deterministic and gold-blind."""
    seen = {r.doc_id for r in base}
    rejected_non_citable = 0
    for seed in base[:3]:
        expanded = query_mod.expand_results(
            conn, [seed], depth=1, expand_top_k=1000,
            query_scope_warning=seed.query_scope_warning
        )
        by_id = {r.doc_id: r for r in expanded}
        edges = sorted(query_mod.list_neighbors(conn, seed.doc_id),
                       key=lambda e: (e.target_id, e.relationship_type or ""))
        for edge in edges:
            if edge.target_id in seen:
                continue
            row = by_id.get(edge.target_id)
            if row is None:
                continue
            if row.citation_class != "citable":
                rejected_non_citable += 1
                continue
            return row, {
                "seed_id": seed.doc_id, "target_id": row.doc_id,
                "edge_type": edge.relationship_type, "seed_rank": base.index(seed) + 1,
                "rejected_non_citable_before_selection": rejected_non_citable
            }
    return None, {"rejected_non_citable_before_selection": rejected_non_citable}


def selftest():
    class R:
        def __init__(self, path):
            self.path = path
            self.doc_id = path
            self.citation_class = "citable"
            self.weak_fit = False
            self.query_scope_warning = None
            self.chunk_text = "one two three"
    assert score([R("x")], ["x"])["recall"] == 1.0
    assert score([R("x")], ["x", "y"])["recall"] == 0.5
    assert score([R("y")], ["x"])["rr"] == 0.0
    assert score([], [])["recall"] is None
    assert score([R("x"), R("y")], ["y"])["first_rank"] == 2
    assert score([R("x"), R("x")], ["x", "z"])["recall"] == 0.5
    assert score([R("z")], ["x"])["recall"] == 0.0
    from types import SimpleNamespace as NS
    class DummyQ:
        @staticmethod
        def list_neighbors(conn, source):
            if source == "seed":
                return [NS(target_id="z", relationship_type="link"),
                        NS(target_id="a", relationship_type="link"),
                        NS(target_id="m", relationship_type="link")]
            return []
        @staticmethod
        def expand_results(conn, seeds, **kwargs):
            a, m, z = R("a"), R("m"), R("z")
            a.citation_class = "not_citable"
            return [a, m, z]
    seed = R("seed")
    row, edge = graph_admit_one(None, DummyQ, [seed])
    assert row.doc_id == "m" and edge["target_id"] == "m"
    assert edge["rejected_non_citable_before_selection"] == 1
    row2, edge2 = graph_admit_one(None, DummyQ, [seed, R("m")])
    assert row2.doc_id == "z"
    assert graph_admit_one(None, DummyQ, [R("noedges")])[0] is None
    print("SELFTEST_PASS: ten oracle/selection controls")


def run(args):
    out = Path(args.out).expanduser().resolve()
    manifest = json.loads((out / "freeze.json").read_text())
    verify_freeze(out, manifest)
    if (out / "results.private.json").exists() or (out / "results.sanitized.json").exists():
        raise RuntimeError("DECISIVE_RUN_ALREADY_PRESENT")
    all_cases = json.loads((out / "cases.frozen.json").read_text())
    sys.path.insert(0, str(out / "product" / "src"))
    from mindgraph import db, embedders, query as q
    runtime = {
        "python": sys.version.split()[0],
        "package_versions": {},
        "model_id": None,
        "model_cache_revision": "UNKNOWN",
        "device": "UNKNOWN",
        "harness_sha256": manifest["harness_sha256"]
    }
    for pkg in ("sentence-transformers", "torch", "numpy", "sqlite-vec", "pydantic"):
        try:
            runtime["package_versions"][pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            runtime["package_versions"][pkg] = "UNAVAILABLE"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    model_error = None
    spec = embedders.resolve_embedder("minilm")
    runtime["model_id"] = spec.model_id
    t0 = time.perf_counter()
    try:
        model = embedders.load_sentence_embedder(spec)
        runtime["device"] = str(getattr(model, "device", "UNKNOWN"))
        path = str(getattr(model, "_model_card_vars", {}).get("model_id", "UNKNOWN"))
        runtime["loaded"] = True
    except Exception as exc:
        model = None
        model_error = type(exc).__name__ + ": " + str(exc)[:350]
        runtime["loaded"] = False
    runtime["model_load_seconds"] = round(time.perf_counter() - t0, 3)
    raw = {"runtime": runtime, "model_error": model_error, "scopes": {}}
    for scope in SCOPES:
        conn = db.get_db(str(out / ("snapshot." + scope + ".sqlite")), read_only=True)
        try:
            db.validate_query_schema(conn, str(out / ("snapshot." + scope + ".sqlite")))
            results = []
            for case in all_cases[scope]:
                question = case["query"]
                t = time.perf_counter()
                lexical = q.run_query(conn, question, None, lexical_top_k=20,
                                      semantic_top_k=0, final_top_k=10)
                latencies = {"A10": time.perf_counter() - t}
                rows = {"A10": lexical}
                graph_edge = None
                if model is not None:
                    formatted = embedders.format_query_text(spec, question, template="mainframe")
                    t = time.perf_counter()
                    b11 = q.run_query(conn, formatted, model, lexical_top_k=20,
                                      semantic_top_k=20, final_top_k=11)
                    latencies["B11"] = time.perf_counter() - t
                    t = time.perf_counter()
                    b10 = q.run_query(conn, formatted, model, lexical_top_k=20,
                                      semantic_top_k=20, final_top_k=10)
                    latencies["B10"] = time.perf_counter() - t
                    if [(r.doc_id, r.chunk_index) for r in b11[:10]] != [
                        (r.doc_id, r.chunk_index) for r in b10]:
                        raise RuntimeError("FUSION_PREFIX_INVARIANCE_FAIL")
                    t = time.perf_counter()
                    graph_row, graph_edge = graph_admit_one(conn, q, b10)
                    rows["B10"], rows["B11"] = b10, b11
                    rows["C10plus1"] = b10 + ([graph_row] if graph_row else [])
                    latencies["C10plus1"] = latencies["B10"] + (time.perf_counter() - t)
                    if [(r.doc_id, r.chunk_index) for r in rows["C10plus1"][:len(b10)]] != [
                        (r.doc_id, r.chunk_index) for r in b10]:
                        raise RuntimeError("C_PREFIX_INVARIANCE_FAIL")
                outcomes = {k: score(v, case["gold"]) for k, v in rows.items()}
                results.append({
                    "case": case["public_id"], "private_id": case["private_id"],
                    "query": question, "class": case["class"],
                    "gold": case["gold"], "index_present_gold": case["index_present_gold"],
                    "index_missing_gold": case["index_missing_gold"],
                    "all_gold_indexed": case["all_gold_indexed"],
                    "negative": case["negative"], "graph_edge": graph_edge,
                    "outcomes": outcomes,
                    "latencies": {k: round(1000 * v, 3) for k, v in latencies.items()},
                    "ranked": {k: [{"doc_id": r.doc_id, "path": r.path,
                                    "signal": r.signal, "citation_class": r.citation_class,
                                    "chunk_index": r.chunk_index} for r in v]
                               for k, v in rows.items()}
                })
            raw["scopes"][scope] = results
        finally:
            conn.close()
    verify_freeze(out, manifest)
    write_json(out / "results.private.json", raw)
    # The public report contains no private queries, paths, docs or raw source.
    public = {"experiment": "MindGraph #54", "product": PRODUCT_SHA,
              "frozen_manifest_sha256": digest(out / "freeze.json"),
              "raw_result_sha256": digest(out / "results.private.json"),
              "runtime": runtime, "model_error": model_error, "scope_reports": {},
              "agent_completion": "NOT_RUN", "progressive_expansion": "NOT_RUN"}
    for scope, cases in raw["scopes"].items():
        pos = [c for c in cases if not c["negative"]]
        eligible = [c for c in pos if c["all_gold_indexed"]]
        graph_appearances = sum(c.get("graph_edge", {}).get("target_id") is not None
                                for c in cases if c.get("graph_edge"))
        arm_reports = {}
        for arm in ARMS:
            if any(arm not in c["outcomes"] for c in cases):
                arm_reports[arm] = {"status": "NOT_RUN"}
                continue
            vals = [c["outcomes"][arm] for c in pos]
            elig = [c["outcomes"][arm] for c in eligible]
            arm_reports[arm] = {
                "all_positive_n": len(pos), "eligible_positive_n": len(eligible),
                "all_any_hit": sum(s["any_found"] for s in vals),
                "all_complete_hit": sum(s["all_found"] for s in vals),
                "all_mean_recall": round(sum(s["recall"] for s in vals)/len(vals), 5) if vals else None,
                "all_mrr": round(sum(s["rr"] for s in vals)/len(vals), 5) if vals else None,
                "eligible_any_hit": sum(s["any_found"] for s in elig),
                "eligible_complete_hit": sum(s["all_found"] for s in elig),
                "eligible_mean_recall": round(sum(s["recall"] for s in elig)/len(elig),5) if elig else None,
                "eligible_mrr": round(sum(s["rr"] for s in elig)/len(elig),5) if elig else None,
                "non_citable_rows_all_cases": sum(c["outcomes"][arm]["citation_non_citable"] for c in cases),
                "total_returned_rows": sum(c["outcomes"][arm]["n_rows"] for c in cases),
                "total_source_chars": sum(c["outcomes"][arm]["chars"] for c in cases),
                "total_whitespace_tokens": sum(c["outcomes"][arm]["whitespace_tokens"] for c in cases),
                "mean_query_ms": round(sum(c["latencies"][arm] for c in cases)/len(cases), 3),
            }
        by_case = []
        for c in cases:
            by_case.append({
                "case": c["case"], "gold_n": len(c["gold"]),
                "gold_indexed_n": len(c["index_present_gold"]),
                "negative": c["negative"],
                "graph_added": bool(c.get("graph_edge", {}) and c["graph_edge"].get("target_id")),
                "graph_edge_type": c["graph_edge"].get("edge_type") if c.get("graph_edge") else None,
                "ranks": {arm: c["outcomes"][arm]["first_rank"] for arm in c["outcomes"]},
                "recalls": {arm: c["outcomes"][arm]["recall"] for arm in c["outcomes"]},
            })
        if model is None:
            disposition = "INCONCLUSIVE_SEMANTIC_RUNTIME_NOT_QUALIFIED"
            better = worse = 0
        else:
            better = sum(c["outcomes"]["C10plus1"]["recall"] > c["outcomes"]["B11"]["recall"]
                         for c in pos)
            worse = sum(c["outcomes"]["C10plus1"]["recall"] < c["outcomes"]["B11"]["recall"]
                        for c in pos)
            if graph_appearances == 0:
                disposition = "INCONCLUSIVE_NO_GRAPH_ADMISSION"
            elif better and not worse and arm_reports["C10plus1"]["non_citable_rows_all_cases"] <= arm_reports["B11"]["non_citable_rows_all_cases"]:
                disposition = "SUPPORTED_WITH_BOUNDS_GRAPH_ADVANTAGE"
            elif better and (worse or arm_reports["C10plus1"]["non_citable_rows_all_cases"] > arm_reports["B11"]["non_citable_rows_all_cases"]):
                disposition = "MIXED_OR_COSTLY_GRAPH_EFFECT"
            else:
                disposition = "NO_OBSERVED_GRAPH_ADVANTAGE"
        public["scope_reports"][scope] = {
            "cases_total": len(cases), "positives": len(pos),
            "eligible_positives": len(eligible),
            "missing_gold_anchors": sum(len(c["index_missing_gold"]) for c in cases),
            "graph_added_cases": graph_appearances, "C_better_than_B11_cases": better,
            "C_worse_than_B11_cases": worse, "disposition": disposition,
            "arms": arm_reports, "per_case": by_case,
        }
    write_json(out / "results.sanitized.json", public)
    print(json.dumps({"run": "COMPLETE_RETRIEVAL_ONLY", "sanitized": public}, sort_keys=True))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("selftest", "prepare", "run"))
    p.add_argument("--out")
    p.add_argument("--repo")
    p.add_argument("--knowledge-db")
    p.add_argument("--projects-db")
    p.add_argument("--knowledge-gold")
    p.add_argument("--projects-gold")
    args = p.parse_args()
    if args.mode == "selftest":
        selftest()
    elif args.mode == "prepare":
        if not all((args.out, args.repo, args.knowledge_db, args.projects_db,
                    args.knowledge_gold, args.projects_gold)):
            p.error("prepare requires all paths")
        prepare(args)
    else:
        if not args.out:
            p.error("run requires --out")
        run(args)


if __name__ == "__main__":
    main()
