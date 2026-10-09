# Graph structure census RC0: observed result

**Date:** 2026-10-08 (America/Toronto)  
**Owner:** [MindGraph issue #56](https://github.com/camerontjs-dot/MindGraph/issues/56)  
**Product reference:** `main@8df9ae7fccdb742558950ef9bdedb96cc74df6d0` / tree `44111ff75c53a1862001b9d041944e81a2d1430a`  
**Research disposition (structure only):** `STRUCTURE_CHARACTERIZED_WITH_BOUNDS`  
**Graph-dependent task utility:** `NOT_RUN`

## Frozen evidence identities

The private experiment used transaction-consistent read-only SQLite backups of three installed indexes. Raw source-path inventory, relationship strings, anomalous edges, and index bytes remain local, not in this public repository.

| Immutable object | SHA-256 |
|---|---|
| Census apparatus | `abf1b128139dcf832fb8568988d149e3095e22ab46bd473fe8c5bff6f7062315` |
| Frozen private manifest | `bd82f0f4657cd4087bd4eed613d3fa1df129602010925107ee5966e67e0d19eb` |
| Knowledge SQLite snapshot | `68df830edc854d5a207905ac71abca7d43e8c0b2e4594acce2c13c801bbf3ba1` |
| Projects SQLite snapshot | `516c49feb8dbc3620da7d2b9d9430b242009ef09e6c9fc92146be4ad7975fb95` |
| Operations SQLite snapshot | `5fd094da67ebe4184cfb616e1f07d9659fddaac817851c8b5b1d66dec776f546` |
| Private raw diagnostics | `5b7c9ec1152ad74610c94b6f208f732a103a46594ac72a27c7cb82e0c8a6ef5b` |
| Independent-formula verifier source | `f70a7e9d2dbb227fec438cdc3ed811dac4629842a4572247d77d377138e7a9be` |

Runtime: Python 3.14.4, standard-library SQLite. No model, embedding, agent, or scorer-generated labels were used during the structural census.

## Connected coverage and unresolved targets

| Metric | Knowledge index | Projects index | Operations index |
|---|---:|---:|---:|
| Indexed documents | 4,075 | 812 | 90 |
| Stored edge rows | 6,742 | 189 | 19 |
| Both endpoints resolve in-index | 6,051 | 56 | 6 |
| Target missing from selected index | 691 | 133 | 13 |
| Source missing from selected index | 0 | 0 | 0 |
| Nodes participating in a resolved edge | 3,458 | 52 | 8 |
| Resolved-connection coverage | 84.86% | 6.40% | 8.89% |
| Nodes with **no** resolved connection | 617 | 760 | 82 |
| Weak connected components | 816 | 769 | 84 |
| Largest component | 2,086 | 29 | 6 |
| Nontrivial components (size >=2) | 199 | 9 | 2 |
| Typed (nonempty) edge rows | 63 | 8 | 1 |
| Untyped edge rows | 6,679 | 181 | 18 |
| Self-loops (resolved) | 24 | 0 | 0 |
| Duplicate source→target edge rows | 0 | 0 | 0 |

The fraction of edges with a target not present in the selected index is **10.25% Knowledge, 70.37% Projects and 68.42% Operations**. This is an *indexed-identity resolution* result. It is **not** evidence that those targets are missing from disk, incorrectly authored, outside the author's intended scope, or semantically invalid.

## Index boundaries and overlap

The Projects index includes **728 documents under `30_projects` and 84 under `40_operations`**. The separate Operations index contains **90 documents under `40_operations`**.

Projects and Operations indexes share **82 exact document source paths**, and every shared path has a **different document ID** in the two indexes. Eight Operations-index source paths are absent from the Projects index; two of the 84 operations paths in the Projects index do not appear in the Operations index.

Among the 56 resolved edge rows in the Projects index:
- 49 are Projects → Projects;
- 2 are Projects → Operations;
- 5 are Operations → Operations;
- 0 are Operations → Projects.

These source prefix counts describe one index's stored edges. They do not establish that a graph step can traverse from one SQLite index to another or that overlapping records have identical source bytes/currentness.

## Reachability

Median directed one-hop and two-hop reachability is **zero across all three indexes** when every indexed document is counted. The Knowledge graph has a large weakly connected component (2,086 documents), but that does not imply each document can reach all those documents along directed links.

The Projects graph's directed two-hop neighborhood reaches at most **9 distinct other indexed documents** from any one source; the Operations graph gets **no additional distinct document from a second hop**. This is structural capacity, not demonstrated task benefit.

## Verification and interpretation

- The census script passed Python compilation and its deterministic synthetic controls for resolved/missing endpoints, component-size changes, type sensitivity, duplicate logical endpoints and source-prefix classification.
- After the decisive first run, a **separately implemented** verifier recomputed source/target validity, edge counts, typed/untyped counts, weak components, resolved-coverage counts, source-path overlap, and immutable file hashes. It returned `VERIFY_PASS` for all three snapshots.
- That independent formula check is **not** a context-independent agent, independently labelled oracle, or external replication. Some nondecisive derived summaries (e.g., two-hop percentiles) were calculated by the measurement script rather than independently reimplemented.
- The runtime saw only frozen installed indexes. Source filesystem freshness and any temporal relationship consistency remain unqualified.

### What follows

**Observed:** Projects and Operations have very low traversable coverage, a large fraction of out-of-index targets, and almost no typed relationships. Knowledge has a more extensive but mostly untyped document-link graph.

**Inference:** Before introducing more graph-aware retrieval or richer ontology machinery, identify the major causes of out-of-index edges and whether source-backed, high-value operational relationships are presently representable. Do not equate index scope gaps with authoring defects.

**Unknown:** Whether a relation-aware graph produces a better *completed task* than equally budgeted ordinary search; whether asserted source relationships are correct; which missing targets are on disk, belong to another index, or reflect bad links; and whether generated/inferred edges would justify their cost.

The separate [task utility protocol](TASK-UTILITY-PROTOCOL.md) is setup-only. No task actors, independent gold-label adjudication, or completed-task comparison have run.
