#!/usr/bin/env python3
"""Source-rechecking verifier, separate from experimental graph builder.

Reads frozen Git trees and immutable SQLite snapshots. Does not import
projection.py or accept edge existence as proof of a correct source witness.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
from urllib.parse import quote

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as s:
        for x in iter(lambda:s.read(1048576),b""):h.update(x)
    return h.hexdigest()
def ident(prefix,*parts):
    return prefix+":"+hashlib.sha256("\0".join(str(x) for x in parts).encode()).hexdigest()
def get(repo,*args):
    return subprocess.check_output(["git","-C",str(repo),*args],timeout=30)
def jsonfile(path):
    return json.loads(Path(path).read_text())
def assert_manifest(repo,info):
    actual=get(repo,"rev-parse",info["commit"]+"^{tree}").decode().strip()
    if actual!=info["tree"]:raise ValueError("wrong immutable tree")
    table={}
    for r in get(repo,"ls-tree","-r","-z",info["commit"]).split(b"\0"):
        if not r:continue
        meta,path=r.split(b"\t",1)
        mode,kind,blob=meta.decode().split()
        table[path.decode()]=(mode,kind,blob)
    return table

def verify_project(g,info,repo):
    if g["authority"]!={"kind":"git_commit","repo":info["repo"],"commit":info["commit"],"tree":info["tree"]}:
        raise ValueError("graph authority differs from checked Git")
    table=assert_manifest(repo,info)
    nodes={n["id"]:n for n in g["nodes"]}
    sources={s["id"]:s for s in g["sources"]}
    if len(nodes)!=len(g["nodes"]) or len(sources)!=len(g["sources"]):
        raise ValueError("duplicate recorded objects")
    if g["scope"] not in ("project:mindgraph","project:conduit"):
        raise ValueError("unexpected scope")
    repo_tree_sources=[s for s in sources.values() if s["kind"]=="git_tree"]
    if len(repo_tree_sources)!=1 or repo_tree_sources[0]["tree"]!=info["tree"]:
        raise ValueError("wrong tree root source")
    tracked_source_files=set()
    parsed={}
    for s in sources.values():
        if s["access"]!="allowed" or s["citation_class"]!="citable":
            raise ValueError("source-level access/citation gate failed")
        if s["kind"]=="git_tree":continue
        if s["kind"]!="git_blob":raise ValueError("unattested source kind")
        p=s["path"]
        if (p not in table or table[p]!=(("100755" if table[p][0]=="100755" else "100644"),"blob",s["blob"])
            or s["commit"]!=info["commit"] or s["repo"]!=info["repo"]):
            raise ValueError("wrong pinned tree source")
        if any(part in ("secrets","node_modules","outputs",".venv",".git","build")
               for part in PurePosixPath(p).parts):
            raise ValueError("unsafe tree selection")
        tracked_source_files.add(p)
        if s["id"]!=ident("source",info["repo"],info["commit"],p,s["blob"]):
            raise ValueError("source identity not tied to blob")
        raw=get(repo,"show",info["commit"]+":"+p)
        calculated=hashlib.sha1(("blob "+str(len(raw))+"\0").encode()+raw).hexdigest()
        if calculated!=s["blob"]:
            raise ValueError("blob bytes do not match Git object")
        if p.endswith(".py"):
            try:parsed[p]=ast.parse(raw.decode("utf-8"))
            except (UnicodeDecodeError,SyntaxError):
                parsed[p]=None
    projects=[n for n in nodes.values() if n["kind"]=="project"]
    if len(projects)!=1:
        raise ValueError("project root not unique")
    file_nodes={n["locator"]:n for n in nodes.values() if n["kind"]=="file"}
    if set(file_nodes)!=tracked_source_files:
        raise ValueError("stored file nodes differ from source blob inventory")
    import_map={}
    for p in tracked_source_files:
        if p.startswith("src/") and p.endswith(".py"):
            m=p[4:-3].replace("/",".")
            if m.endswith(".__init__"):m=m[:-9]
            import_map[m]=p
    allowed_declarations=set()
    for p,tree in parsed.items():
        if tree is None:continue
        for item in tree.body:
            if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                allowed_declarations.add((p,item.name,item.lineno,"class" if isinstance(item,ast.ClassDef) else "function"))
    seen_declarations=set()
    seen_imports=0
    source_by_path={s["path"]:s for s in sources.values() if s["kind"]=="git_blob"}
    for n in nodes.values():
        if n["source_id"] not in sources:
            raise ValueError("orphan node")
        if n["id"]!=ident("node",g["scope"],info["commit"],n["kind"],n["locator"]):
            raise ValueError("node identity substitution")
        if n["access"]!="allowed" or n["citation_class"]!="citable":
            raise ValueError("Git object access/claim mismatch")
        if n["kind"]=="symbol":
            p=n["locator"].split("::",1)[0]
            triple=(p,n["attrs"]["name"],n["attrs"]["start_line"],n["attrs"]["symbol_kind"])
            if triple not in allowed_declarations:
                raise ValueError("unwarranted symbol declaration")
            if n["source_id"]!=source_by_path[p]["id"]:
                raise ValueError("symbol cites different existing file")
            seen_declarations.add(triple)
        if n["kind"]=="file":
            if n["source_id"]!=source_by_path[n["locator"]]["id"]:
                raise ValueError("file cites different existing file")
    if seen_declarations!=allowed_declarations:
        raise ValueError("missing top-level declarations")
    recorded_refs=[]
    for e in g["edges"]:
        if e["source"] not in nodes or e["target"] not in nodes or e["evidence_source_id"] not in sources:
            raise ValueError("missing endpoint/source")
        if e["id"]!=ident("edge",g["scope"],e["source"],e["predicate"],
                          e["target"],e["evidence_source_id"],e["line"]):
            raise ValueError("edge source/line substitution")
        if e["provenance"]!="observed_structure":raise ValueError("unsupported source authority")
        a,b=nodes[e["source"]],nodes[e["target"]]
        if e["predicate"]=="CONTAINS":
            if e["evidence_source_id"]!=b["source_id"]:
                raise ValueError("false containment source")
            p=str(PurePosixPath(b["locator"]).parent)
            if ("." if a["kind"]=="project" else a["locator"])!=p:
                raise ValueError("false filesystem edge")
        elif e["predicate"]=="DECLARES":
            expected=(a["locator"],b["attrs"]["name"],e["line"],b["attrs"]["symbol_kind"])
            if (expected not in allowed_declarations or e["evidence_source_id"]!=a["source_id"]
                or b["source_id"]!=a["source_id"]):
                raise ValueError("claim declaration unsupported by pinned AST")
        elif e["predicate"]=="IMPORTS_MODULE":
            src=a["locator"];tgt=b["locator"]
            if src not in parsed or parsed[src] is None or e["evidence_source_id"]!=a["source_id"]:
                raise ValueError("import witness absent from source")
            matches=[]
            for item in parsed[src].body:
                if not isinstance(item,(ast.Import,ast.ImportFrom)) or item.lineno!=e["line"]:continue
                if isinstance(item,ast.Import):
                    names=[al.name for al in item.names]
                elif item.level==0:
                    base=item.module or ""
                    names=[base]+[base+"."+al.name for al in item.names] if base else []
                else:continue
                resolved={import_map[x] for x in names if x in import_map}
                if tgt in resolved:matches.append(True)
            if not matches:raise ValueError("unwarranted import target/line")
            seen_imports+=1
        else:
            raise ValueError("unsupported source-code semantic relation")
        recorded_refs.append(e["id"])
    return {"nodes":len(nodes),"files":len(file_nodes),"edges":len(g["edges"]),
            "verified_declarations":len(seen_declarations),
            "verified_imports":seen_imports,
            "verified_blobs":len(tracked_source_files),"status":"PASS"}

def verify_legacy(g,snapshot,expected_digest,scope):
    if sha256(snapshot)!=expected_digest or g["authority"]!={"kind":"sqlite_snapshot","sha256":expected_digest}:
        raise ValueError("index snapshot changed")
    con=sqlite3.connect("file:"+quote(str(Path(snapshot).resolve()),safe="/")+"?mode=ro",uri=True)
    docs=con.execute("SELECT id,path,content_hash,trust_profile FROM documents").fetchall()
    edges=con.execute("SELECT source_id,target_id,relationship_type FROM edges").fetchall()
    con.close()
    target={x["id"]:x for x in g["nodes"]}
    src={s["id"]:s for s in g["sources"]}
    doc_index={row[0]:row for row in docs}
    if len(target)!=len(docs) or len(src)!=len(docs):
        raise ValueError("legacy document count wrong")
    for docid,path,sha,trust in docs:
        nid=ident("node",scope,expected_digest,docid)
        sid=ident("source",scope,expected_digest,docid,sha)
        if (nid not in target or sid not in src or target[nid]["source_id"]!=sid
            or target[nid]["locator"]!=path or target[nid]["access"]!="unknown"
            or target[nid]["citation_class"]!="unverified"
            or target[nid]["attrs"]["trust_profile"]!=trust):
            raise ValueError("legacy source authority substitution")
    valid=[(a,b,rel) for a,b,rel in edges if a in doc_index and b in doc_index]
    if len(g["edges"])!=len(valid):raise ValueError("legacy resolved edge mismatch")
    edgeids={e["id"] for e in g["edges"]}
    expected={ident("edge",scope,a,b,rel) for a,b,rel in valid}
    if edgeids!=expected:raise ValueError("legacy edge set not identical to actual snapshot")
    for e in g["edges"]:
        if e["provenance"]!="derived_nomination" or e["predicate"]!="LINKS_TO":
            raise ValueError("legacy link gained authority")
    return {"nodes":len(docs),"edges":len(valid),"unresolved":len(edges)-len(valid),"status":"PASS"}

def main():
    ap=argparse.ArgumentParser()
    for x in ("projection","cases","mindgraph-repo","conduit-repo","output"):
        ap.add_argument("--"+x,required=True)
    for x in ("knowledge-snapshot","operations-snapshot","knowledge-sha256","operations-sha256"):
        ap.add_argument("--"+x)
    args=ap.parse_args()
    if Path(args.output).exists():raise FileExistsError("NO_OVERWRITE")
    cases=jsonfile(args.cases)
    output={}
    for scope in ("mindgraph","conduit"):
        g=jsonfile(Path(args.projection)/(scope+".json"))
        repo=Path(getattr(args,scope.replace("-","_")+"_repo"))
        output[scope]=verify_project(g,cases["git_versions"][scope],repo)
    for scope in ("knowledge","operations"):
        file=Path(args.projection)/(scope+".json")
        snap=getattr(args,scope+"_snapshot")
        expected=getattr(args,scope+"_sha256")
        if not file.exists():
            output[scope]={"status":"NOT_RUN_UNAVAILABLE"}
            continue
        if not snap or not expected:
            raise ValueError("legacy source present without source snapshot authority")
        g=jsonfile(file)
        output[scope]=verify_legacy(g,Path(snap),expected,scope)
    payload={"status":"PASS","projection_sha256":{n:sha256(Path(args.projection)/(n+".json"))
             for n in output if output[n]["status"]!="NOT_RUN_UNAVAILABLE"},
             "cases_sha256":sha256(args.cases),"results":output}
    Path(args.output).write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    print(json.dumps(payload,sort_keys=True))

if __name__=="__main__":main()
