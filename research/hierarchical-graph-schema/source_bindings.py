"""Strict, side-effect-free source-instance binding checks for graph RC1.

Identity existence alone is not evidentiary custody. The separately run Git
attestor still validates recorded source bytes and AST witnesses.
"""
import hashlib
from pathlib import PurePosixPath
import re

def validate_bindings(g,n,s,identity,validate_path):
    profile=g["profile"]
    if profile=="project_code":
        auth=g["authority"]
        repo,rev,tree=auth["repo"],auth["commit"],auth["tree"]
        tree_id=identity("source",repo,rev,tree)
        if tree_id not in s:raise ValueError("absent immutable tree source")
        for sid,z in s.items():
            if z["access"]!="allowed" or z["citation_class"]!="citable":
                raise ValueError("Git source cannot be denied or silently unverified")
            if z["kind"]=="git_tree":
                if (sid!=identity("source",z["repo"],z["commit"],z["tree"])
                    or (z["repo"],z["commit"],z["tree"])!=(repo,rev,tree)):
                    raise ValueError("wrong Git tree source identity")
            elif z["kind"]=="git_blob":
                validate_path(z["path"])
                if (sid!=identity("source",z["repo"],z["commit"],z["path"],z["blob"])
                    or z["repo"]!=repo or z["commit"]!=rev or
                    not re.fullmatch("[0-9a-f]{40}",z["blob"])):
                    raise ValueError("wrong Git blob source identity")
            else:raise ValueError("unexpected code source kind")
        for node in n.values():
            if node["as_of_commit"]!=rev or node["id"]!=identity(
                "node",g["scope"],rev,node["kind"],node["locator"]):
                raise ValueError("node version/id mismatch")
            sid=node["source_id"]
            if node["kind"] in ("project","directory"):
                if sid!=tree_id:raise ValueError("unbound directory tree witness")
            elif node["kind"]=="file":
                if s[sid]["kind"]!="git_blob" or s[sid]["path"]!=node["locator"]:
                    raise ValueError("file points at a different existing blob")
            elif node["kind"]=="symbol":
                parent=node["locator"].split("::",1)[0]
                if s[sid]["kind"]!="git_blob" or s[sid]["path"]!=parent:
                    raise ValueError("symbol points at a different existing blob")
                if int(node["attrs"]["start_line"])<1:raise ValueError("invalid declaration line")
            else:raise ValueError("unknown code kind")
        for e in g["edges"]:
            a,b=n[e["source"]],n[e["target"]]
            expected=identity("edge",g["scope"],e["source"],e["predicate"],
                              e["target"],e["evidence_source_id"],e["line"])
            if e["id"]!=expected:raise ValueError("edge/source identity mismatch")
            if e["provenance"]!="observed_structure":
                raise ValueError("unreviewed code assertion")
            if e["predicate"]=="CONTAINS":
                if e["evidence_source_id"]!=b["source_id"]:
                    raise ValueError("wrong containment witness")
                expected_parent=str(PurePosixPath(b["locator"]).parent)
                parent_loc="." if a["kind"]=="project" else a["locator"]
                if parent_loc!=expected_parent:raise ValueError("false containment parent")
            elif e["predicate"] in ("DECLARES","IMPORTS_MODULE"):
                if (e["evidence_source_id"]!=a["source_id"]
                    or not isinstance(e["line"],int) or e["line"]<1):
                    raise ValueError("wrong code witness")
                if e["predicate"]=="DECLARES" and (
                    b["source_id"]!=a["source_id"] or
                    e["line"]!=b["attrs"]["start_line"]):
                    raise ValueError("declaration mismatch")
            else:raise ValueError("invented code relation")
    elif profile in ("knowledge_notes","operations_history"):
        snap=g["authority"]["sha256"]
        for sid,z in s.items():
            if z["access"]!="unknown" or z["citation_class"]!="unverified":
                raise ValueError("historical index lacks independent authority warrant")
            if z["kind"]!="indexed_document" or z["snapshot_sha256"]!=snap:
                raise ValueError("wrong legacy source kind")
            if sid!=identity("source",g["scope"],snap,z["indexed_doc_id"],z["content_hash"]):
                raise ValueError("legacy source mismatch")
        for node in n.values():
            expected=identity("node",g["scope"],snap,s[node["source_id"]]["indexed_doc_id"])
            if (node["id"]!=expected or node["access"]!="unknown"
                or node["citation_class"]!="unverified"):
                raise ValueError("legacy unknown promoted")
        for e in g["edges"]:
            if (e["predicate"]!="LINKS_TO" or
                e["provenance"]!="derived_nomination" or
                e["evidence_source_id"]!=n[e["source"]]["source_id"]):
                raise ValueError("legacy authored link upgraded")
    elif profile=="federated_overlay":
        for z in s.values():
            if z["kind"]!="synthetic_statement":
                raise ValueError("unexpected fixture source")
            if hashlib.sha256(z["text"].encode()).hexdigest()!=z["content_sha256"]:
                raise ValueError("synthetic source bytes mutated")
    else:raise ValueError("unknown graph profile")
