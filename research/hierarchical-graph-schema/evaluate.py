#!/usr/bin/env python3
"""Frozen case evaluation for research schema RC0, no model actors."""
import argparse
from collections import defaultdict
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import subprocess

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def source_code(repo,rev,path):
    return subprocess.check_output(["git","-C",str(repo),"show",rev+":"+path],timeout=30)

def tracked_paths(repo,rev):
    return set(subprocess.check_output(["git","-C",str(repo),"ls-tree","-r","--name-only",rev],text=True,timeout=30).splitlines())

def by_ids(graph):
    return {n["id"]:n for n in graph["nodes"]}

def real_graph_path(graph,source,target,predicate,max_hops=9):
    edges=defaultdict(list)
    for e in graph["edges"]:
        if e["predicate"]==predicate:edges[e["source"]].append(e)
    for key in edges:edges[key].sort(key=lambda e:e["id"])
    todo=[(source,[],{source})]
    for current,chain,seen in todo:
        if current==target:return chain
        if len(chain)>=max_hops:continue
        for edge in edges.get(current,[]):
            if edge["target"] in seen:continue
            todo.append((edge["target"],chain+[edge],seen|{edge["target"]}))
    return None

def sql_graph_path(graph,source,target,predicate,max_hops=9):
    db=sqlite3.connect(":memory:")
    db.execute("CREATE TABLE edges (eid TEXT,src TEXT,tgt TEXT,predicate TEXT)")
    db.executemany("INSERT INTO edges VALUES (?,?,?,?)",[
        (e["id"],e["source"],e["target"],e["predicate"]) for e in graph["edges"]
    ])
    rows=db.execute("""
    WITH RECURSIVE paths(node,visited,path,depth) AS (
       SELECT ?, '|' || ? || '|', '', 0
       UNION ALL
       SELECT e.tgt, p.visited || e.tgt || '|',
              CASE WHEN p.path='' THEN e.eid ELSE p.path || '|' || e.eid END,
              p.depth+1
       FROM paths p JOIN edges e ON e.src=p.node
       WHERE p.depth < ? AND e.predicate=?
         AND instr(p.visited, '|' || e.tgt || '|')=0
    )
    SELECT path FROM paths WHERE node=? ORDER BY depth,path LIMIT 1
    """,(source,source,max_hops,predicate,target)).fetchone()
    db.close()
    if rows is None:return None
    lookup={e["id"]:e for e in graph["edges"]}
    return [lookup[p] for p in rows[0].split("|")] if rows[0] else []

def normalize_chain(chain):
    return None if chain is None else [(e["source"],e["predicate"],e["target"]) for e in chain]

def verify_real(cases,graphs,repo_dirs):
    reports=[];counter={"positives":0,"negatives":0,"graph_satisfied":0,
                        "git_direct_satisfied":0,"sql_parity":0,"violations":[]}
    for c in cases["positive_cases"]:
        graph=graphs[c["repo"]]
        nodes=by_ids(graph)
        source_loc=c["subject"];target_loc=c["object"];predicate=c["predicate"]
        projects=[n for n in graph["nodes"] if n["kind"]=="project"]
        if source_loc=="repo":
            sources=projects
        else:
            sources=[n for n in graph["nodes"] if n["kind"]=="file" and n["locator"]==source_loc]
        if predicate=="DECLARES":
            targets=[n for n in graph["nodes"] if n["kind"]=="symbol"
                     and n["attrs"].get("name")==target_loc
                     and n["locator"].startswith(source_loc+"::")
                     and n["attrs"].get("symbol_kind")==c["expected_symbol_type"]]
        else:
            targets=[n for n in graph["nodes"] if n["kind"]=="file" and n["locator"]==target_loc]
        source_ids=[n["id"] for n in sources]
        target_ids=[n["id"] for n in targets]
        if len(source_ids)!=1 or len(target_ids)!=1:
            pchain=None;schain=None
        else:
            pchain=real_graph_path(graph,source_ids[0],target_ids[0],predicate)
            schain=sql_graph_path(graph,source_ids[0],target_ids[0],predicate)
        resolved=pchain is not None
        parity=normalize_chain(pchain)==normalize_chain(schain)
        repo=repo_dirs[c["repo"]]
        rev=cases["git_versions"][c["repo"]]["commit"]
        paths=tracked_paths(repo,rev)
        if predicate=="CONTAINS":baseline=target_loc in paths
        elif predicate=="DECLARES":
            body=source_code(repo,rev,source_loc).decode()
            baseline=bool(re.search(r"(?m)^(?:async\s+)?def\s+"+re.escape(target_loc)+r"\s*\(",body))
        elif predicate=="IMPORTS_MODULE":
            body=source_code(repo,rev,source_loc).decode()
            baseline="mindgraph" in body and target_loc in paths
        else:baseline=False
        counter["positives"]+=1
        counter["graph_satisfied"]+=int(resolved)
        counter["git_direct_satisfied"]+=int(baseline)
        counter["sql_parity"]+=int(parity)
        if not resolved or not baseline or not parity:counter["violations"].append(c["id"])
        reports.append({"id":c["id"],"graph_satisfied":resolved,
                        "sqlite_parity":parity,"direct_source_baseline":baseline,
                        "path_edges":len(pchain) if pchain is not None else None,
                        "path_witness_ids":[e["id"] for e in (pchain or [])]})
    for c in cases["negative_cases"]:
        graph=graphs[c["repo"]]
        e=[e for e in graph["edges"] if e["predicate"]==c.get("forbid_predicate")]
        if c.get("subject"):
            file_ids={n["id"] for n in graph["nodes"]
                      if n["kind"]=="file" and n["locator"]==c["subject"]}
            e=[v for v in e if v["source"] in file_ids]
        passed=len(e)==0
        if c.get("forbid_path"):
            try:
                import projection
                projection.validate_path(c["forbid_path"])
            except ValueError:
                passed=True
            else:
                passed=False
        counter["negatives"]+=1
        if not passed:counter["violations"].append(c["id"])
        reports.append({"id":c["id"],"negative_control_satisfied":passed})
    return {"summary":counter,"cases":reports}

