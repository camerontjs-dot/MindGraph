# MainFrame hierarchical graph v0.3: bounded research schema

**Status: `STRUCTURALLY_DEFENSIBLE_FOR_BOUNDED_RESEARCH` on the tested identities only.**
Not a maintained product schema, MainFrame migration plan, universal ontology, or verified agent-task performance claim. Research owner: [MindGraph #58](https://github.com/camerontjs-dot/MindGraph/issues/58). Source/falsifier baseline: [PREREGISTRATION.md](PREREGISTRATION.md).

## Architectural decision

Use one **logical graph contract with distinct scope views**, a lightweight global directory/catalogue projection, and a source-of-truth-preserving cross-scope overlay. Do **not** provision a database per project by default. A project's “own graph” is initially a filtered, commit-bound view of its tracked directory/file/code structure. The directory catalogue stores **pointers**, not an invented semantic relationship between every project.

The graph is **derived and inspectable, not authoritative**. Its owner source remains Git, MainFrame's maintained document/operation records, or Evidence Room. Its connections can be queried using SQLite: a specialized graph store has no measured benefit on the current tests.

```text
MainFrame directory catalogue (scoped, content-hash-bound pointers only)
  ├── Knowledge view: concepts/notes, citations, historical links
  ├── Projects
  │    ├── project:MindGraph @ exact Git commit (files, Python symbols, static imports)
  │    └── project:Conduit @ exact Git commit (files; Swift symbols unsupported RC0)
  ├── Operations view: versioned episodes/procedures, source-specific state
  └── Evidence Room: externally owned evidence/version/decision records
                    (not materialized without explicit access and source contract)

Explicit cross-scope overlay, only if independently warranted:
  source identity + relation + direction + predicate + hop witness
  + scope grant + per-node and per-edge eligibility + temporal aperture
```

A cross-scope overlay is not currently an automatic graph edge generator. The pilot's public directory catalogue advertises zero cross-scope semantic edges.

## Source envelope

A `source` refers to **exact material owned outside the graph**:

- `git_tree`: `{repo, commit: full SHA, tree: full SHA}`.
- `git_blob`: `{repo, commit: full SHA, path: normalized relative path, blob: Git blob SHA}`.
- `indexed_document`: `{snapshot_sha256, indexed_doc_id, content_hash}`, with original index scope. A source path is not a sufficient identity across two scopes.
- `synthetic_statement`: text and SHA-256 only in explicitly marked evaluation worlds.
- Future Evidence Room / GitHub evidence pointers must preserve their source-owner record ID, version, review/authority/access state and direct source identity. A name or citation-like string is insufficient.

Each source carries its own `access` and `citation_class`; independently indexed historical material remains `unknown/unverified` until verified in its owning system. Observing a Git blob at an old commit proves **historical bytes**, not present status or approval. Similarity retrieval does not upgrade source status.

Source identity is recomputed from the binding tuple, and an independent-formula source attestor must re-open the exact Git tree/blob and compare actual bytes where these stronger facts are claimed. An internally self-consistent false SHA can fool a structural validator and must fail at the source-object boundary.

## Node envelope

`{id, scope, kind, locator, source_id, access, citation_class, as_of_commit, attrs}`

- `id` is a stable deterministic scoped identity over kind/locator plus the immutable projection version; two projects with the same path do not collide.
- `scope`: e.g., `project:mindgraph`, `project:conduit`, `knowledge`, `operations`.
- `kind`: `project`, `directory`, `file`, `symbol`, `note`, `operational_record`; synthetic `record` is evaluation-only.
- `locator` is an exact path, AST symbol locator/line, or indexed source path. It is never an absolute, escaping, or implicitly current machine path.
- `source_id` must resolve to a matching source instance, *not merely any existing source ID*.
- `attrs` is a bounded profile-specific object. It is **not** evidence of a predicate unless supported by the source.

Directory and source paths derive from the Git tree. Declared Python top-level functions/classes derive from the exact pinned Python AST; static module import edges derive only from resolvable Python import syntax. A Swift source file is navigable, but RC0 **does not claim to parse Swift symbols or calls**. Extend code semantics later through qualified SourceKit/SCIP/tree-sitter adapters, with new receipts and error coverage.

## Edge envelope

`{id, source, target, predicate, provenance, evidence_source_id, line, direction, [scope_bridge]}`

- `id` binds scoped endpoints, predicate, specific evidence source and source line.
- `source` and `target` are directed node IDs; direction may not be reversed to imply a matching relation.
- `evidence_source_id` identifies the **actual declaring source blob or index record**; referential existence alone is insufficient.
- `line`: integer for source-line-supported Python declaration/import, else null.
- `provenance`: `observed_structure`, `source_asserted`, `reviewed_coded`, or `derived_nomination`.
- Optional `scope_bridge: explicit_reference` is necessary but **not sufficient** for cross-scope traversal. The consumer must separately grant the source→target scope pair.

### Predicate registry for this bounded candidate

| Predicate | Current supported meaning | What it does **not** establish |
|---|---|---|
| `CONTAINS` | Git tree/direct-parent directory → directory/file | Subject-matter relationship or repo ownership approval |
| `DECLARES` | Pinned Python file declares a top-level function/class at a recorded AST line | Dynamic behavior, call site or test coverage |
| `IMPORTS_MODULE` | Source-line Python import resolves to a module file in the same pinned tree | Runtime use or automatic transitive dependency |
| `LINKS_TO` | Existing indexed wiki/document link nomination, or explicit synthetic fixture link | Verified relation, current authority, approved cross-scope traversal |
| `EVIDENCED_BY` | **Reserved** for an Evidence Room/source-owner attested binding | Automatically established in the two-repo pilot |
| `SUPERSEDED_BY` | **Reserved** for an explicit evidence-owner version/decision transition | Inferable from timestamps or similar titles |

**Explicitly not inferred:** `DEPENDS_ON`, `CALLS`, `TESTED_BY`, `QUALIFIED_BY`, `APPROVES`, `CURRENT`, `SAME_INCIDENT_AS`. These may become separate source-attested relations in a successor. No graph edge may independently assert normative approval.

## Profile and version boundaries

- **Knowledge**: note/concept retrieval, existing explicit authored links and citations, trust-separated from live operations. The frozen legacy projection labels prior link edges `derived_nomination` and refuses to infer authority from them. It does not claim a full concept/entity ontology.
- **Project**: project workspace and code structure, immutable pinned repo tree, declared code symbols and import semantics. One logical graph per project, each at its own exact commit.
- **Operations**: procedures, runs/incidents and time-bound status once an authorized source contract exists. The RC0 operation snapshot projection retains historical documents and link nominations only, not inferred process ownership or current status.
- **Evidence Room**: owns adjudicated episode, qualification, review and supersession authority. Only source-version-bound *references* should be projected into MainFrame, and no protected/private details copied into public graph outputs.
- **Catalogue**: `directory_roots` and `views[]` with `scope`, `profile`, `projection_sha256`, `authority`, access requirements. Its `semantic_edges_between_scopes` remains empty unless separately warranted. Unknown/missing scopes are `NOT_RUN_UNAVAILABLE`, never a generated placeholder graph.

These profiles share identity/provenance/access vocabulary, **not** a claim that their content and freshness semantics are identical.

## Query/path contract

Return a path as an ordered **edge witness chain**, not just an endpoint's citation status. To call a *whole path* admissible:

1. The path starts in a named, access-authorized scope and retains the declared direction.
2. Each hop is explicitly source-bound, eligible, appropriately typed, and inside the configured hop/size limits.
3. **Every intermediate** node, evidence source and destination satisfies its own access/citation eligibility; a quarantined midpoint cannot silently become a valid warrant for an otherwise citable leaf.
4. Any boundary crossing requires a source-attested/reviewed edge with an explicit consumer grant for that source→target scope pair.
5. `derived_nomination` edges are discovery hints, not warrants. Absent relation is `UNKNOWN` outside explicitly complete coverage.
6. Preserve citation, source hash/version, traversal path, exclusions and negative results in the output. An individual destination remains individually citable without implying its traversal route was admissible.

The GN0 quarantined-middle-node example is the key falsifier. The synthetic RC0 fixture tests path lineage, a reversed edge, ambiguous/denied intermediate, inferred link, scope crossing, cycle and no-answer paths. The tested SQL control reproduces the graph outcomes on source-equivalent facts.

## Database and implementation posture

SQLite (including recursive CTEs) is sufficient for the current candidate. A physically separate database per project would duplicate schema/index overhead and still require global identity/version semantics. Consider separate physical storage only when measured index scale, permissions, workload or lifecycle independence justifies it. Do not normalize private material into one globally readable table merely because the graphs share a schema.

Use **small projections and lazy local source expansion** instead of eagerly indexing all source texts. Exclude credentials, untracked working trees, generated output, third-party directories and unsupported file modes. Introduce optional symbol index adapters for languages in which verified source references are useful. Never claim a full repository call graph from file containment alone.

## Research status and limits

The RC0.3 candidate passed source identity/line validation on two real public repos, 7/7 predeclared navigation cases with 7/7 strong Git and recursive-SQL baselines, 7/7 synthetic path cases, and 12/12 full adversarial checks. The public-only mode passed 11/11 with the private Knowledge mutation marked `NOT_RUN_UNAVAILABLE`. A separate actual Git-object attestor checked 318 pinned public code blobs. Private frozen Knowledge (4,075 nodes) and Operations (90 nodes) projections passed source/index identity checks.

**What this does not prove:** better retrieval or completed tasks than search; whole-MainFrame coverage; current index freshness; cross-project authority; full Swift AST/call relations; GitHub PR→decision→qualification lineage; automatically inferred ontologies; independent model actor performance; consumer isolation. No existing repository code, DB or MainFrame product contract was changed.

A successor must supply independently source-derived real task oracles and a qualified isolated actor before claiming graph-specific advantage. This research candidate is defendable as a **bounded structure**, not yet as a complete MainFrame knowledge graph.
