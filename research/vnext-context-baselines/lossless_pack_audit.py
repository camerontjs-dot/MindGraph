#!/usr/bin/env python3
"""Lossless schema-collected nomination transport experiment (not product code).

This reduces repeated JSON member names without dropping any nomination
value. Mechanical wire-byte diagnosis; not an LLM-context efficiency claim.
"""
from __future__ import annotations
from collections import Counter,defaultdict
import argparse
import hashlib
import json
from pathlib import Path

SCHEMA="nomination-column-transport-research-v0"

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for piece in iter(lambda:f.read(1<<20),b""):h.update(piece)
    return h.hexdigest()

def load(p):
    return json.loads(Path(p).read_text())

def dump(obj,pretty=False):
    return (json.dumps(obj,ensure_ascii=False,sort_keys=pretty,indent=2)
            if pretty else json.dumps(obj,ensure_ascii=False,separators=(",",":")))

def write_new(p,obj):
    with Path(p).open("x",encoding="utf8") as f:f.write(dump(obj,pretty=True)+"\n")

def pack(envelope):
    if not isinstance(envelope,dict):raise ValueError("expected envelope")
    original=envelope.get("nominations")
    if not isinstance(original,list):raise ValueError("expected nomination list")
    keys=sorted(original[0]) if original else []
    if not all(isinstance(x,dict) and sorted(x)==keys for x in original):
        raise ValueError("candidate records differ in field sets")
    if len(keys)!=len(set(keys)):raise ValueError("ambiguous source fields")
    return {"schema":SCHEMA,
            "original_envelope":{k:v for k,v in envelope.items() if k!="nominations"},
            "fields":keys,
            "rows":[[obj[k] for k in keys] for obj in original]}

def unpack(packed):
    if not isinstance(packed,dict) or set(packed)!={"schema","original_envelope","fields","rows"}:
        raise ValueError("wrong packed keys")
    if packed["schema"]!=SCHEMA:raise ValueError("wrong schema")
    fields=packed["fields"]
    if not isinstance(fields,list) or any(not isinstance(k,str) for k in fields):
        raise ValueError("invalid fields")
    if sorted(set(fields))!=fields:raise ValueError("noncanonical/doubled fields")
    if not isinstance(packed["rows"],list):raise ValueError("wrong row form")
    if not isinstance(packed["original_envelope"],dict) or "nominations" in packed["original_envelope"]:
        raise ValueError("invalid header")
    rows=[]
    for r in packed["rows"]:
        if not isinstance(r,list) or len(r)!=len(fields):raise ValueError("row arity")
        rows.append(dict(zip(fields,r)))
    return {**packed["original_envelope"],"nominations":rows}

def selftest():
    item={"z":None,"citation_class":"citable","doc_id":"d","expansion_handle":"exp2:e","source_path":None}
    o={"schema_version":"v1","citation_counts":{"citable":1},"nominations":[item,item]}
    x=pack(o)
    assert unpack(x)==o
    def rejects(mut,by_decoder=True):
        variant=load_str(dump(x))
        mut(variant)
        try:
            decoded=unpack(variant)
            if decoded!=o:raise ValueError("roundtrip identity mismatch")
        except ValueError:return
        raise AssertionError("mutant accepted as equivalent")
    rejects(lambda x:x["fields"].append(x["fields"][-1]))
    rejects(lambda x:x["fields"].reverse())
    rejects(lambda x:x["rows"][0].pop())
    rejects(lambda x:x["rows"][1].__setitem__(x["fields"].index("doc_id"),"different"))
    rejects(lambda x:x.pop("original_envelope"))
    print("SELFTEST_PASS: lossless, duplicate, order, arity, altered source ID, missing header")

def load_str(s):
    return json.loads(s)