def synthetic_graph(fixture):
    import projection
    nodes=[{"id":n["id"],"scope":n["scope"],"kind":"record","locator":n["id"],
            "source_id":"SX1","access":n["access"],"citation_class":n["eligibility"],
            "as_of_commit":None,"attrs":{}} for n in fixture["nodes"]]
    sources=[{"id":s["id"],"kind":"synthetic_statement","text":s["text"],
              "content_sha256":hashlib.sha256(s["text"].encode()).hexdigest(),
              "access":s["access"],"citation_class":s["status"]} for s in fixture["sources"]]
    edges=[]
    for e in fixture["edges"]:
        eid=projection.identity("edge","fixture",e["source"],e["target"],e["predicate"],e["evidence"])
        edges.append({"id":eid,"source":e["source"],"target":e["target"],
                      "predicate":e["predicate"],"provenance":e["provenance"],
                      "evidence_source_id":e["evidence"],"direction":"forward",
                      "line":None,**({"scope_bridge":"explicit_reference"} if e["source"].split(":")[0]!=e["target"].split(":")[0] else {})})
    graph={"schema":projection.VERSION,"profile":"federated_overlay","scope":"federation",
           "authority":{"kind":"synthetic_frozen_fixture"},
           "nodes":nodes,"sources":sources,"edges":edges,
           "diagnostics":{}}
    projection.validate(graph)
    return graph

def path_options(g,start,goal,max_hops):
    edge_map=defaultdict(list)
    for e in g["edges"]:edge_map[e["source"]].append(e)
    for k in edge_map:edge_map[k].sort(key=lambda x:x["id"])
    results=[]
    frontier=[(start,[],{start})]
    for current,chain,seen in frontier:
        if current==goal:
            results.append(chain)
            continue
        if len(chain)>=max_hops:continue
        for e in edge_map.get(current,[]):
            if e["target"] in seen:continue
            frontier.append((e["target"],chain+[e],seen|{e["target"]}))
        if len(frontier)>1000:raise ValueError("unbounded path explosion")
    return sorted(results,key=lambda c:(len(c),[e["id"] for e in c]))

def admissibility(g,chain,start,grant=None):
    nodes=by_ids(g);sources={s["id"]:s for s in g["sources"]}
    if start not in nodes:return False,["unknown_start"]
    reasons=[]
    visited=[start]+[e["target"] for e in chain]
    for n_id in visited:
        n=nodes[n_id]
        if n["access"]!="allowed" or n["citation_class"]!="citable":
            reasons.append("inadmissible_node:"+n_id)
    for e in chain:
        src=sources[e["evidence_source_id"]]
        if src["access"]!="allowed" or src["citation_class"]!="citable":
            reasons.append("inadmissible_evidence:"+e["evidence_source_id"])
        if e["provenance"] not in ("observed_structure","source_asserted","reviewed_coded"):
            reasons.append("nomination:"+e["id"])
        scope_a=nodes[e["source"]]["scope"]
        scope_b=nodes[e["target"]]["scope"]
        if scope_a!=scope_b:
            if e.get("scope_bridge")!="explicit_reference" or (scope_a,scope_b) not in (grant or []):
                reasons.append("cross_scope_unauthorized")
    return not reasons,reasons

