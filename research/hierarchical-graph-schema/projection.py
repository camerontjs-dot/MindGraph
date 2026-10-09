#!/usr/bin/env python3
"""Experimental MainFrame hierarchical graph v0. Run only on frozen inputs.

Research #58. Structural observations are not qualification or current authority.
No working-tree files are read; versioned source comes from exact Git objects.
"""
import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import sys

VERSION="mainframe-graph-research-v0.3"
ELIGIBLE_EXT={".py",".swift",".md",".toml",".json",".yaml",".yml",".sh"}
EXCLUDED_DIRS={"node_modules",".git",".venv","__pycache__",".build","build","dist","outputs","derived","private-evidence"}
EXCLUDED_TOKENS={".env","secrets","credentials","secret","private_key"}
PREDICATES={"CONTAINS","DECLARES","IMPORTS_MODULE","LINKS_TO","EVIDENCED_BY","SUPERSEDED_BY"}
PROVENANCE={"observed_structure","source_asserted","reviewed_coded","derived_nomination"}

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=True)

def identity(prefix,*parts):
    return prefix+":"+hashlib.sha256("\0".join(map(str,parts)).encode()).hexdigest()

def validate_path(raw):
    if not isinstance(raw,str) or not raw or raw.startswith(("/","~")) or "\\" in raw:
        raise ValueError("unsafe path")
    p=PurePosixPath(raw)
    if ".." in p.parts or "." in p.parts or str(p)!=raw or any(not bit for bit in p.parts):
        raise ValueError("noncanonical path")
    return str(p)

def allowed_file(path,mode):
    validate_path(path)
    parts=PurePosixPath(path).parts
    if any(x in EXCLUDED_DIRS for x in parts):return False
    if any(x.lower() in EXCLUDED_TOKENS or x.lower().endswith((".pem",".key",".p12")) for x in parts):return False
    if mode not in ("100644","100755"):return False
    if Path(path).suffix.lower() not in ELIGIBLE_EXT:return False
    return True

def git(repo,*cmd,raw=False):
    r=subprocess.run(["git","-C",str(repo),*cmd],capture_output=True,check=True,timeout=60)
    return r.stdout if raw else r.stdout.decode().strip()

def load_tree(repo,commit,expected_tree):
    if not re.fullmatch(r"[0-9a-f]{40}",commit):raise ValueError("exact git SHA required")
    tree=git(repo,"rev-parse",commit+"^{tree}")
    if tree!=expected_tree:raise ValueError("Git tree identity mismatch")
    rows=git(repo,"ls-tree","-r","-z",commit,raw=True).split(b"\0")
    files=[]
    skips=Counter()
    for entry in rows:
        if not entry:continue
        meta,path=entry.split(b"\t",1)
        mode,kind,blob=meta.decode("ascii").split(" ")
        path=path.decode("utf-8")
        if not allowed_file(path,mode):
            skips["out_of_profile_or_unsafe"]+=1
            continue
        if kind!="blob":raise ValueError("tracked non-blob under allowed mode")
        files.append({"path":path,"mode":mode,"blob":blob})
    return sorted(files,key=lambda d:d["path"]),dict(skips)

