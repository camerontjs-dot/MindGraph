#!/usr/bin/env python3
"""Independent-formula read-only check of MindGraph issue #54 frozen results.

This verifier does not import the experimental harness or its score function.
It does not mutate its targets.
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

def h(p):
    z = hashlib.sha256()
    with p.open("rb") as f:
        for buf in iter(lambda: f.read(1 << 20), b""):
            z.update(buf)
    return z.hexdigest()

out = Path(sys.argv[1]).resolve()
m = json.loads((out/"freeze.json").read_text())
raw = json.loads((out/"results.private.json").read_text())
public = json.loads((out/"results.sanitized.json").read_text())
assert h(out/"harness.py") == m["harness_sha256"]
assert h(out/"cases.frozen.json") == m["cases_sha256"]
assert h(out/"product.tar") == m["product"]["archive_sha256"]
assert h(out/"results.private.json") == public["raw_result_sha256"]
for scope in ("knowledge", "projects"):
    info = m["scopes"][scope]
    assert h(out/f"snapshot.{scope}.sqlite") == info["snapshot_sha256"]
    assert h(out/f"source.{scope}.gold.yaml") == info["gold_sha256"]
    conn = sqlite3.connect(f"file:{out/f'snapshot.{scope}.sqlite'}?mode=ro", uri=True)
    edge_pairs = set(conn.execute("select source_id,target_id from edges").fetchall())
    docpaths = set(t[0] for t in conn.execute("select path from documents"))
    cases = raw["scopes"][scope]
    assert len(cases) == info["cases"]
    recalls = {arm: [] for arm in m["arms"]}
    counts = {arm: 0 for arm in m["arms"]}
    graph = 0
    for c in cases:
        gold = c["gold"]
        assert c["index_present_gold"] == [x for x in gold if x in docpaths]
        assert c["index_missing_gold"] == [x for x in gold if x not in docpaths]
        for arm in m["arms"]:
            rows = c["ranked"][arm]
            outputs = c["outcomes"][arm]
            docids = [x["doc_id"] for x in rows]
            paths = [x["path"] for x in rows]
            assert len(rows) <= (10 if arm in ("A10","B10") else 11)
            assert len(docids) == len(set(docids)), (scope,c["case"],arm,"duplicate doc")
            hits = sum(x in set(paths) for x in gold)
            rec = (hits/len(gold)) if gold else None
            assert outputs["recall"] == rec, (scope,c["case"],arm,"recall")
            pos = next((n for n, row in enumerate(rows,1) if row["path"] in gold), None)
            assert outputs["first_rank"] == pos, (scope,c["case"],arm,"rank")
            assert outputs["all_found"] == (bool(gold) and hits == len(gold))
            assert outputs["any_found"] == bool(hits)
            assert outputs["n_rows"] == len(rows)
            assert outputs["n_distinct_docs"] == len(set(docids))
            if gold:
                recalls[arm].append(rec)
                counts[arm] += bool(hits)
        b10 = c["ranked"]["B10"]
        cgraph = c["ranked"]["C10plus1"]
        b11 = c["ranked"]["B11"]
        assert b11[:10] == b10, (scope,c["case"],"B11 prefix drift")
        assert cgraph[:10] == b10, (scope,c["case"],"C prefix drift")
        candidate = c["graph_edge"]
        if candidate and candidate.get("target_id"):
            graph += 1
            assert len(cgraph)==len(b10)+1, (scope,c["case"],"graph append not exactly one")
            assert candidate["seed_rank"] in (1,2,3)
            assert b10[candidate["seed_rank"]-1]["doc_id"] == candidate["seed_id"]
            assert cgraph[-1]["doc_id"] == candidate["target_id"]
            assert (candidate["seed_id"],candidate["target_id"]) in edge_pairs
            assert cgraph[-1]["citation_class"] == "citable"
            assert candidate["target_id"] not in {x["doc_id"] for x in b10}
        else:
            assert cgraph == b10, (scope,c["case"],"graph-off equality")
    summary = public["scope_reports"][scope]
    assert summary["graph_added_cases"] == graph
    for arm in m["arms"]:
        if recalls[arm]:
            avg = sum(recalls[arm])/len(recalls[arm])
            assert abs(summary["arms"][arm]["all_mean_recall"]-avg)<1e-5, (scope,arm,"aggregate")
            assert summary["arms"][arm]["all_any_hit"]==counts[arm]
    print(json.dumps({"scope":scope,"cases":len(cases),"positive":len(recalls["A10"]),
                      "graph_added":graph,"lexical_any":counts["A10"],
                      "hybrid11_any":counts["B11"],
                      "graph_any":counts["C10plus1"],
                      "verified":"PASS"},sort_keys=True))
    conn.close()
print("VERIFY_PASS: frozen source, snapshots, case oracle, per-arm ranks, link custody, metrics")
