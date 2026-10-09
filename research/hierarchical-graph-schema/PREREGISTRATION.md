# Hierarchical Graph Schema RC0: preregistration (before prototype execution)

**Research:** [MindGraph #58](https://github.com/camerontjs-dot/MindGraph/issues/58)  
**Work class:** Research + Research Infrastructure. **No deployment authority.**  
**Bounded decision:** Is a small, versioned, evidence-bearing graph *projection contract* sufficient to represent per-project file/code navigation and cross-scope references safely, without a new graph database or falsely upgrading retrieval to authority?

## Sources pinned at design freeze

| Authority / use | Immutable identity |
|---|---|
| MindGraph maintained main, real Python specimen | `8df9ae7fccdb742558950ef9bdedb96cc74df6d0`, tree `44111ff75c53a1862001b9d041944e81a2d1430a` |
| Conduit maintained main, real Swift specimen | `10a8d03852b1589a98f212d317c26463cda93c76`, tree `5a134c3fd68f0515199ce0b062c9954abac77854` |
| MainFrame maintained private main, wrapper/operations context only | `dbff8c39790ce70961de2310809e1bd737ccb7a9`, tree `360eb4dc8134272ea0d5a2415f4f80d2bf4ec248` |
| Evidence Room maintained private main, canonical evidence-owner reference | `338a776479b835e67ed2fefa78801754063822d1`, tree `b0d124c9e20912bd8d08bfad948af7ac3a00fbae` |
| Evidence Room GN0 preserved research, external negative/SQL comparator | [Draft #72](https://github.com/camerontjs-dot/the-evidence-room/pull/72) at `1f6ed43984bb7b20f845c680163bea1990bd3563` |
| MindGraph graph census frozen previous evaluation | [#56 / Draft #57](https://github.com/camerontjs-dot/MindGraph/pull/57) |

Do not consume local dirty worktree bytes as GitHub authority. Real source read by `git ls-tree` and `git show` from *these* objects only. Use no `git fetch`, checkout/reset, reindex, source edit, daemon restart, agent launch, or model calls for this RC0.

## Experimental contrasts

1. **Flat baseline:** `git ls-tree`, `git grep` and language-aware direct source lookup. This is the strong low-complexity control for source/file questions.
2. **Structured relational baseline:** SQLite tables over the *same* frozen nodes and edges, with recursive CTE reachability.
3. **Typed logical graph:** the same SQLite-derived node/edge dataset exposed as project-local and cross-scope graph views, returning explicit path witnesses. A physical per-project DB is **not required** unless this contrast reveals a capability or isolation need.

Only supported source facts may create asserted edges. Every file reference is commit+blob anchored, and every parsed symbol/import uses an exact source span. A Swift file is a file, **not** a parsed Swift symbol graph in RC0; unsupported languages should be transparently marked.

## Candidate schema properties to falsify

- **Identity:** immutable source version and scoped node/edge IDs; no conflation of same paths across projects/versions; no duplicate endpoint collision; normalized relative path only.
- **Containment:** a root repo/directory contains a tracked file; no `../`, escaped symlink, untracked file, secret/binary, generated resource or test-result directory admitted silently. Directories themselves carry tree-relative navigation only.
- **Code:** Python function/class declarations and resolvable static imports may generate **syntactic** relations. Python dynamic dispatch, Swift symbol definitions, actual runtime calls, test coverage, semantic `DEPENDS_ON` and `QUALIFIED_BY` remain UNKNOWN without specialized source evidence.
- **Temporal/evidence:** node and source cite exact revision/blob; `current` is not assumed from a historical SHA. Evidence Room/source-owner artifacts are referenced rather than duplicated into a second governance authority.
- **Provenance:** edge category must be `observed_structure`, `source_asserted`, `reviewed_coded`, or `derived_nomination`. A derived nomination cannot be traversed as a warranted edge.
- **Access/path:** each output path carries all hops, direction, source witness and intermediate eligibility. A not-citable/quarantined intermediate blocks **qualified whole-path admissibility**, even if the terminal destination is independently citable. Reverse direction, silent cross-scope hop, missing source/invalid blob or unknown access cannot be repaired by a high relevance score.
- **Boundaries:** Knowledge stays a separate note/concept profile; Project and Operations reuse structural and evidence-status vocabulary with different freshness; Evidence Room remains owner of episode and review decisions. A global directory graph is an index-level *projection*, not automatically a semantic knowledge graph.

## Frozen real-source probes and negative controls

The public `CASES.json` declares real code/file checks and false-link controls before any candidate run. The adversarial `FIXTURE.json` declares a synthetic source-equivalent graph whose expected path refusals cannot be softened after results. The private cross-scope MainFrame wrapper check may add **source-backed diagnostic context** but is not scored as an independently adjudicated golden source and must not leak its bytes to the public PR.

## Pass threshold for `STRUCTURALLY_DEFENSIBLE_BOUNDED_SCHEMA_RC0`

All of these must hold on the exact frozen objects:

1. Extraction produces deterministic source/edge identities and stable JSON/SQLite receipts across independent same-input rebuilds.
2. The *real public repos*' pinned code navigation positive cases match independently source-read fixtures, with no unsupported Swift semantic edges.
3. At least one nontrivial graph path and an equivalent recursive-SQL path produce identical ordered node/edge witnesses. The flat baseline's ability to find direct files is **reported**, not discounted.
4. Synthetic adversarial cases reject wrong source identity, reverse direction, cross-scope without source authority, non-citable intermediate, derived nomination, cycles without bound, and malformed path. Seeded violations must fail validation and preserve the initial failure.
5. No protected source or current on-disk worktree bytes are changed. Exact object hashes, test logs and negative outputs retained.

`SCHEMA_INCONCLUSIVE` for partial coverage or invalid checks; `SCHEMA_REJECTED` for demonstrable unjustified authority promotion; `STRUCTURALLY_DEFENSIBLE_BOUNDED_SCHEMA_RC0` only for a scoped research contract satisfying those mechanical properties. This is **not `GRAPH_UTILITY_SUPPORTED`**, universal ontology adoption, MainFrame vNext qualification, or production compatibility.

## Further evidence burden

Full cross-project source/decision/qualification task usefulness needs genuinely independent task gold, matched strong search/SQL baselines, and a properly isolated agent runtime. MainFrame #80 and MindGraph #33-35 remain blocked/NOT_RUN. Preserve the first result, including any negative SQL parity, before any successor schema design.

## Artifacts and privacy

Publish the procedural schema, public synthetic fixtures, portable read-only builders/checkers and sanitized aggregates on a new Draft Research PR. Keep real private source snippets, absolute paths, indexes, historical user records, raw experiments and environment details in MainFrame's existing private evaluation surface. Do not modify or merge [#57](https://github.com/camerontjs-dot/MindGraph/pull/57), which owns the previous graph census.