def analyze(args):
    root=Path(args.folder).resolve(strict=True)
    freeze=load(args.freeze)
    if sha(__file__)!=freeze["script_sha256"]:raise ValueError("CODE_SHA_DRIFT")
    if sha(root/"transport-result-rc0/transport.private.json")!=freeze["transport_private_sha256"]:
        raise ValueError("TRANSPORT_INPUT_DRIFT")
    inputdir=root/"transport-result-rc0"
    original=load(inputdir/"transport.private.json")
    cohort=original["cases"]
    if [x["id"] for x in cohort]!=freeze["case_order"]:raise ValueError("COHORT_ORDER_DRIFT")
    out=Path(args.out)
    if out.exists():raise FileExistsError("NO_OVERWRITE")
    out.mkdir(parents=True)
    info=defaultdict(Counter);field_sets=Counter();allcases=[]
    for i,case in enumerate(cohort):
        scope=case["scope"];key=case["id"]
        source=inputdir/f"{i:02d}-{key}-compact.private.json"
        legacy=inputdir/f"{i:02d}-{key}-legacy.private.json"
        current=load(source);first=load(legacy)
        packed=pack(current)
        roundtrip=unpack(packed)
        if roundtrip!=current:raise RuntimeError("LOSSLESS_FAILED:"+key)
        original_min=len(dump(current).encode())
        legacy_min=len(dump(first).encode())
        packed_min=len(dump(packed).encode())
        packed_pretty=len((dump(packed,pretty=True)+"\n").encode())
        original_pretty=source.stat().st_size
        legacy_pretty=legacy.stat().st_size
        fields=packed["fields"]
        field_sets[hashlib.sha256("\0".join(fields).encode()).hexdigest()]+=1
        info[scope]["cases"]+=1
        info[scope]["legacy_pretty_bytes"]+=legacy_pretty
        info[scope]["legacy_minified_bytes"]+=legacy_min
        info[scope]["compact_pretty_bytes"]+=original_pretty
        info[scope]["compact_minified_bytes"]+=original_min
        info[scope]["packed_pretty_bytes"]+=packed_pretty
        info[scope]["packed_minified_bytes"]+=packed_min
        info[scope]["nominations"]+=len(current["nominations"])
        fname=out/f"packed-{i:02d}-{key}.private.json"
        with fname.open("x",encoding="utf-8") as f:f.write(dump(packed)+"\n")
        allcases.append({"id":key,"scope":scope,"lossless":True,
                         "packed_sha256":sha(fname),
                         "source_nomination_sha256":sha(source),
                         "packed_minified_bytes":packed_min,
                         "legacy_minified_bytes":legacy_min,
                         "compact_minified_bytes":original_min})
    summary={"study":"lossless-columnar-nominations-rc0",
             "status":"LOSSLESS_REPRESENTATION_RECONSTRUCTED_ALL_CASES",
             "freeze_sha256":sha(args.freeze),
             "cohort_size":len(allcases),"field_schema_identity_histogram":dict(field_sets),
             "by_scope":{},"nonclaims":[
                "source equivalence is value-level, not byte-exact original JSON formatting",
                "no model tokenization or agent selector runs",
                "packed arrays may reduce human/model readability",
                "producer binding and expansion authorization remain unchanged",
                "this is not an integrated product or consumer protocol"]}
    for scope,c in info.items():
        row=dict(c)
        row["packed_over_legacy_minified"]=round(
            c["packed_minified_bytes"]/c["legacy_minified_bytes"],5) if c["legacy_minified_bytes"] else None
        row["packed_over_compact_minified"]=round(
            c["packed_minified_bytes"]/c["compact_minified_bytes"],5) if c["compact_minified_bytes"] else None
        summary["by_scope"][scope]=row
    raw={"cases":allcases,"summary":summary}
    file=out/"packed.private.json"
    write_new(file,raw)
    summary["private_result_sha256"]=sha(file)
    write_new(out/"packed.public.json",summary)
    print(json.dumps(summary,sort_keys=True))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("mode",choices=["selftest","analyze"])
    ap.add_argument("--folder")
    ap.add_argument("--freeze")
    ap.add_argument("--out")
    a=ap.parse_args()
    if a.mode=="selftest":selftest();return
    if not all([a.folder,a.freeze,a.out]):ap.error("missing source/freeze/out")
    analyze(a)
if __name__=="__main__":main()