def check_fixture(fixture):
    import projection
    graph=synthetic_graph(fixture)
    records=[];passes=0
    for case in fixture["predeclared_queries"]:
        chains=path_options(graph,case["from"],case["to"],case["max_hops"])
        qualified=[c for c in chains if admissibility(graph,c,case["from"])[0]]
        received=bool(qualified)
        accepted=received==case["qualified"]
        # SQL evidence controls path existence (including disallowed).
        sql_path=sql_graph_path(graph,case["from"],case["to"],"LINKS_TO",case["max_hops"])
        sql_exists=sql_path is not None
        py_exists=bool(chains)
        parity=sql_exists==py_exists
        if received:
            chosen=qualified[0]
            source_ids=[e["evidence_source_id"] for e in chosen]
            expected=case.get("source_path")
            if expected is not None:accepted=accepted and source_ids==expected
        passes+=int(accepted and parity)
        records.append({"id":case["id"],"qualifies":received,
                        "expected":case["qualified"],"passed":accepted,"sql_path_parity":parity,
                        "candidate_paths":len(chains),
                        "path_witness":source_ids if received else [],
                        "first_inadmissible_reason":admissibility(graph,chains[0],case["from"])[1][:3] if chains else []})
    # Positive test of explicit cross-scope capability after a separately declared grant.
    bridge=path_options(graph,"alpha:D","beta:Q",3)
    granted=any(admissibility(graph,c,"alpha:D",[("project:alpha","project:beta")])[0] for c in bridge)
    # Negative and mutation sensitivity on fixed fixture/gold.
    mutations=[]
    def expects_rejected(name,mutator):
        altered=copy.deepcopy(graph)
        mutator(altered)
        try:projection.validate(altered)
        except (ValueError,KeyError):mutations.append((name,True))
        else:mutations.append((name,False))
    expects_rejected("missing_evidence",lambda x:x["edges"][0].update(evidence_source_id="NO_SUCH_SOURCE"))
    expects_rejected("duplicate_edge_identity",lambda x:x["edges"].append(copy.deepcopy(x["edges"][0])))
    expects_rejected("reversed_semantic_direction_field",lambda x:x["edges"][0].update(direction="reverse"))
    expects_rejected("bad_cross_scope_witness",lambda x:next(e for e in x["edges"] if e["source"]=="alpha:D").pop("scope_bridge"))
    expects_rejected("nomination_as_structural_fact",lambda x:next(e for e in x["edges"] if e["target"]=="alpha:E").update(predicate="IMPORTS_MODULE"))
    def wrong_node(x):
        x["nodes"][0]["source_id"]="NOT_A_SOURCE"
    expects_rejected("dangling_node_source",wrong_node)
    # Semantic mutation is judged against the previously frozen expected set, not schema syntax.
    tampered=copy.deepcopy(graph)
    tampered_nodes={n["id"]:n for n in tampered["nodes"]}
    tampered_nodes["alpha:B"]["citation_class"]="citable"
    next(s for s in tampered["sources"] if s["id"]=="SX2")["citation_class"]="citable"
    path=path_options(tampered,"alpha:A","alpha:C",3)
    mutations.append(("detect_quarantine_bypass",all(not admissibility(tampered,c,"alpha:A")[0] for c in path)==False))
    # Wrong direction should invalidate the frozen AtoD positive.
    bad=copy.deepcopy(graph)
    reverse=next(e for e in bad["edges"] if e["source"]=="alpha:A" and e["target"]=="alpha:D")
    reverse["source"],reverse["target"]=reverse["target"],reverse["source"]
    mutations.append(("detect_direction_reversal",not path_options(bad,"alpha:A","alpha:D",3)))
    return {"n_cases":len(records),"passed":passes,"results":records,
            "explicit_cross_scope_grant_positive":granted,
            "mutation_gates":{k:v for k,v in mutations},
            "all_controls_pass":passes==len(records) and granted and all(v for _,v in mutations)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--projection",required=True)
    p.add_argument("--cases",required=True)
    p.add_argument("--fixture",required=True)
    p.add_argument("--mindgraph-repo",required=True)
    p.add_argument("--conduit-repo",required=True)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    out=Path(args.output)
    if out.exists():raise FileExistsError("NO_OVERWRITE")
    cases=load(args.cases)
    graphs={name:load(Path(args.projection)/(name+".json")) for name in ("mindgraph","conduit")}
    real=verify_real(cases,graphs,{"mindgraph":Path(args.mindgraph_repo),"conduit":Path(args.conduit_repo)})
    syn=check_fixture(load(args.fixture))
    data={"source_case_sha256":hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
          "synthetic_fixture_sha256":hashlib.sha256(Path(args.fixture).read_bytes()).hexdigest(),
          "real":real,"synthetic":syn,"actor_tasks":"NOT_RUN"}
    out.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"real":real["summary"],
                      "synthetic":{"cases":syn["n_cases"],"passed":syn["passed"],
                                   "cross_scope_grant":syn["explicit_cross_scope_grant_positive"],
                                   "mutation_gates":syn["mutation_gates"],
                                   "all_controls_pass":syn["all_controls_pass"]}},sort_keys=True))

if __name__=="__main__":main()
