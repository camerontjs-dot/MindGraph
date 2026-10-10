#!/usr/bin/env python3
"""Measure actual MindGraph #46 CLI payload on all #54 frozen query texts.

No semantic answer score, no model, no index change. Source/provenance identity
parity and serialized wire bytes only. All private content stays local.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for piece in iter(lambda:f.read(1<<20),b""):h.update(piece)
    return h.hexdigest()

def new(path,item):
    with Path(path).open("x",encoding="utf-8") as f:
        json.dump(item,f,sort_keys=True,indent=2)
        f.write("\n")

def cli_call(python,repo,source,scope,query,mode,top_k,env):
    fixed=[python,"-c","from mindgraph.cli import app; app()",
           "query",query,"--db",str(source),"--lexical-only",
           "--no-intent","--top-k",str(top_k),"--json"]
    if mode in ("compact","identity"):
        fixed += ["--envelope","--nominations","--nomination-scope",scope]
    if mode=="identity":
        fixed += ["--identity-envelope"]
    p=subprocess.run(fixed,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                     cwd=str(repo),env=env,timeout=30)
    try:data=json.loads(p.stdout)
    except (ValueError,UnicodeDecodeError):data=None
    return {"ok":p.returncode==0,"returncode":p.returncode,
            "wire_bytes":len(p.stdout),
            "stdout_sha256":hashlib.sha256(p.stdout).hexdigest(),
            "stderr_sha256":hashlib.sha256(p.stderr).hexdigest(),
            "stderr_summary":p.stderr.decode("utf-8","replace")[:180],
            "data":data,"stdout":p.stdout.decode("utf-8","replace")}

def check_parity(a,b):
    if not a["ok"] or not b["ok"]:return {"pass":False,"kind":"one_mode_failed"}
    if not isinstance(a["data"],list) or not isinstance(b["data"],dict):
        return {"pass":False,"kind":"schema_shape"}
    raw=a["data"];rows=b["data"].get("nominations")
    if not isinstance(rows,list):return {"pass":False,"kind":"nomination_array_missing"}
    if len(rows)!=len(raw):return {"pass":False,"kind":"count"}
    for i,(x,y) in enumerate(zip(raw,rows)):
        for key in ("doc_id","chunk_index","citation_class","signal"):
            if x.get(key)!=y.get(key):
                return {"pass":False,"kind":f"row_{i}_{key}"}
        if x.get("content_hash") != y.get("content_hash"):
            return {"pass":False,"kind":f"row_{i}_hash"}
        if not isinstance(y.get("expansion_handle"),str) or not y["expansion_handle"]:
            return {"pass":False,"kind":f"row_{i}_no_expansion"}
    return {"pass":True,"kind":"exact_nomination_source_identity_and_order"}

def measure(args):
    directory=Path(args.folder).resolve(strict=True)
    with Path(args.freeze).open("r",encoding="utf-8") as f:fr=json.load(f)
    if digest(__file__)!=fr["script_sha256"]:raise RuntimeError("source script changed")
    casesfile=Path(args.cases).resolve(strict=True)
    if digest(casesfile)!=fr["cases_sha256"]:raise RuntimeError("cases changed")
    repo=directory/"source-candidate46"
    if digest(directory/"candidate46.source.tar")!=fr["product_archive_sha256"]:
        raise RuntimeError("source package drift")
    indexes={}
    for name in ("knowledge","projects","operations"):
        file=directory/f"snapshot.{name}.sqlite"
        if digest(file)!=fr["index_sha256"][name]:raise RuntimeError("index drift: "+name)
        indexes[name]=file
    cases=json.loads(casesfile.read_text())
    manifest=[]
    for name in ("knowledge","projects"):
        for row in cases[name]:
            manifest.append({"scope":name,"id":row["public_id"],
                             "query":row["query"],"class":row["class"],
                             "negative":row["negative"],
                             "eligible_gold_in_index":len(row["index_present_gold"])})
    # Operations adds one declared smoke question, not a golden retrieval case.
    manifest.append({"scope":"operations","id":"O_SMOKE","query":"operations",
                     "class":"unlabelled_smoke","negative":False,
                     "eligible_gold_in_index":None})
    if [x["id"] for x in manifest]!=fr["case_order"]:
        raise RuntimeError("case order differs from preregistration")
    output=Path(args.out).resolve()
    if output.exists():raise FileExistsError("NO_OVERWRITE")
    output.mkdir(parents=True)
    env=dict(os.environ)
    env.update({"PYTHONPATH":str(repo/"src"),"HF_HUB_OFFLINE":"1",
                "TRANSFORMERS_OFFLINE":"1","PYTHONDONTWRITEBYTECODE":"1"})
    details=[];sizes=defaultdict(lambda:Counter());parities=Counter()
    first_nonempty={}
    for i,case in enumerate(manifest):
        scope=case["scope"];query=case["query"];path=indexes[scope]
        a=cli_call(args.python,repo,path,scope,query,"legacy",fr["top_k"],env)
        b=cli_call(args.python,repo,path,scope,query,"compact",fr["top_k"],env)
        parity=check_parity(a,b)
        parities[parity["kind"]]+=1
        base=a["data"] if isinstance(a["data"],list) else []
        noms=b["data"].get("nominations",[]) if isinstance(b["data"],dict) else []
        source_a=sum(len(x.get("chunk_text") or "") for x in base)
        source_b=sum(len(x.get("preview") or "") for x in noms)
        result={"scope":scope,"id":case["id"],"class":case["class"],
                "legacy":{"returncode":a["returncode"],"wire_bytes":a["wire_bytes"],
                          "stdout_sha256":a["stdout_sha256"],"result_count":len(base),
                          "source_chars":source_a,
                          "whitespace_tokens_wire":len(a["stdout"].split())},
                "compact":{"returncode":b["returncode"],"wire_bytes":b["wire_bytes"],
                          "stdout_sha256":b["stdout_sha256"],"result_count":len(noms),
                          "source_preview_chars":source_b,
                          "whitespace_tokens_wire":len(b["stdout"].split())},
                "parity":parity}
        details.append(result)
        for key,src in (("legacy",a),("compact",b)):
            sizes[scope][key+"_wire_bytes"]+=src["wire_bytes"]
        sizes[scope]["legacy_source_chars"]+=source_a
        sizes[scope]["compact_preview_chars"]+=source_b
        sizes[scope]["queries"]+=1
        sizes[scope]["nonempty_queries"]+=int(bool(base))
        if scope not in first_nonempty and noms:
            first_nonempty[scope]={"case_id":case["id"],"nomination":noms[0]}
        for name,raw in (("legacy",a),("compact",b)):
            fname=output/f"{i:02d}-{case['id']}-{name}.private.json"
            if not raw["ok"]:
                new(fname,{"returncode":raw["returncode"],
                           "stderr":raw["stderr_summary"]})
            else:
                # Preserve original output exactly in private evidence, while
                # permitting future independent byte comparisons.
                with fname.open("x",encoding="utf-8") as fp:fp.write(raw["stdout"])
    # Separate producer-identity and selected-handle tests. A source-query
    # failure does not silently fall back to an unbound authority.
    identity={}
    expansion={}
    for name,path in indexes.items():
        request=next(x["query"] for x in manifest if x["scope"]==name)
        ident=cli_call(args.python,repo,path,name,request,"identity",fr["top_k"],env)
        identity[name]={"returncode":ident["returncode"],
                        "envelope_present":ident["ok"] and isinstance(ident["data"],dict)
                        and bool(ident["data"].get("database_identity")),
                        "stdout_sha256":ident["stdout_sha256"],
                        "stderr_sha256":ident["stderr_sha256"]}
        source=first_nonempty.get(name)
        if not source:
            expansion[name]={"status":"NOT_RUN_NO_NOMINATION"}
            continue
        handle=source["nomination"]["expansion_handle"]
        call=[args.python,"-c","from mindgraph.cli import app; app()",
              "expand-nomination",handle,"--db",str(path),
              "--scope",name,"--json"]
        p=subprocess.run(call,cwd=str(repo),env=env,capture_output=True,timeout=25)
        q=subprocess.run([*call[:4],"bad-scope",*call[4:]],cwd=str(repo),env=env,
                         capture_output=True,timeout=25) if False else None
        # Explicit malformed and foreign caller-scope tests are run separately.
        wrong=[args.python,"-c","from mindgraph.cli import app; app()",
               "expand-nomination",handle,"--db",str(path),
               "--scope","invalid-foreign-scope","--json"]
        w=subprocess.run(wrong,cwd=str(repo),env=env,capture_output=True,timeout=25)
        fake=[args.python,"-c","from mindgraph.cli import app; app()",
               "expand-nomination","exp2:tampered","--db",str(path),
               "--scope",name,"--json"]
        z=subprocess.run(fake,cwd=str(repo),env=env,capture_output=True,timeout=25)
        try:expanded=json.loads(p.stdout)
        except (ValueError,UnicodeDecodeError):expanded=None
        if isinstance(expanded,dict):
            body=expanded.get("expansion",expanded)
        else:body={}
        selected=source["nomination"]
        source_equal=(p.returncode==0 and isinstance(body,dict)
                      and body.get("doc_id")==selected.get("doc_id")
                      and body.get("chunk_index")==selected.get("chunk_index")
                      and body.get("content_hash")==selected.get("content_hash"))
        expansion[name]={"selected_case_id":source["case_id"],
                          "returncode":p.returncode,
                          "source_identity_parity":source_equal,
                          "wire_bytes":len(p.stdout),
                          "wrong_scope_refused":w.returncode!=0 and not w.stdout,
                          "malformed_handle_refused":z.returncode!=0 and not z.stdout,
                          "stdout_sha256":hashlib.sha256(p.stdout).hexdigest()}
        with (output/f"expand-{name}.private.json").open("x") as fp:
            fp.write(p.stdout.decode("utf-8","replace"))
    raw={"cohort":manifest,"cases":details,"identity":identity,
         "selected_expansion":expansion}
    private=output/"transport.private.json"
    new(private,raw)
    summary={"status":"TRANSPORT_CHARACTERIZED_WITH_BOUNDS",
             "freeze_sha256":digest(args.freeze),
             "private_result_sha256":digest(private),
             "queries_total":len(details),
             "cohort_by_scope":{k:dict(v) for k,v in sizes.items()},
             "parity_outcomes":dict(parities),
             "required_producer_identity":identity,
             "selected_expansion":expansion,
             "nonclaims":["wire bytes are not destination model tokens",
                          "source selection after language model evaluation is NOT_RUN",
                          "historical query gold is not independent current authority",
                          "compact modes do not automatically make a consumer admit less context",
                          "per-query raw material retained in local private directory"]}
    for scope,v in sizes.items():
        a=v["legacy_wire_bytes"];b=v["compact_wire_bytes"]
        summary["cohort_by_scope"][scope]["compact_minus_legacy_bytes"]=b-a
        summary["cohort_by_scope"][scope]["compact_to_legacy_wire_ratio"]=round(b/a,5) if a else None
    new(output/"transport.public.json",summary)
    for name,p in indexes.items():
        if digest(p)!=fr["index_sha256"][name]:raise RuntimeError("post index drift:"+name)
    print(json.dumps(summary,sort_keys=True))

def synthetic():
    a={"ok":True,"data":[{"doc_id":"x","chunk_index":2,"citation_class":"citable",
                          "signal":"lexical","content_hash":"one"}]}
    b={"ok":True,"data":{"nominations":[{"doc_id":"x","chunk_index":2,
         "citation_class":"citable","signal":"lexical",
         "content_hash":"one","expansion_handle":"exp2:test"}]}}
    assert check_parity(a,b)["pass"]
    changed=json.loads(json.dumps(b))
    changed["data"]["nominations"][0]["content_hash"]="different"
    assert not check_parity(a,changed)["pass"]
    changed=json.loads(json.dumps(b))
    changed["data"]["nominations"][0]["citation_class"]="not_citable"
    assert not check_parity(a,changed)["pass"]
    changed=json.loads(json.dumps(b))
    changed["data"]["nominations"][0].pop("expansion_handle")
    assert not check_parity(a,changed)["pass"]
    print("SYNTHETIC_PRETEST_PASS: exact IDs/order, citation, source hash, handle")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("mode",choices=["selftest","analyze"])
    for key in ("folder","freeze","cases","out","python"):
        p.add_argument("--"+key)
    a=p.parse_args()
    if a.mode=="selftest":synthetic();return
    if not all([a.folder,a.freeze,a.cases,a.out,a.python]):p.error("missing paths")
    measure(a)
if __name__=="__main__":main()
