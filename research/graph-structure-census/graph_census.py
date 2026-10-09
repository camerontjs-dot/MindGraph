#!/usr/bin/env python3
"""Frozen, read-only MindGraph Knowledge/Projects/Operations graph census.

Research #56. Does not establish edge correctness, source truth, or task utility.
Uses transaction-consistent SQLite backups, not direct filesystem DB copies.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

NAMES = ("knowledge", "projects", "operations")
COLUMNS = {"documents": {"id", "path"}, "edges": {"source_id", "target_id", "relationship_type"}}

def digest(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as fh:
        for b in iter(lambda:fh.read(1048576),b""): h.update(b)
    return h.hexdigest()

def write_new(p, data):
    with Path(p).open("x", encoding="utf-8") as f:
        json.dump(data,f,indent=2,sort_keys=True)
        f.write("\n")

def ro(path):
    p=Path(path).expanduser().resolve(strict=True)
    c=sqlite3.connect("file:"+quote(str(p),safe="/")+"?mode=ro",uri=True,timeout=30)
    c.execute("PRAGMA query_only=ON")
    return c

def backup(src,dest):
    if dest.exists(): raise FileExistsError("snapshot already exists")
    with ro(src) as a:
        with sqlite3.connect(str(dest)) as b:
            a.backup(b)

def schema(c):
    for t,expect in COLUMNS.items():
        fields={row[1] for row in c.execute("PRAGMA table_info("+t+")")}
        if expect-fields: raise ValueError("missing columns "+t+": "+str(sorted(expect-fields)))

def prefix(path):
    head=str(path or "").split("/",1)[0]
    return head if head in ("10_knowledge","30_projects","40_operations") else "other"

def dstat(xs):
    if not xs: return {"mean":0,"p50":0,"p90":0,"p99":0,"max":0}
    xs=sorted(xs)
    return {"mean":round(sum(xs)/len(xs),4),"p50":xs[(len(xs)-1)*50//100],
            "p90":xs[(len(xs)-1)*90//100],"p99":xs[(len(xs)-1)*99//100],"max":xs[-1]}

def census(c,private=False):
    schema(c)
    dr=c.execute("SELECT id,path,index_id,namespace FROM documents ORDER BY id").fetchall()
    er=c.execute("SELECT source_id,target_id,relationship_type FROM edges ORDER BY source_id,target_id,relationship_type").fetchall()
    docs={}
    for id,path,index_id,ns in dr:
        if id is None or id in docs: raise ValueError("duplicate/empty doc identity")
        docs[id]={"path":str(path or ""),"index_id":index_id,"namespace":ns}
    ids=set(docs)
    degree_in=Counter();degree_out=Counter()
    any_in=Counter();any_out=Counter()
    incident=defaultdict(set)
    nexts=defaultdict(set)
    endpoint=Counter();types=Counter();pairs=Counter()
    valid=[];anomalies=[]
    fail={"missing_target":0,"missing_source":0,"both_missing":0}
    selfloops=0
    for s,t,rel in er:
        a,b=s in ids,t in ids
        endpoint[(s,t)]+=1
        types[str(rel).strip() if rel is not None and str(rel).strip() else "<UNTYPED>"]+=1
        if a: any_out[s]+=1
        if b: any_in[t]+=1
        if a and b:
            valid.append((s,t,rel))
            degree_out[s]+=1
            degree_in[t]+=1
            nexts[s].add(t)
            if s==t:selfloops+=1
            else:
                incident[s].add(t)
                incident[t].add(s)
            pairs[(prefix(docs[s]["path"]),prefix(docs[t]["path"]))]+=1
        else:
            kind="missing_target" if a else "missing_source" if b else "both_missing"
            fail[kind]+=1
            if private: anomalies.append({"src_id":s,"target_id":t,"relation":rel,"reason":kind,
                                          "indexed_src_path":docs.get(s,{}).get("path"),
                                          "indexed_target_path":docs.get(t,{}).get("path")})
    parent={v:v for v in ids};weights={v:1 for v in ids}
    def find(v):
        while parent[v]!=v:
            parent[v]=parent[parent[v]]
            v=parent[v]
        return v
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b:
            if weights[a]<weights[b]:a,b=b,a
            parent[b]=a;weights[a]+=weights[b]
    for s,t,_ in valid:union(s,t)
    sizes=sorted(Counter(find(v) for v in ids).values(),reverse=True)
    reachable_one=[];reachable_two=[];two_increment=[]
    for v in ids:
        one=nexts[v]-{v}
        two=set()
        for n in one:two.update(nexts[n])
        two.discard(v)
        reachable_one.append(len(one))
        reachable_two.append(len(one|two))
        two_increment.append(len(two-one))
    involved=set(degree_in)|set(degree_out)
    any_involved=set(any_in)|set(any_out)
    graph={
        "documents":len(ids),"edges_stored":len(er),"edges_resolved_both":len(valid),
        "edges_with_missing_target":fail["missing_target"],
        "edges_with_missing_source":fail["missing_source"],
        "edges_with_both_missing":fail["both_missing"],
        "resolved_self_loops":selfloops,
        "duplicate_endpoint_edges":sum(n-1 for n in endpoint.values() if n>1),
        "typed_edges":len(er)-types["<UNTYPED>"],"untyped_edges":types["<UNTYPED>"],
        "distinct_typed_edge_values":len(types)-int("<UNTYPED>" in types),
        "document_prefixes":dict(sorted(Counter(prefix(v["path"]) for v in docs.values()).items())),
        "resolved_edge_prefix_pairs":[{"source_prefix":s,"target_prefix":t,"count":n}
                                      for (s,t),n in sorted(pairs.items())],
        "nodes_with_resolved_edge":len(involved),
        "nodes_without_resolved_edge":len(ids)-len(involved),
        "resolved_edge_coverage":round(len(involved)/len(ids),6) if ids else None,
        "nodes_with_any_stored_edge":len(any_involved),
        "nodes_with_only_dangling_incidence":len(any_involved-involved),
        "nodes_with_resolved_inbound":len(degree_in),
        "nodes_with_resolved_outbound":len(degree_out),
        "weak_components":len(sizes),
        "nontrivial_weak_components":sum(x>1 for x in sizes),
        "weak_component_sizes_largest10":sizes[:10],
        "largest_weak_component_size":sizes[0] if sizes else 0,
        "largest_component_fraction":round((sizes[0] if sizes else 0)/len(ids),6) if ids else None,
        "resolved_in_degree":dstat([degree_in[x] for x in ids]),
        "resolved_out_degree":dstat([degree_out[x] for x in ids]),
        "one_hop_reachable":dstat(reachable_one),
        "within_two_hops_reachable":dstat(reachable_two),
        "extra_second_hop_reachable":dstat(two_increment),
        "missing_endpoint_edge_fraction":round(sum(fail.values())/len(er),6) if er else 0,
    }
    raw={}
    if private:
        raw={"edge_anomalies":anomalies,
             "relationship_types":[{"type":k,"count":v} for k,v in types.most_common()],
             "max_in_degree_paths":sorted(
                 ({"path":docs[x]["path"],"degree":degree_in[x]} for x in ids),
                 key=lambda x:(-x["degree"],x["path"]))[:25],
             "max_out_degree_paths":sorted(
                 ({"path":docs[x]["path"],"degree":degree_out[x]} for x in ids),
                 key=lambda x:(-x["degree"],x["path"]))[:25]}
    return graph,raw

def overlap(p,o):
    with ro(p) as a:
        paths_a={r[1]:r[0] for r in a.execute("SELECT id,path FROM documents")}
    with ro(o) as b:
        paths_b={r[1]:r[0] for r in b.execute("SELECT id,path FROM documents")}
    shared=paths_a.keys() & paths_b.keys()
    return {"exact_path_overlap":len(shared),
            "projects_only_paths":len(paths_a.keys()-paths_b.keys()),
            "operations_only_paths":len(paths_b.keys()-paths_a.keys()),
            "overlap_with_different_scoped_ids":sum(paths_a[p]!=paths_b[p] for p in shared),
            "non_claim":"path overlap does not establish index-byte equivalence or cross-index edge traversability"}

def test():
    c=sqlite3.connect(":memory:")
    c.execute("CREATE TABLE documents(id TEXT,path TEXT,index_id TEXT,namespace TEXT)")
    c.execute("CREATE TABLE edges(source_id TEXT,target_id TEXT,relationship_type TEXT)")
    c.executemany("INSERT INTO documents VALUES (?,?, 'i','ns')",
      [("a","30_projects/a"),("b","40_operations/b"),("c","30_projects/c"),
       ("d","40_operations/d"),("e","30_projects/e"),("f","30_projects/f")])
    c.executemany("INSERT INTO edges VALUES (?,?,?)",
      [("a","b","depends_on"),("b","c",None),("a","OUT",None),("OUT","a",None),
       ("c","c",None),("e","f","supports"),("e","f","documents")])
    r,_=census(c)
    assert (r["documents"],r["edges_stored"],r["edges_resolved_both"])==(6,7,5)
    assert (r["edges_with_missing_source"],r["edges_with_missing_target"])==(1,1)
    assert (r["weak_components"],r["largest_weak_component_size"],r["nodes_without_resolved_edge"])==(3,3,1)
    assert r["resolved_self_loops"]==1 and r["duplicate_endpoint_edges"]==1
    assert r["typed_edges"]==3 and r["distinct_typed_edge_values"]==3
    assert {"source_prefix":"30_projects","target_prefix":"40_operations","count":1} in r["resolved_edge_prefix_pairs"]
    c.execute("DELETE FROM edges WHERE source_id='a' AND target_id='b'")
    assert census(c)[0]["largest_weak_component_size"]<r["largest_weak_component_size"]
    c.execute("INSERT INTO edges VALUES ('a','b','depends_on')")
    c.execute("UPDATE edges SET target_id='absent' WHERE source_id='b' AND target_id='c'")
    q,_=census(c)
    assert q["edges_with_missing_target"]>r["edges_with_missing_target"]
    c.execute("UPDATE edges SET target_id='c' WHERE source_id='b' AND target_id='absent'")
    c.execute("UPDATE edges SET relationship_type=NULL WHERE source_id='a' AND target_id='b'")
    assert census(c)[0]["typed_edges"]<r["typed_edges"]
    c.execute("INSERT INTO documents VALUES ('new','30_projects/new','i','ns')")
    assert census(c)[0]["nodes_without_resolved_edge"]==r["nodes_without_resolved_edge"]+1
    c.close()
    print("SELFTEST_PASS: connectivity, dangling, typed, ownership, mutation sensitivity")

def prepare(args):
    out=Path(args.out).expanduser().resolve()
    out.mkdir(parents=True,exist_ok=True)
    if (out/"FREEZE.json").exists(): raise FileExistsError("freeze exists")
    sources={"knowledge":Path(args.knowledge_db).expanduser(),
             "projects":Path(args.projects_db).expanduser(),
             "operations":Path(args.operations_db).expanduser()}
    if len({p.resolve() for p in sources.values()})!=3:raise ValueError("input DBs must be distinct")
    m={"research":"https://github.com/camerontjs-dot/MindGraph/issues/56",
       "experiment":"graph-structure-census-RC0",
       "created_at_utc":datetime.now(timezone.utc).isoformat(),
       "code_sha256":digest(__file__),
       "product_reference_commit":"8df9ae7fccdb742558950ef9bdedb96cc74df6d0",
       "scope_authority":"independent installed database snapshots; no reindex",
       "scopes":{}}
    for n,source in sources.items():
        dest=out/("snapshot."+n+".sqlite")
        backup(source,dest)
        with ro(dest) as c:
            schema(c)
            m["scopes"][n]={"sha256":digest(dest),"bytes":dest.stat().st_size,
                            "documents":c.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                            "edges":c.execute("SELECT COUNT(*) FROM edges").fetchone()[0]}
    write_new(out/"FREEZE.json",m)
    print(json.dumps({"status":"FROZEN_BEFORE_CENSUS","freeze_sha256":digest(out/"FREEZE.json"),
                      "code_sha256":m["code_sha256"],"scopes":m["scopes"]},sort_keys=True))

def measure(args):
    out=Path(args.out).expanduser().resolve(strict=True)
    m=json.loads((out/"FREEZE.json").read_text())
    if digest(__file__)!=m["code_sha256"]:raise ValueError("code hash drift")
    if (out/"results.sanitized.json").exists() or (out/"results.private.json").exists():
        raise FileExistsError("NO_OVERWRITE decisive result")
    snaps={n:out/("snapshot."+n+".sqlite") for n in NAMES}
    for n,p in snaps.items():
        if digest(p)!=m["scopes"][n]["sha256"]:raise ValueError("snapshot hash drift "+n)
    report={"research":m["research"],"freeze_sha256":digest(out/"FREEZE.json"),
            "census_code_sha256":m["code_sha256"],"python":sys.version.split()[0],
            "disposition":"STRUCTURE_CHARACTERIZED_WITH_BOUNDS",
            "task_utility":"NOT_RUN","scopes":{}}
    private={"scopes":{}}
    for n,p in snaps.items():
        with ro(p) as conn:
            report["scopes"][n],private["scopes"][n]=census(conn,private=True)
    report["cross_index_overlap"]=overlap(snaps["projects"],snaps["operations"])
    for n,p in snaps.items():
        if digest(p)!=m["scopes"][n]["sha256"]:raise ValueError("post-run snapshot drift "+n)
    write_new(out/"results.private.json",private)
    report["private_details_sha256"]=digest(out/"results.private.json")
    write_new(out/"results.sanitized.json",report)
    print(json.dumps(report,sort_keys=True))

def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("command",choices=("selftest","prepare","measure"))
    a.add_argument("--out");a.add_argument("--knowledge-db")
    a.add_argument("--projects-db");a.add_argument("--operations-db")
    x=a.parse_args()
    if x.command=="selftest":test()
    elif x.command=="prepare":
        if not all((x.out,x.knowledge_db,x.projects_db,x.operations_db)):
            a.error("prepare requires --out plus three database paths")
        prepare(x)
    else:
        if not x.out:a.error("measure requires --out")
        measure(x)
if __name__=="__main__":main()
