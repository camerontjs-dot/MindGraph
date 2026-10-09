#!/usr/bin/env python3
"""Separate read-only consistency verifier for MindGraph research issue #56.

Recomputes counts, validity, connected coverage and weak components directly
from frozen SQLite rows. Does not import the measurement apparatus.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

NAMES=("knowledge","projects","operations")
def digest(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(1048576),b""):h.update(chunk)
    return h.hexdigest()

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def independent_components(ids,edges):
    neighbors={i:set() for i in ids}
    for src,tgt in edges:
        if src in ids and tgt in ids:
            neighbors[src].add(tgt)
            neighbors[tgt].add(src)
    remaining=set(ids)
    sizes=[]
    while remaining:
        first=remaining.pop()
        frontier=[first]
        size=0
        while frontier:
            n=frontier.pop()
            size+=1
            newly=neighbors[n] & remaining
            remaining-=newly
            frontier.extend(newly)
        sizes.append(size)
    return sorted(sizes,reverse=True)

def verify(out):
    freeze=read(out/"FREEZE.json")
    report=read(out/"results.sanitized.json")
    private=out/"results.private.json"
    assert digest(out/"FREEZE.json")==report["freeze_sha256"]
    assert digest(private)==report["private_details_sha256"]
    assert digest(out/"graph_census.py")==freeze["code_sha256"]
    summaries={}
    for name in NAMES:
        snap=out/f"snapshot.{name}.sqlite"
        assert digest(snap)==freeze["scopes"][name]["sha256"]
        conn=sqlite3.connect("file:"+str(snap)+"?mode=ro",uri=True)
        docs=dict(conn.execute("SELECT id,path FROM documents").fetchall())
        edges=conn.execute("SELECT source_id,target_id,relationship_type FROM edges").fetchall()
        conn.close()
        ids=set(docs)
        both=[(s,t,rel) for s,t,rel in edges if s in ids and t in ids]
        miss_target=sum(s in ids and t not in ids for s,t,_ in edges)
        miss_source=sum(s not in ids and t in ids for s,t,_ in edges)
        miss_both=sum(s not in ids and t not in ids for s,t,_ in edges)
        typed=sum(bool(str(rel).strip()) for _,_,rel in edges if rel is not None)
        incident={k for s,t,_ in both for k in (s,t)}
        seen=set()
        dup=0
        for s,t,_ in edges:
            if (s,t) in seen:dup+=1
            else:seen.add((s,t))
        comp=independent_components(ids,[(s,t) for s,t,_ in both])
        got=report["scopes"][name]
        expected={
            "documents":len(ids),
            "edges_stored":len(edges),
            "edges_resolved_both":len(both),
            "edges_with_missing_target":miss_target,
            "edges_with_missing_source":miss_source,
            "edges_with_both_missing":miss_both,
            "typed_edges":typed,
            "untyped_edges":len(edges)-typed,
            "resolved_self_loops":sum(s==t for s,t,_ in both),
            "duplicate_endpoint_edges":dup,
            "nodes_with_resolved_edge":len(incident),
            "nodes_without_resolved_edge":len(ids)-len(incident),
            "weak_components":len(comp),
            "nontrivial_weak_components":sum(k>1 for k in comp),
            "largest_weak_component_size":max(comp,default=0),
            "weak_component_sizes_largest10":comp[:10],
        }
        for metric,value in expected.items():
            assert got[metric]==value,(name,metric,got[metric],value)
        assert sum(got["document_prefixes"].values())==len(ids)
        assert sum(row["count"] for row in got["resolved_edge_prefix_pairs"])==len(both)
        assert 0<=got["resolved_edge_coverage"]<=1
        summaries[name]={"verified":"PASS",**expected}
    conn_a=sqlite3.connect("file:"+str(out/"snapshot.projects.sqlite")+"?mode=ro",uri=True)
    conn_b=sqlite3.connect("file:"+str(out/"snapshot.operations.sqlite")+"?mode=ro",uri=True)
    a=dict((path,doc_id) for doc_id,path in conn_a.execute("SELECT id,path FROM documents"))
    b=dict((path,doc_id) for doc_id,path in conn_b.execute("SELECT id,path FROM documents"))
    conn_a.close();conn_b.close()
    intersection=set(a)&set(b)
    overlap=report["cross_index_overlap"]
    assert overlap["exact_path_overlap"]==len(intersection)
    assert overlap["overlap_with_different_scoped_ids"]==sum(a[p]!=b[p] for p in intersection)
    assert overlap["projects_only_paths"]==len(a.keys()-b.keys())
    assert overlap["operations_only_paths"]==len(b.keys()-a.keys())
    return {"status":"VERIFY_PASS","scopes":summaries,"source_path_overlap":len(intersection)}

if __name__=="__main__":
    if len(sys.argv)!=2:raise SystemExit("usage: verify_results.py PRIVATE_EXPERIMENT_DIR")
    print(json.dumps(verify(Path(sys.argv[1]).expanduser().resolve(strict=True)),sort_keys=True))
