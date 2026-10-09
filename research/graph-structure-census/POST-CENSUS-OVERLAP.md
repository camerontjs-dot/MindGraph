# Post-census diagnostic: overlapping index identities and live source hashes

**This is a separately labelled, after-result diagnostic.** It does **not** modify the frozen [graph structure census RC0](RESULTS.md), whose structural disposition was `STRUCTURE_CHARACTERIZED_WITH_BOUNDS`.

Observed at **2026-10-09T03:52:52Z** (October 8 evening, America/Toronto), against the same private frozen Projects and Operations SQLite snapshots and the *then-current* MainFrame filesystem. The filesystem was inspected read-only and **not separately frozen**. This makes the observation time-sensitive.

## Observation

The Projects and Operations indexes had **82 identical relative source paths**, all with distinct per-index document IDs:

| Check | Documents |
|---|---:|
| Overlapping paths with matching indexed `content_hash` | 76 |
| Overlapping paths with differing indexed `content_hash` | 6 |
| Both indexed hashes match current file bytes | 76 |
| Only the Projects indexed hash matches current file bytes | 5 |
| Only the Operations indexed hash matches current file bytes | 0 |
| Neither indexed hash matches current file bytes | 1 |
| Overlapping source files missing from disk | 0 |

Method: join `documents.path` between the two frozen SQLite snapshots, compare each `documents.content_hash`, and compute SHA-256 over current bytes for the corresponding relative file, resolving paths under the MainFrame root and checking containment. The observed hashes are compatible with a lagging Operations index for five files, but the calculation alone does not establish when or why either index was updated. The sixth path matched neither frozen index at this observation time.

For the graph target IDs that failed lookup in their own index, none matched a document ID in either of the other two indexes (Knowledge 691, Projects 133, Operations 13). **This does not prove there is no cross-scope relationship**; scoped document IDs have different namespaces, and the edge table does not preserve the original link target string.

## Interpretation

- These are observations of indexed content identity and one live-file state, **not an approved reindex request**.
- Do not repair the Results RC0 record, reload a daemon, or promote new source authority on this basis.
- The immediate successor is a **read-only provenance diagnosis** of the six differing paths, index staging/promotion timestamps and ownership. It should determine whether operations lag is expected lifecycle behavior, a missed refresh, or a true contract/installation defect.
- A repair or operational migration requires a separately bounded and verified change with an owner, source authority, and current-client impact.

All specific private file paths, contents and source hashes remain local. This supplement is aggregated to avoid exposing private MainFrame operational material.
