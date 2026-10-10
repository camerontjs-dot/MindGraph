#!/usr/bin/env python3
"""Separate formula verifier of lossless columnar transport result.

No dependency on the packer. Checks every recorded raw compact envelope
and packed row, size, order, field identity and frozen hashes.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import sys

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for x in iter(lambda:f.read(1<<20),b""):h.update(x)
    return h.hexdigest()

def load(path):return json.loads(Path(path).read_text())

def minbytes(obj):
    return len(json.dumps(obj,ensure_ascii=False,separators=(",",":")).encode())

def reconstruct(p):
    if set(p)!={"schema","fields","rows","original_envelope"}:raise ValueError("wrong envelope")
    if p["schema"]!="nomination-column-transport-research-v0":raise ValueError("wrong version")
    keys=p["fields"]
    if keys!=sorted(set(keys)):raise ValueError("unqualified column list")
    if not isinstance(p["rows"],list):raise ValueError("non-list rows")
    if not isinstance(p["original_envelope"],dict):raise ValueError("no envelope")
    if "nominations" in p["original_envelope"]:raise ValueError("duplicate nominations")
    rows=[]
    for row in p["rows"]:
        if not isinstance(row,list) or len(row)!=len(keys):raise ValueError("bad row length")
        rows.append(dict(zip(keys,row)))
    return {**p["original_envelope"],"nominations":rows}

def verify(root):
    freeze=load(root/"PACK-FREEZE.json")
    resultdir=root/"pack-result-rc0"
    public=load(resultdir/"packed.public.json")
    raw=load(resultdir/"packed.private.json")
    transport=root/"transport-result-rc0"
    assert sha(root/"PACK-FREEZE.json")==public["freeze_sha256"]
    assert sha(root/"lossless_pack_audit.py")==freeze["script_sha256"]
    assert sha(transport/"transport.private.json")==freeze["transport_private_sha256"]
    assert sha(resultdir/"packed.private.json")==public["private_result_sha256"]
    original=load(transport/"transport.private.json")["cases"]
    assert len(original)==len(raw["cases"])==public["cohort_size"]==35
    assert [x["id"] for x in original]==freeze["case_order"]
    agg=defaultdict(Counter)
    for i,(original_case,packed_case) in enumerate(zip(original,raw["cases"])):
        scope=original_case["scope"];cid=original_case["id"]
        assert cid==packed_case["id"] and scope==packed_case["scope"]
        packfile=resultdir/f"packed-{i:02d}-{cid}.private.json"
        source=transport/f"{i:02d}-{cid}-compact.private.json"
        baseline=transport/f"{i:02d}-{cid}-legacy.private.json"
        assert sha(packfile)==packed_case["packed_sha256"]
        assert sha(source)==packed_case["source_nomination_sha256"]
        packed=load(packfile)
        current=load(source)
        decoded=reconstruct(packed)
        assert decoded==current,(scope,cid,"roundtrip changed")
        size=minbytes(packed)
        assert size==packed_case["packed_minified_bytes"]
        agg[scope]["cases"]+=1
        agg[scope]["nominations"]+=len(current["nominations"])
        agg[scope]["packed_minified_bytes"]+=size
        agg[scope]["compact_minified_bytes"]+=minbytes(current)
        agg[scope]["legacy_minified_bytes"]+=minbytes(load(baseline))
        agg[scope]["compact_pretty_bytes"]+=source.stat().st_size
        agg[scope]["legacy_pretty_bytes"]+=baseline.stat().st_size
    for scope,c in agg.items():
        report=public["by_scope"][scope]
        for metric,v in c.items():
            assert report[metric]==v,(scope,metric,report[metric],v)
        assert c["packed_minified_bytes"]<c["legacy_minified_bytes"]
    # Calibrated negative controls on the actual first packed object:
    first=load(resultdir/f"packed-00-{original[0]['id']}.private.json")
    mut=json.loads(json.dumps(first))
    mut["fields"].append(mut["fields"][-1])
    try:reconstruct(mut)
    except ValueError:pass
    else:raise AssertionError("duplicate fields accepted")
    mut=json.loads(json.dumps(first))
    mut["rows"][0][mut["fields"].index("doc_id")]="forged-id"
    assert reconstruct(mut)!=load(transport/f"00-{original[0]['id']}-compact.private.json")
    return {"status":"VERIFY_PASS","cases":len(original),"scope_results":{k:dict(v) for k,v in agg.items()},
            "negative_duplicate_columns_rejected":True,"negative_source_mutation_detected":True}

if __name__=="__main__":
    if len(sys.argv)!=2:raise SystemExit("usage: verify_packing.py PRIVATE_VNEXT_DIR")
    root=Path(sys.argv[1]).resolve(strict=True)
    result=verify(root)
    with (root/"PACK-VERIFIED.json").open("x") as f:
        json.dump(result,f,indent=2,sort_keys=True)
    print(json.dumps(result,sort_keys=True))
