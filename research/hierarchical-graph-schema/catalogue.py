#!/usr/bin/env python3
"""A hierarchical directory index over graph views, not a trust federation.

Catalogue pointers are pinned to immutable graph projection bytes. Membership
is structural navigation; it does not create dependency/evidence edges.
"""
import argparse
import hashlib
import json
from pathlib import Path

EXPECTED={"knowledge":"knowledge_notes","operations":"operations_history",
          "mindgraph":"project_code","conduit":"project_code"}

def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def make(projected,manifest_path,cases_path):
    m=json.loads(Path(manifest_path).read_text())
    cases=json.loads(Path(cases_path).read_text())
    if m["pinned_cases_sha256"]!=digest(cases_path):
        raise ValueError("input fixture identity drift")
    views=[]
    for key,profile in EXPECTED.items():
        if key not in m["scopes"]:
            continue  # External private view is unavailable; no synthetic substitute.
        src=Path(projected)/(key+".json")
        graph=json.loads(src.read_text())
        if graph["schema"]!="mainframe-graph-research-v0.3" or graph["profile"]!=profile:
            raise ValueError("unsupported graph schema/profile")
        if digest(src)!=m["scopes"][key]["sha256"]:
            raise ValueError("projection digest mismatch")
        expected_scope="project:"+key if profile=="project_code" else key
        if graph["scope"]!=expected_scope:raise ValueError("scope confusion")
        if profile=="project_code":
            pin=cases["git_versions"][key]
            if (graph["authority"]["commit"]!=pin["commit"] or
                graph["authority"]["tree"]!=pin["tree"] or
                graph["authority"]["repo"]!=pin["repo"]):
                raise ValueError("project pointer points at wrong commit")
        views.append({
            "scope":expected_scope,
            "profile":profile,
            "family":"projects" if profile=="project_code" else key,
            "projection_sha256":digest(src),
            "authority":graph["authority"],
            "read_access":"private_index_authorization_required" if profile!="project_code" else "source_policy_required",
            "link_semantics":"navigation_only",
        })
    if len({x["scope"] for x in views})!=len(views):raise ValueError("scope collision")
    roots={
        "knowledge":[],
        "projects":[],
        "operations":[],
        "evidence":[]  # externally owned; absent until a qualified locator exists
    }
    for item in views:roots[item["family"]].append(item["scope"])
    output={
        "contract":"mainframe-directory-catalogue-v0",
        "basis":{"cases_sha256":digest(cases_path),
                 "projection_manifest_sha256":digest(manifest_path)},
        "directory_roots":[{"directory":k,"catalogued_scopes":sorted(v)}
                           for k,v in sorted(roots.items())],
        "views":sorted(views,key=lambda row:row["scope"]),
        "semantic_edges_between_scopes":[],
        "unqualified_roots":["evidence"]+[k for k in ("knowledge","operations") if not roots[k]],
        "limits":{"full_mainframe_corpus_covered":False,
                  "current_authority_asserted":False,
                  "per_project_database_required":False,
                  "cross_scope_execution_enabled":False},
    }
    return output

def verify_lookup(catalogue,requested_scope,sha):
    matches=[x for x in catalogue["views"] if x["scope"]==requested_scope]
    if len(matches)!=1 or matches[0]["projection_sha256"]!=sha:
        raise ValueError("missing or drifted graph view")
    return matches[0]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--projection",required=True)
    p.add_argument("--manifest",required=True)
    p.add_argument("--cases",required=True)
    p.add_argument("--output",required=True)
    x=p.parse_args()
    if Path(x.output).exists():raise FileExistsError("no overwrite")
    value=make(x.projection,x.manifest,x.cases)
    for view in value["views"]:
        verify_lookup(value,view["scope"],view["projection_sha256"])
    try:verify_lookup(value,"project:nonexistent","0"*64)
    except ValueError:pass
    else:raise ValueError("invented project accepted")
    Path(x.output).write_text(json.dumps(value,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"views":len(value["views"]),"namespaces":list(value["directory_roots"]),
                      "cross_scope_semantic_edges":0,"status":"CATALOGUE_STRUCTURALLY_VERIFIED"},sort_keys=True))
if __name__=="__main__":main()
