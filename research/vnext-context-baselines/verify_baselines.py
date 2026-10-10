#!/usr/bin/env python3
"""Independent-formula mechanical verifier of the two frozen vNext RC0 outputs.

Does not import the research implementation scripts. Recounts indexes and raw
CLI records directly. Does not qualify semantic labels, model actors or release.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for x in iter(lambda:f.read(1<<20),b""):h.update(x)
    return h.hexdigest()

def load(path):
    return json.loads(Path(path).read_text())

def check_link(root):
    freeze=root/"LINK-FREEZE.json"
    public=root/"link-result-rc0/resolution.public.json"
    private=root/"link-result-rc0/resolution.private.json"
    f=load(freeze);pub=load(public);raw=load(private)
    assert digest(freeze)==pub["freeze_sha256"]
    assert digest(private)==pub["private_receipt_sha256"]
    assert digest(root/"link_resolution_audit.py")==f["script_sha256"]
    summary={}
    for scope in ("knowledge","projects","operations"):
        source=root/f"snapshot.{scope}.sqlite"
        assert digest(source)==f["input_sha256"][scope]
        c=sqlite3.connect("file:"+str(source)+"?mode=ro",uri=True)
        docs={v[0] for v in c.execute("SELECT id FROM documents")}
        edges=c.execute("SELECT source_id,target_id,relationship_type FROM edges").fetchall()
        c.close()
        missing=sum(t not in docs for _,t,_ in edges)
        r=pub["scopes"][scope]
        assert r["stored_edges"]==len(edges)
        assert r["stored_edges_missing_target"]==missing
        assert r["stored_edges_resolving_in_scope"]==len(edges)-missing
        assert r["matched_edge_tuples"]<=len(edges)
        assert r["matched_edge_tuples"]+r["stored_only_tuples"]==len(edges)
        assert r["matched_edge_tuples"]+r["reconstructed_only_tuples"]==r["reconstructed_edge_tuples"]
        assert sum(r["missing_target_categories"].values())==missing
        items=raw["scopes"][scope]["unresolved_edges"]
        assert len(items)==missing
        assert Counter(x["category"] for x in items)==Counter(r["missing_target_categories"])
        summary[scope]={"stored":len(edges),"missing_target":missing,
                        "unattributed":r["missing_target_unattributed_edge_count"],
                        "reconstructed_exact":r["stored_only_tuples"]==0 and r["reconstructed_only_tuples"]==0}
    return summary

def check_transport(root,casesdir):
    freeze=root/"TRANSPORT-FREEZE.json";f=load(freeze)
    folder=root/"transport-result-rc0"
    public=folder/"transport.public.json";private=folder/"transport.private.json"
    pub=load(public);priv=load(private)
    assert digest(freeze)==pub["freeze_sha256"]
    assert digest(private)==pub["private_result_sha256"]
    assert digest(root/"compact_transport_audit.py")==f["script_sha256"]
    assert digest(casesdir)==f["cases_sha256"]
    assert len(priv["cases"])==len(f["case_order"])==pub["queries_total"]==35
    assert [x["id"] for x in priv["cases"]]==f["case_order"]
    observed=defaultdict(Counter)
    outcome=Counter()
    for i,x in enumerate(priv["cases"]):
        scope=x["scope"]
        key=x["id"]
        a=folder/f"{i:02d}-{key}-legacy.private.json"
        b=folder/f"{i:02d}-{key}-compact.private.json"
        assert digest(a)==x["legacy"]["stdout_sha256"]
        assert digest(b)==x["compact"]["stdout_sha256"]
        raw=json.loads(a.read_text());compact=json.loads(b.read_text())
        assert isinstance(raw,list)
        nom=compact["nominations"]
        assert len(nom)==len(raw)
        for item,view in zip(raw,nom):
            for field in ("doc_id","chunk_index","signal","citation_class","content_hash"):
                assert item.get(field)==view.get(field)
            assert isinstance(view.get("expansion_handle"),str)
        assert x["parity"]["pass"] is True
        outcome[x["parity"]["kind"]]+=1
        observed[scope]["queries"]+=1
        observed[scope]["legacy_wire_bytes"]+=a.stat().st_size
        observed[scope]["compact_wire_bytes"]+=b.stat().st_size
        observed[scope]["legacy_source_chars"]+=sum(len(row.get("chunk_text") or "") for row in raw)
        observed[scope]["compact_preview_chars"]+=sum(len(row.get("preview") or "") for row in nom)
        observed[scope]["nonempty_queries"]+=int(bool(raw))
    assert dict(outcome)==pub["parity_outcomes"]
    for scope,counters in observed.items():
        reported=pub["cohort_by_scope"][scope]
        for k,count in counters.items():
            assert reported[k]==count,(scope,k,reported[k],count)
        assert reported["compact_minus_legacy_bytes"]==reported["compact_wire_bytes"]-reported["legacy_wire_bytes"]
    expansions={}
    for scope,entry in pub["selected_expansion"].items():
        if "returncode" not in entry:continue
        expanded=load(folder/f"expand-{scope}.private.json")
        data=expanded.get("expansion",expanded)
        idx=next(i for i,x in enumerate(priv["cases"]) if x["id"]==entry["selected_case_id"] and x["scope"]==scope)
        compact=load(folder/f"{idx:02d}-{entry['selected_case_id']}-compact.private.json")
        chosen=compact["nominations"][0]
        assert entry["returncode"]==0
        for k in ("doc_id","chunk_index","content_hash","citation_class"):
            assert chosen.get(k)==data.get(k),(scope,k)
        conn=sqlite3.connect("file:"+str(root/f"snapshot.{scope}.sqlite")+"?mode=ro",uri=True)
        db=conn.execute("SELECT text FROM chunks WHERE doc_id=? AND chunk_index=?",
                        (chosen["doc_id"],chosen["chunk_index"])).fetchone()
        conn.close()
        assert db is not None
        assert data["chunk_text"]==db[0]
        assert entry["wrong_scope_refused"] and entry["malformed_handle_refused"]
        expansions[scope]={"exact_index_chunk_redeemed":True,
                           "returned_bytes":entry["wire_bytes"],
                           "wrong_scope_refused":True,"malformed_refused":True}
    return {"queries":len(priv["cases"]),"wire_counts":{k:dict(v) for k,v in observed.items()},
            "selected_expansion":expansions}

def main():
    root=Path(sys.argv[1]).resolve(strict=True)
    cases=Path(sys.argv[2]).resolve(strict=True)
    result={"status":"VERIFY_PASS",
            "source_link_census":check_link(root),
            "transport":check_transport(root,cases)}
    with (root/"VERIFIED-RC0.json").open("x") as f:
        json.dump(result,f,indent=2,sort_keys=True)
        f.write("\n")
    # Public output suppresses source names/query text and detailed file names.
    print(json.dumps(result,sort_keys=True))
if __name__=="__main__":
    if len(sys.argv)!=3:raise SystemExit("usage: verify_baselines.py PRIVATE_RC0_DIR FROZEN_CASES_JSON")
    main()