def build_project(repo_dir,repo_name,scope,commit,tree):
    files,skips=load_tree(repo_dir,commit,tree)
    nodes={};edges={};sources={};diag=Counter(skips)
    def node(kind,locator,source_id,meta=None,access="allowed",citation="citable"):
        node_id=identity("node",scope,commit,kind,locator)
        item={"id":node_id,"scope":scope,"kind":kind,"locator":locator,
              "source_id":source_id,"access":access,"citation_class":citation,
              "as_of_commit":commit,"attrs":meta or {}}
        prior=nodes.setdefault(node_id,item)
        if prior!=item:raise ValueError("node hash collision")
        return node_id
    root_source=identity("source",repo_name,commit,tree)
    sources[root_source]={"id":root_source,"kind":"git_tree","repo":repo_name,"commit":commit,
                          "tree":tree,"access":"allowed","citation_class":"citable"}
    root=node("project",".",root_source,{"repo":repo_name,"tree":tree})
    directories={"":root}
    def add_edge(source,target,predicate,proof,provenance="observed_structure",line=None):
        if predicate not in PREDICATES or provenance not in PROVENANCE:
            raise ValueError("unsupported relation or provenance")
        eid=identity("edge",scope,source,predicate,target,proof,line)
        e={"id":eid,"source":source,"target":target,"predicate":predicate,
           "evidence_source_id":proof,"provenance":provenance,"line":line,
           "direction":"forward"}
        if eid in edges and edges[eid]!=e:raise ValueError("edge collision")
        edges[eid]=e
    blobs={}
    for f in files:
        path=f["path"]
        sid=identity("source",repo_name,commit,path,f["blob"])
        sources[sid]={"id":sid,"kind":"git_blob","repo":repo_name,"commit":commit,
                      "blob":f["blob"],"path":path,"access":"allowed","citation_class":"citable"}
        blobs[path]=(f,sid)
        parent=""
        bits=path.split("/")
        for i,part in enumerate(bits[:-1]):
            partial="/".join(bits[:i+1])
            if partial not in directories:
                directory=node("directory",partial,root_source,{"parent":parent})
                add_edge(directories[parent],directory,"CONTAINS",root_source)
                directories[partial]=directory
            parent=partial
        file_node=node("file",path,sid,{"language":Path(path).suffix.lower().lstrip(".")})
        add_edge(directories[parent],file_node,"CONTAINS",sid)
    module_paths={}
    for path in blobs:
        if path.startswith("src/") and path.endswith(".py"):
            name=path[4:-3].replace("/",".")
            if name.endswith(".__init__"):name=name[:-9]
            module_paths[name]=path
    for path,(file_meta,source_id) in blobs.items():
        if not path.endswith(".py"):continue
        byte_data=git(repo_dir,"show",commit+":"+path,raw=True)
        if hashlib.sha1((f"blob {len(byte_data)}\0").encode()+byte_data).hexdigest()!=file_meta["blob"]:
            raise ValueError("Git blob source mismatch: "+path)
        try:tree_ast=ast.parse(byte_data.decode("utf-8"),filename=path)
        except (SyntaxError,UnicodeDecodeError) as exc:
            diag["python_unparsed"]+=1
            continue
        owner=node("file",path,source_id,{"language":"py"})
        for item in tree_ast.body:
            if isinstance(item,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                kind="class" if isinstance(item,ast.ClassDef) else "function"
                symbol=node("symbol",path+"::"+item.name+"@"+str(item.lineno),
                            source_id,{"name":item.name,"symbol_kind":kind,
                                       "start_line":item.lineno})
                add_edge(owner,symbol,"DECLARES",source_id,line=item.lineno)
                diag["python_top_level_declarations"]+=1
            if isinstance(item,(ast.Import,ast.ImportFrom)):
                possible=[]
                if isinstance(item,ast.Import):
                    possible=[alias.name for alias in item.names]
                elif item.level==0:
                    base=item.module or ""
                    possible=[base+"."+alias.name for alias in item.names] if base else []
                    if base:possible.append(base)
                else:
                    diag["unsupported_relative_imports"]+=1
                    continue
                targets=sorted(set(module_paths[n] for n in possible if n in module_paths))
                if not targets:
                    diag["imports_without_pinned_module_target"]+=1
                    continue
                for target_path in targets:
                    target=node("file",target_path,blobs[target_path][1],{"language":"py"})
                    add_edge(owner,target,"IMPORTS_MODULE",source_id,line=item.lineno)
                    diag["imports_resolved"]+=1
    diag["unsupported_swift_symbol_files"]=sum(f["path"].endswith(".swift") for f in files)
    out={"schema":VERSION,"profile":"project_code",
         "scope":scope,"authority":{"kind":"git_commit","repo":repo_name,"commit":commit,"tree":tree},
         "nodes":sorted(nodes.values(),key=lambda n:n["id"]),
         "edges":sorted(edges.values(),key=lambda e:e["id"]),
         "sources":sorted(sources.values(),key=lambda s:s["id"]),
         "diagnostics":dict(sorted(diag.items()))}
    validate(out)
    return out

def build_legacy_index(snapshot,scope,digest):
    """Profile-preserving legacy projection; old links are nominations only."""
    with sqlite3.connect("file:"+str(Path(snapshot).resolve())+"?mode=ro",uri=True) as c:
        docs=c.execute("SELECT id,path,content_hash,trust_profile FROM documents ORDER BY id").fetchall()
        raw_edges=c.execute("SELECT source_id,target_id,relationship_type FROM edges ORDER BY source_id,target_id").fetchall()
    nodes={};sources={};edges=[];unresolved=0
    for doc_id,path,content_hash,trust in docs:
        sid=identity("source",scope,digest,doc_id,content_hash)
        sources[sid]={"id":sid,"kind":"indexed_document","snapshot_sha256":digest,
                      "content_hash":content_hash,"indexed_doc_id":doc_id,
                      "access":"unknown","citation_class":"unverified"}
        nid=identity("node",scope,digest,doc_id)
        nodes[doc_id]=nid
    rows=[]
    for doc_id,path,content_hash,trust in docs:
        rows.append({"id":nodes[doc_id],"scope":scope,"kind":"note" if scope=="knowledge" else "operational_record",
                     "locator":path,"source_id":identity("source",scope,digest,doc_id,content_hash),
                     "access":"unknown","citation_class":"unverified",
                     "as_of_commit":None,"attrs":{"trust_profile":trust}})
    for src,tgt,rel in raw_edges:
        if src not in nodes or tgt not in nodes:
            unresolved+=1;continue
        sid=next(n["source_id"] for n in rows if n["id"]==nodes[src])
        edges.append({"id":identity("edge",scope,src,tgt,rel),
                      "source":nodes[src],"target":nodes[tgt],
                      "predicate":"LINKS_TO","provenance":"derived_nomination",
                      "evidence_source_id":sid,"line":None,"direction":"forward"})
    out={"schema":VERSION,"profile":"knowledge_notes" if scope=="knowledge" else "operations_history",
         "scope":scope,"authority":{"kind":"sqlite_snapshot","sha256":digest},
         "nodes":sorted(rows,key=lambda n:n["id"]),
         "edges":sorted(edges,key=lambda e:e["id"]),
         "sources":sorted(sources.values(),key=lambda s:s["id"]),
         "diagnostics":{"unresolved_legacy_edges_not_represented":unresolved,
                        "legacy_links_are_nominations":True}}
    validate(out)
    return out

def validate(graph):
    if graph.get("schema")!=VERSION:raise ValueError("schema identifier invalid")
    n={i["id"]:i for i in graph["nodes"]}
    s={i["id"]:i for i in graph["sources"]}
    if len(n)!=len(graph["nodes"]) or len(s)!=len(graph["sources"]):raise ValueError("duplicate identity")
    ids=set()
    is_federated=graph.get("profile")=="federated_overlay"
    for node in graph["nodes"]:
        if not is_federated and node["scope"]!=graph["scope"]:
            raise ValueError("foreign namespace node")
        if node["source_id"] not in s:raise ValueError("unattested node")
        if node["access"] not in ("allowed","unknown","denied"):raise ValueError("invalid access")
        if node["citation_class"] not in ("citable","unverified","not_citable"):raise ValueError("invalid citation class")
        if node["kind"]=="file":validate_path(node["locator"])
    for edge in graph["edges"]:
        if edge["id"] in ids:raise ValueError("duplicate edge identity")
        ids.add(edge["id"])
        if edge["source"] not in n or edge["target"] not in n:raise ValueError("dangling edge not admitted")
        if edge["evidence_source_id"] not in s:raise ValueError("missing edge witness")
        if edge["predicate"] not in PREDICATES or edge["provenance"] not in PROVENANCE:
            raise ValueError("unknown edge type/provenance")
        if edge["direction"]!="forward":raise ValueError("reverse assertion invalid")
        if n[edge["source"]]["scope"]!=n[edge["target"]]["scope"]:
            if not (is_federated and edge.get("scope_bridge")=="explicit_reference"
                    and edge["provenance"] in ("source_asserted","reviewed_coded")
                    and edge["predicate"] in ("LINKS_TO","EVIDENCED_BY")):
                raise ValueError("implicit cross-scope relation")
        if edge["predicate"]=="CONTAINS":
            if n[edge["source"]]["kind"] not in ("project","directory") or n[edge["target"]]["kind"] not in ("directory","file"):
                raise ValueError("invalid containment topology")
        if edge["predicate"]=="DECLARES" and (n[edge["source"]]["kind"]!="file" or n[edge["target"]]["kind"]!="symbol"):
            raise ValueError("unsupported declaration topology")
        if edge["predicate"]=="IMPORTS_MODULE" and (n[edge["source"]]["kind"]!="file" or n[edge["target"]]["kind"]!="file"):
            raise ValueError("unsupported import topology")
        if edge["provenance"]=="derived_nomination" and edge["predicate"] not in ("LINKS_TO",):
            raise ValueError("nomination promoted to typed structural fact")
    from source_bindings import validate_bindings
    validate_bindings(graph,n,s,identity,validate_path)
    return True

def write(path,obj):
    path=Path(path)
    if path.exists():raise FileExistsError("NO_OVERWRITE "+str(path))
    path.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n",encoding="utf-8")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mindgraph-repo",required=True)
    p.add_argument("--conduit-repo",required=True)
    p.add_argument("--cases",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--knowledge-snapshot")
    p.add_argument("--operations-snapshot")
    p.add_argument("--knowledge-sha256")
    p.add_argument("--operations-sha256")
    args=p.parse_args()
    target=Path(args.out).resolve()
    if target.exists():raise FileExistsError("NO_OVERWRITE "+str(target))
    gold=json.loads(Path(args.cases).read_text())
    target.mkdir(parents=True)
    mapping={"mindgraph":Path(args.mindgraph_repo),"conduit":Path(args.conduit_repo)}
    manifest={"schema":VERSION,"pinned_cases_sha256":hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),"scopes":{}}
    for name,repo in mapping.items():
        d=gold["git_versions"][name]
        graph=build_project(repo,d["repo"],"project:"+name,d["commit"],d["tree"])
        file=target/(name+".json");write(file,graph)
        manifest["scopes"][name]={"sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
                                  "nodes":len(graph["nodes"]),"edges":len(graph["edges"]),
                                  "sources":len(graph["sources"]),"diagnostics":graph["diagnostics"]}
    for name in ("knowledge","operations"):
        snap=getattr(args,name+"_snapshot");expected=getattr(args,name+"_sha256")
        if not snap:continue
        if not expected or not re.fullmatch("[0-9a-f]{64}",expected):
            raise ValueError("snapshot requires expected sha256")
        h=hashlib.sha256(Path(snap).read_bytes()).hexdigest()
        if h!=expected:raise ValueError(name+" snapshot differs from preregistered hash")
        graph=build_legacy_index(snap,name,h)
        file=target/(name+".json");write(file,graph)
        manifest["scopes"][name]={"sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
                                 "nodes":len(graph["nodes"]),"edges":len(graph["edges"]),
                                 "diagnostics":graph["diagnostics"]}
    write(target/"manifest.json",manifest)
    print(json.dumps(manifest,sort_keys=True))

if __name__=="__main__":main()
