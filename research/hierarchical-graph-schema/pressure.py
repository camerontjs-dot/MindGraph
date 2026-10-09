#!/usr/bin/env python3
"""Adversarial source-custody controls for hierarchical graph RC1.

Mutation expectations are independent of the trained/model output; all source
objects are exact frozen Git tree bytes. Actual check calls are two layers:
candidate structural validator and separate pinned-object attestor.
"""
import copy
import os
import hashlib
import json
from pathlib import Path
import projection
import attest

ROOT=Path(__file__).parent
PROJECTION_DIR=Path(os.environ.get("MGRAPH_PROJECTION_DIR",str(ROOT/"projected")))
CASES_FILE=Path(os.environ.get("MGRAPH_CASES_FILE",str(ROOT/"CASES.json")))
OUTPUT=Path(os.environ.get("MGRAPH_PRESSURE_OUTPUT",str(ROOT/"PRESSURE.json")))

def read(path):return json.loads(Path(path).read_text())

def check():
    original=read(PROJECTION_DIR/"mindgraph.json")
    conduit=read(PROJECTION_DIR/"conduit.json")
    records=[]
    def run(name,graph,change,owner="mindgraph",layer="validate"):
        g=copy.deepcopy(graph);change(g)
        try:
            if layer=="validate":
                projection.validate(g)
            else:
                repo=Path(os.environ["MGRAPH_"+owner.upper()+"_REPO"])
                info=read(CASES_FILE)["git_versions"][owner]
                attest.verify_project(g,info,repo)
        except (ValueError,KeyError,AssertionError,IndexError) as e:
            records.append({"control":name,"rejected":True,"layer":layer,
                            "error_type":type(e).__name__})
        else:records.append({"control":name,"rejected":False,"layer":layer})
    def choose(g,pred):
        return next(e for e in g["edges"] if e["predicate"]==pred)
    def wrong_valid_source(g):
        e=choose(g,"DECLARES")
        s=next(s["id"] for s in g["sources"] if s["kind"]=="git_blob"
               and s["id"]!=e["evidence_source_id"])
        e["evidence_source_id"]=s
    run("wrong-but-existing-source",original,wrong_valid_source)
    run("same-source-forged-blob-sha",original,
        lambda g:next(s for s in g["sources"] if s["kind"]=="git_blob").update(blob="0"*40))
    run("existing-witness-no-edge-rekey",original,
        lambda g:choose(g,"IMPORTS_MODULE").update(line=99999))
    run("claim-current-version-with-older-commit",original,
        lambda g:next(n for n in g["nodes"] if n["kind"]=="file").update(as_of_commit="0"*40))
    run("forged-dir-path-escape",original,
        lambda g:next(n for n in g["nodes"] if n["kind"]=="file").update(locator="../../secret"))
    run("invented-cross-project-scope",original,
        lambda g:next(n for n in g["nodes"] if n["kind"]=="file").update(scope="project:conduit"))
    run("unreviewed-call-promoted-to-qualifies",original,
        lambda g:choose(g,"IMPORTS_MODULE").update(predicate="QUALIFIED_BY"))
    run("semantic-nomination-masquerading-as-import",original,
        lambda g:choose(g,"IMPORTS_MODULE").update(provenance="derived_nomination"))
    def fabricated_swift(g):
        f=next(n for n in g["nodes"] if n["kind"]=="file"
               and n["locator"]=="Sources/Conduit/AppModel.swift")
        locator=f["locator"]+"::madeUpFunction@99"
        sid=projection.identity("node",g["scope"],g["authority"]["commit"],"symbol",locator)
        g["nodes"].append({"id":sid,"scope":g["scope"],"kind":"symbol","locator":locator,
                           "source_id":f["source_id"],"access":"allowed",
                           "citation_class":"citable","as_of_commit":g["authority"]["commit"],
                           "attrs":{"name":"madeUpFunction","symbol_kind":"function","start_line":99}})
        e={"source":f["id"],"target":sid,"predicate":"DECLARES",
           "evidence_source_id":f["source_id"],"provenance":"observed_structure",
           "line":99,"direction":"forward"}
        e["id"]=projection.identity("edge",g["scope"],e["source"],e["predicate"],
                                    e["target"],e["evidence_source_id"],e["line"])
        g["edges"].append(e)
    run("forged-Swift-symbol-not-parsed",conduit,fabricated_swift,"conduit","attestor")
    def forged_import(g):
        file={n["locator"]:n for n in g["nodes"] if n["kind"]=="file"}
        e=next(e for e in g["edges"] if e["predicate"]=="IMPORTS_MODULE"
               and e["source"]==file["src/mindgraph/cli.py"]["id"]
               and e["target"]==file["src/mindgraph/query.py"]["id"])
        e["target"]=file["src/mindgraph/mcp_proxy.py"]["id"]
        e["id"]=projection.identity("edge",g["scope"],e["source"],e["predicate"],
                                     e["target"],e["evidence_source_id"],e["line"])
    run("forged-import-at-real-line",original,forged_import,"mindgraph","attestor")
    def bad_access(g):
        sid=next(s["id"] for s in g["sources"] if s["kind"]=="git_blob")
        next(s for s in g["sources"] if s["id"]==sid)["access"]="denied"
    run("denied-source-deceptively-admitted",original,bad_access,"mindgraph","attestor")
    # Check legacy links never become source-attested or current.
    legacy_available=(PROJECTION_DIR/"knowledge.json").exists()
    if legacy_available:
        legacy=read(PROJECTION_DIR/"knowledge.json")
        run("legacy-nomination-upgraded",legacy,
            lambda g:choose(g,"LINKS_TO").update(provenance="source_asserted"))
    passed=sum(x["rejected"] for x in records)
    output={"rejected":passed,"total":len(records),
            "legacy_knowledge_mutation":"PASS" if legacy_available else "NOT_RUN_UNAVAILABLE",
            "status":
            "PASS" if passed==len(records) else "FAILED_PROVENANCE_CONTROL",
            "checks":records}
    dest=OUTPUT
    with dest.open("x") as f:json.dump(output,f,indent=2,sort_keys=True)
    print(json.dumps(output,sort_keys=True))
    return passed==len(records)
if __name__=="__main__":
    raise SystemExit(0 if check() else 1)
