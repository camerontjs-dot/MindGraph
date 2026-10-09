# MainFrame graph structure census

Reusable research-infrastructure companion to [MindGraph issue #56](https://github.com/camerontjs-dot/MindGraph/issues/56). This project deliberately distinguishes **what links the indexed graph can traverse** from **whether graph navigation helps an agent answer an operational question**.

This apparatus performs the former only. It never infers missing relationships, modifies an existing corpus, rebuilds a production index, or treats a graph link as authority.

## Inputs and ownership

Three separately installed MindGraph SQLite indexes: `knowledge`, `projects`, and `operations`. Use their **actual installed SQLite files** as sources, but create transaction-consistent SQLite backups before any census computation. The Projects index may include documents from `40_operations`. Report the actual path overlap between Projects and Operations and do not count these as independent corpora when discussing evidence.

Private source paths, indexed identities, raw edges and backup files remain in the local `40_operations/mindgraph-eval/raw-materials/` experiment directory. Only reproducible portable scripts and aggregate results belong in this public repository.

## Run

With Python 3.10+ and sqlite3 from the standard library:

```sh
python research/graph-structure-census/graph_census.py selftest

python research/graph-structure-census/graph_census.py prepare \
  --out PRIVATE_EXPERIMENT_DIRECTORY \
  --knowledge-db PRIVATE_KNOWLEDGE_INDEX.sqlite \
  --projects-db PRIVATE_PROJECTS_INDEX.sqlite \
  --operations-db PRIVATE_OPERATIONS_INDEX.sqlite

# Inspect FREEZE.json. No code or snapshot changes permitted after freeze.

python research/graph-structure-census/graph_census.py measure \
  --out PRIVATE_EXPERIMENT_DIRECTORY

python research/graph-structure-census/verify_results.py \
  PRIVATE_EXPERIMENT_DIRECTORY
```

`prepare` creates three SQLite backups via the SQLite online backup API and freezes their SHA-256 hashes together with the code hash. `measure` refuses changed code or snapshots and will not overwrite a decisive result. `verify_results.py` is a separate implementation that recomputes key graph counts and components from frozen SQLite rows; it does not import the census calculations.

## Metric semantics

A **resolved edge** has *both endpoints present in the selected SQLite index*. A missing target is **out of that index**, not necessarily a broken filename or missing source file. A **connected document** participates in a resolved in-index edge; a document that only points to an out-of-index target is not connected for the purposes of current graph traversal.

**Weak connected components** ignore link direction but use only resolved endpoints. A directed one-/two-hop reachability metric keeps edge direction, deduplicates destinations and excludes the origin. Edge types are simply raw nonempty strings; free-text labels are not validated ontology predicates.

`Projects` and `Operations` are separate indexes, with overlapping source paths but potentially distinct document IDs. A shared file path or semantic similarity is **not** proof of cross-index link traversal. Cross-scope target intent is UNKNOWN unless independently recovered from source relationships.

## Why this is not a utility benchmark

Connectivity and density are structural observations. They do not establish whether a relationship is accurate, current, authorized, or relevant to a user's question. High connectivity can represent duplicated or unhelpful material; sparse graphs may still support a small number of high-value links.

A distinct, independently labelled **task utility protocol** is documented in [TASK-UTILITY-PROTOCOL.md](TASK-UTILITY-PROTOCOL.md). Its decisive comparisons are **not executed by the census** and require task selection before retrieval/graph scores. Do not choose gold by inspecting graph edges, use mutable source content after freezing, or count an unqualified model actor as an independent verifier.

## Evidence

The 2026-10-08 read-only structural measurement and its limits are in [RESULTS.md](RESULTS.md). The underlying private SQLite backups, raw anomaly inventories and detailed graph topology were not published. The resulting public code and aggregate results cannot recreate those private snapshots without access to the same authorized underlying databases.

**Neither this research branch nor its Draft PR authorizes changes to maintained MindGraph behavior, Conduit, MainFrame, indexes, schemas, graph authoring conventions or releases.**
