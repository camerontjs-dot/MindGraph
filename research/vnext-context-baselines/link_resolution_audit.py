#!/usr/bin/env python3
"""Read-only reconstruction of stored MindGraph link edges against frozen FTS text.

This diagnoses scope/identity effects. It never edits source, indexes, labels,
the established #56/#54 freezes, or installed MindGraph. Public output is
aggregated; per-edge provenance stays private.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from mindgraph import parser
from mindgraph.models import ParsedDocument

NAMES=("knowledge","projects","operations")

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda:f.read(1<<20),b""):h.update(part)
    return h.hexdigest()

def jsonnew(path,obj):
    with Path(path).open("x",encoding="utf-8") as f:
        json.dump(obj,f,indent=2,sort_keys=True)
        f.write("\n")

def ro(path):
    import urllib.parse
    p=Path(path).resolve(strict=True)
    conn=sqlite3.connect("file:"+urllib.parse.quote(str(p),safe="/")+"?mode=ro",uri=True,timeout=30)
    conn.execute("PRAGMA query_only=ON")
    return conn

def scope_rows(path):
    with ro(path) as c:
        docs=c.execute("""SELECT d.id,d.title,d.path,d.content_hash,d.metadata_json,
                                 f.content
                           FROM documents d JOIN documents_fts f ON f.id=d.id
                           ORDER BY d.id""").fetchall()
        edges=c.execute("""SELECT source_id,target_id,relationship_type
                           FROM edges ORDER BY source_id,target_id,relationship_type""").fetchall()
    parsed=[]
    for id,title,filepath,h,raw,content in docs:
        meta=json.loads(raw or "{}")
        parsed.append(ParsedDocument(id=id,title=title,path=filepath,content_hash=h or "",
                                     metadata=meta,truth_text=content or ""))
    return parsed, [(str(a),str(b),v) for a,b,v in edges]

def raw_link_labels(doc):
    labels=[]
    for label in parser.extract_metadata_link_targets(doc.metadata):
        labels.append((label,None,"frontmatter"))
    for match in parser.LINK_PATTERN.finditer(doc.truth_text):
        target,relation=match.groups()
        labels.append((target,relation.strip() if relation else None,"truth"))
    return labels

def normalize(raw):
    return parser._normalize_link_target(raw)

def resolve_categories(label,src_path,scopes,name):
    own=scopes[name]["resolver"]
    own_hit=own.resolve(label,src_path)
    cross_hits={k:v["resolver"].resolve(label) for k,v in scopes.items() if k!=name}
    cross_hits={k:v for k,v in cross_hits.items() if v is not None}
    own_ambiguous=_is_ambiguous(own,label,src_path)
    foreign_ambiguous=any(_is_ambiguous(scopes[k]["resolver"],label,src_path)
                          for k in scopes if k!=name)
    if own_hit is not None:
        return "in_scope_declared_target_recoverable_but_index_edge_missing",cross_hits
    if own_ambiguous:
        return "same_scope_ambiguous_label",cross_hits
    if cross_hits:
        # This is only a nomination; another index's doc ID is never a
        # project-source authority or permission to traverse that index.
        return "target_nominated_by_other_index",cross_hits
    if foreign_ambiguous:
        return "possible_foreign_scope_label_ambiguity",cross_hits
    return "not_resolved_in_any_frozen_index",{}

def _is_ambiguous(resolver,label,source_path):
    normalized=normalize(label)
    if normalized in resolver.paths or (
        source_path is not None and str(Path(source_path).parent/normalized) in resolver.paths
    ):
        return False
    if "/" not in normalized:
        stem=parser._normalize_lookup_key(Path(normalized).stem)
        if len(resolver.stems.get(stem,set()))>1:return True
    title=parser._normalize_lookup_key(label)
    if len(resolver.titles.get(title,set()))>1:return True
    if "/" not in normalized:
        stem=parser._normalize_lookup_key(Path(normalized).stem)
        if len(resolver.slug_suffixes.get(stem,set()))>1:return True
    return False

def synthetic():
    # Explicit same-scope, ambiguity, and out-of-index target must remain
    # distinct. Under no case should another scope's path silently be
    # treated as a valid in-index link.
    def doc(i,path,title):
        return ParsedDocument(id="synthetic:"+i,path=path,title=title,
                              content_hash="0"*64,metadata={},truth_text="")
    a=[doc("1","30_projects/module/one.md","Module One"),
       doc("2","30_projects/module/other.md","Some target"),
       doc("3","30_projects/unrelated/some-target.md","Some target")]
    b=[doc("4","40_operations/run.md","Run")]
    own=parser.LinkResolver.from_documents(a)
    foreign=parser.LinkResolver.from_documents(b)
    assert own.resolve("one","30_projects/module/entry.md")=="30_projects/module/one.md"
    assert own.resolve("Some target") is None
    assert own.resolve("run") is None and foreign.resolve("run")=="40_operations/run.md"
    assert _is_ambiguous(own,"Some target",None)
    assert not _is_ambiguous(own,"missing",None)
    assert parser.compute_doc_id("run.md") != b[0].id
    print("SYNTHETIC_PRETEST_PASS: sibling, ambiguous, foreign, unknown, no ID conflation")

def run(folder,freeze_path,out):
    freeze=json.loads(freeze_path.read_text())
    me=Path(__file__)
    if sha(me)!=freeze["script_sha256"]:
        raise ValueError("FROZEN_CODE_DRIFT")
    datasets={}
    for name in NAMES:
        db=folder/f"snapshot.{name}.sqlite"
        if sha(db)!=freeze["input_sha256"][name]:raise ValueError("FROZEN_DB_DRIFT:"+name)
        docs,edges=scope_rows(db)
        res=parser.LinkResolver.from_documents(docs)
        datasets[name]={"docs":docs,"edges":edges,"resolver":res,"ids":{d.id for d in docs},
                        "docpaths":{d.id:d.path for d in docs}}
    result={"study":"index-authored-link-resolution-rc0",
            "code_sha256":freeze["script_sha256"],"freeze_sha256":sha(freeze_path),
            "sources":"frozen documents_fts original Truth text plus indexed metadata",
            "scopes":{},"limits":["out-of-index does not prove missing from disk",
                                  "foreign matching is a navigation nomination, not a legitimate cross-scope edge",
                                  "parser version may differ from installed index producer",
                                  "no source freshness or approval is inferred"]}
    private={"scopes":{}}
    for name in NAMES:
        data=datasets[name];docs=data["docs"];stored=data["edges"];res=data["resolver"]
        stored_set=set(stored)
        recovered=set()
        labels_by_tuple=defaultdict(list)
        for d in docs:
            for label,rel,origin in raw_link_labels(d):
                e=parser._edge_for_target(label,d.id,link_resolver=res,source_path=d.path,
                                           relationship_type=rel)
                labels_by_tuple[(e.source_id,e.target_id,e.relationship_type)].append(
                    {"target":label,"origin":origin,"source_path":d.path})
            for e in parser.extract_document_graph_edges(d,link_resolver=res):
                recovered.add((e.source_id,e.target_id,e.relationship_type))
        stored_missing=[edge for edge in stored if edge[1] not in data["ids"]]
        categories=Counter()
        private_rows=[]
        source_ids={x[0] for x in stored_missing}
        matched=0;ambiguous=0;foreign=Counter()
        for edge in stored_missing:
            claims=labels_by_tuple.get(edge,[])
            if not claims:
                categories["cannot_reconstruct_original_edge_label"]+=1
                private_rows.append({"edge_source_id":edge[0],"edge_target_id":edge[1],
                                     "category":"cannot_reconstruct_original_edge_label"})
                continue
            matched+=1
            labels=list(dict.fromkeys(x["target"] for x in claims))
            candidate_labels=[]
            for label in labels:
                category,hits=resolve_categories(label,claims[0]["source_path"],datasets,name)
                candidate_labels.append((category,label,hits))
            # Classification intentionally refuses to choose between distinct
            # labels generating the same target hash if their explanations differ.
            classes={x[0] for x in candidate_labels}
            cat=next(iter(classes)) if len(classes)==1 else "multiple_declared_labels_with_different_explanations"
            categories[cat]+=1
            if cat=="target_nominated_by_other_index":
                for hit in candidate_labels:
                    for other in hit[2]:foreign[other]+=1
            private_rows.append({"edge_source_id":edge[0],"edge_target_id":edge[1],
                                 "source_path":claims[0]["source_path"],"category":cat,
                                 "label_nominations":candidate_labels})
        reconstructed_only=recovered-stored_set
        stored_only=stored_set-recovered
        r={
            "indexed_documents":len(docs),
            "stored_edges":len(stored),
            "stored_edges_resolving_in_scope":len(stored)-len(stored_missing),
            "stored_edges_missing_target":len(stored_missing),
            "reconstructed_edge_tuples":len(recovered),
            "matched_edge_tuples":len(recovered & stored_set),
            "stored_only_tuples":len(stored_only),
            "reconstructed_only_tuples":len(reconstructed_only),
            "missing_target_edges_attributed_to_indexed_authored_text":matched,
            "missing_target_unattributed_edge_count":len(stored_missing)-matched,
            "missing_target_categories":dict(sorted(categories.items())),
            "foreign_index_candidate_mentions":dict(sorted(foreign.items())),
        }
        assert len(stored_missing)==sum(categories.values()),(name,"category sum")
        assert r["matched_edge_tuples"]+len(stored_only)==len(stored_set)
        assert r["matched_edge_tuples"]+len(reconstructed_only)==len(recovered)
        result["scopes"][name]=r
        private["scopes"][name]={"unresolved_edges":private_rows,
                                   "stored_only_tuples":list(stored_only),
                                   "reconstructed_only_tuples":list(reconstructed_only)}
    for name in NAMES:
        db=folder/f"snapshot.{name}.sqlite"
        if sha(db)!=freeze["input_sha256"][name]:raise ValueError("POST_DB_DRIFT:"+name)
    target=out/"resolution.private.json"
    jsonnew(target,private)
    result["private_receipt_sha256"]=sha(target)
    jsonnew(out/"resolution.public.json",result)
    print(json.dumps(result,sort_keys=True))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("mode",choices=["selftest","analyze"])
    p.add_argument("--folder")
    p.add_argument("--freeze")
    p.add_argument("--out")
    a=p.parse_args()
    if a.mode=="selftest":synthetic();return
    if not (a.folder and a.freeze and a.out):p.error("analyze requires folder/freeze/out")
    out=Path(a.out).resolve()
    if out.exists():raise FileExistsError("NO_OVERWRITE output")
    out.mkdir(parents=True)
    run(Path(a.folder).resolve(),Path(a.freeze).resolve(),out)
if __name__=="__main__":main()
