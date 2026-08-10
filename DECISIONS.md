# MindGraph decisions

Architectural decision records for MindGraph. Each entry records what was decided, what was rejected, and why. A future reader should be able to recover the full reasoning from the entry alone, without access to planning files or conversation history.

---

## 2026-08-05 — Opt-in lease-aware idle exit with serialized proxy wakeup

**Status:** Accepted for promoted root implementation; live activation deferred.

**Decision:** Add an optional idle lifecycle to the shared loopback daemon. An
idle exit is permitted only after a documented grace with zero in-flight tool
calls and zero unexpired renewable client leases. The stdio proxy may opt into
serialized health-checked startup and holds a lease for its lifetime. Health
reports process identity and lifecycle counters; status combines listener
health with the standalone PID file, while stop refuses ambiguous PID or
listener identity. Legacy stdio/list behavior, explicit scope selection,
loopback binding, and default persistent behavior remain unchanged.

**Supervision boundary:** The current `KeepAlive=true`, `RunAtLoad=true`
LaunchAgent is incompatible with idle exit and remains untouched by
implementation/testing. Explicit activation unloads and disables that plist in
a human-confirmed idle window and writes the proxy opt-in marker. Direct HTTP
cannot transparently wake a dead endpoint without an additional supervisor or
socket-activation front door, so it retains a documented manual-start or
connection-failure boundary.

**Rejected alternatives:** exiting under the current KeepAlive job; using
process age as idleness; exiting with an in-flight request; assuming an open
direct HTTP session is a lease; signaling a stale PID blindly; concurrent
proxy-spawn races; changing defaults; or claiming transparent direct-HTTP wake.

## 2026-08-03 — Explicit-scope loopback Streamable HTTP daemon

**Status:** Accepted for workbench verification only; root promotion deferred.

**Decision:** Add a shared FastMCP Streamable HTTP server configured on
loopback, with explicit `knowledge` (`durable_knowledge`) and `projects`
(`project_status`) scope selection. Every daemon response names the selected
scope and trust profile; no call queries or reranks both stores. Open both
indexes read-only and share one embedder. Preserve the existing one-database
stdio server and its default list-shaped query and neighbor results unchanged.
Provide an official MCP SDK stdio server/client proxy and PID-file lifecycle
control with caller-configurable state paths.

**Why:** Process sharing must not erase lifecycle trust or break installed
stdio callers. SDK transports and session initialization are safer than a
hand-written JSON-RPC bridge. Explicit state paths keep supervision inspectable
and tests away from live state.

**Consequences:** This workbench slice is loopback-only and has no auto-start,
hot reload, authentication, launchd integration, or benchmark claim. Root
wrappers and `.mcp.json` remain unchanged pending a separate promotion gate.

**Rejected alternatives:** binding all interfaces; silently querying both
indexes; returning unlabelled daemon lists; replacing `serve-mcp`; hand-rolling
JSON-RPC over HTTP; or testing against `~/.mindgraph`.

## 2026-07-27 — Separable C-0 filtering and context allocation (MainFrame ADR-047)

**Status:** Accepted; workbench implementation pending.

**Decision:** Split `apply_dual_gate_governance` into two behaviour-preserving primitives — `_filter_by_c0_eligibility(results, manifest)` (manifest validation, document ID / resolved path / content-hash matching, and attachment of the consumed eligibility run ID) and `_allocate_context_budget(results, max_seats, max_chars, quiet_keywords)` (seat shortlist, the `quiet_keywords` swap heuristic, and the character budget with its truncation rule). Rewrite the existing helper as the composition of the two, keeping its name, signature, defaults, and behaviour unchanged. Expose two new entry points: `filter_by_c0_eligibility` (no seat or character cap) and `allocate_ungoverned_context` (no manifest parameter). The latter requires a keyword-only `evaluation_use_only: Literal[True]` with no default, refuses an `eligibility_manifest` keyword, never sets `eligibility_run_id`, and stays out of `__all__`, the CLI, and the MCP surface.

**Why:** The dual-gate confirmatory protocol measures two apparatus independently — C-0 source eligibility and Speaker context allocation — across baseline, C-0 only, Speaker only, and both. The combined helper made the Speaker-only arm unreachable (it refuses to run without a manifest, and filters before seating) and reduced the C-0-only arm to setting seat and character limits high enough to "effectively disable" them. That is a parameter choice, not an absent step, so a measured difference could not be attributed to a specific gate. Each arm must differ by the presence or absence of a step.

Allocation is subtractive: `allocate(results, …) ⊆ results`. It cannot admit a source that ungated `run_query` did not already return, so exposing it adds no retrieval reach beyond the existing baseline path. The real risk is misinterpretation — a caller reading "allocated" as "governed" — which the naming, the explicit acknowledgement argument, and the null-provenance assertion address.

**Consequences:** This authorizes measurement only. Promoting ungoverned allocation to any product or default path is a separate decision needing its own evidence; a passing 2×2 arm is not that evidence. `apply_dual_gate_governance` gains no new behaviour and callers are unaffected. Error precedence is preserved deliberately: manifest validation still runs before the negative-argument check and the `max_seats == 0` early return, so manifest errors continue to win. The existing governance tests (five test functions, six collected cases — one is parametrized) must pass **unmodified**; they are the regression proof, and adapting them would void it. Work proceeds workbench-first, then a separate root-promotion packet written once the workbench diff is real rather than predicted.

**Rejected alternatives:** Approximating C-0-only by neutralising seat and character limits; reimplementing the allocator rather than extracting it verbatim; giving `evaluation_use_only` a default or accepting it positionally; adding an automatic ungated fallback inside the governed helper; or exposing the allocator through the CLI or MCP surface.

---

## 2026-07-26 — Governed context requires explicit C-0 manifest membership

**Status:** Accepted for the workbench implementation gate.

**Decision:** Keep ordinary `run_query` as the explicit ungated retrieval baseline. Make the separate governed-context helper require a C-0 eligibility manifest with a run identity and approved inventory; it may pass a result only when the result's document ID, resolved source path, and indexed content SHA-256 match one unique approved record. The helper stamps the consumed eligibility run ID onto the returned rows. Missing, empty, malformed, duplicate, mismatched, or status-only inputs produce no governed context rather than an eligibility fallback.

**Why:** Frontmatter status is descriptive source metadata, not a current eligibility decision. A status-only filter and its fallback could admit a source that C-0 quarantined or a source whose contents changed after approval. Exact manifest membership makes the control boundary auditable while leaving MindGraph's ranking and default retrieval contract untouched.

**Consequences:** This is an additive consumer boundary, not a database migration, default CLI/MCP behavior change, C-B bundle, or RAG-quality result. Callers that need governed context must provide a current manifest; callers intentionally running a baseline use `run_query` without this helper. The evaluation project must measure benefit separately on a frozen corpus and preregistered held-out queries.

**Rejected alternatives:** Filtering by `status` alone; falling back to all results when no approved status appears; importing the apparatus project into the engine; or claiming the boundary establishes answer quality or regulatory compliance.

---

## 2026-07-08 — Opt-in CLI/MCP envelope with legacy list compatibility

**Status:** Accepted. Workbench reconciled with root operational package on
2026-07-09 (union merge: envelope + workbench-only pruning tests retained).

**Context:** The promoted root package gained intent-resolution code before the
transport contract was finished. An intermediate state returned an object
envelope from plain `--json`, which broke existing CLI callers and tests that
depend on the long-standing JSON array of `QueryResult` records. The MCP tool
also accepted `envelope=true` but discarded the intent metadata and still
returned the legacy list.

**Decision:** Preserve the legacy list as the default machine-readable surface.
`mindgraph query --json` returns a JSON array of `QueryResult` rows. Callers
that want intent metadata must opt in with `--json --envelope` (CLI) or
`envelope=true` (MCP). Both surfaces share one envelope shape:

```json
{
  "schema_version": "1",
  "intent_resolution": { "...": "..." },
  "routing": {
    "mode": "single_database",
    "selected_retrievers": ["cli-bound-db | mcp-bound-db"],
    "reason_codes": ["intent_resolved | intent_store_missing | ..."],
    "warnings": []
  },
  "results": []
}
```

`--no-intent` suppresses resolution for text/envelope paths. The stdio server
accepts `--intent-db` so tests and operators can bind envelope metadata to a
specific compiled intent graph instead of relying only on the home default.
`routing` here is single-database metadata for the bound index, not multi-index
federation.

**Consequences:** Existing CLI and MCP integrations continue to parse list
output without change. Query Station and future trace clients have an explicit
envelope path for intent/routing metadata. This decision does not install a new
operational intent graph, merge retrieval databases, change ranking, or approve
workstation trace UI work.

**Rejected alternatives:** Returning envelopes by default; hiding intent
metadata inside individual result rows; making MCP envelope support a no-op;
or removing `--no-intent` from the compatibility contract.

---

## 2026-06-30 — Additive deterministic router core and grouped retrieval envelope

**Status:** Accepted for the first Phase 3 implementation gate in the
workbench. Operational intent installation and MainFrame integration remain
deferred.

**Context:** Phase 2 provides a deterministic, read-only `IntentResolution`,
while the shipped document engine still requires callers to choose one SQLite
store. Phase 3 needs a model-independent policy layer over the existing durable
and project query paths without changing their ranking or the legacy CLI/MCP
response list.

**Decision:** Add strict versioned route, policy, registry, batch, failure, and
envelope contracts in a separate routing module. The MindGraph package owns the
generic deterministic decision and orchestration primitives. MainFrame callers
own database paths, retriever registrations, allowed capabilities, refusal
rules, and policy versions.

An intent capability hint is a nomination only. It cannot bypass the caller
allowlist, registry availability, or refusal policy. Explicit durable, project,
and permitted two-store federated modes are safe scopes. Automatic mode may use
one configured safe default, but it never widens a no-match, ambiguous, invalid,
or unavailable intent to every retriever. An unavailable requested capability
is reported rather than silently replaced.

Each registered retriever returns its unchanged local `QueryResult` order. The
envelope wraps rows with local ranks and groups them by retriever/trust profile;
it never compares or normalizes scores across stores. Retriever failures are
isolated and visible, including when every selected retriever fails.

The existing query pipeline is wrapped through a read-only SQLite connection
that loads sqlite-vec and enables `query_only`; it does not call the current
`db.get_db` helper because that helper can persist WAL/schema changes. The
library orchestration API is additive. Existing `mindgraph query`, neighbors,
and MCP behavior remain unchanged.

**Consequences:** Phase 3 can measure routing and grouping over the two shipped
retrievers before installing an operational graph. Synchronous timeout values
are traceable budget hints, not cancellation guarantees. No model classifier,
concurrency layer, retry loop, live/structured/episodic/Gmail retriever,
operational intent database, root promotion, or workstation surface is added in
this gate.

**Rejected alternatives:** putting policy only in workstation; merging the two
document stores; globally reranking result rows; treating hints as authority;
querying both stores after ambiguity; registering future placeholders as
available; changing the legacy result shape; or claiming synchronous timeout
cancellation that the runtime cannot enforce.

---

## 2026-06-29 — Separate V1 intent graph compiler and read-only traversal

**Status:** Accepted for Phase 2 implementation in the workbench. Runtime
routing and operational installation remain deferred.

**Context:** MindGraph's durable and project databases contain ranked document
nominations. The remodel needs a procedural graph for reviewed goals,
prerequisites, constraints, and capability hints without contaminating those
indexes or making a model responsible for deterministic fallback.

**Decision:** Add a separate intent-control subsystem whose immutable source is
a directory of reviewed, versioned YAML graph files. The workbench compiles the
validated corpus into one standalone SQLite artifact containing graph-version,
node, alias, edge, binding, and deterministic-rule tables. The compiler uses
canonical semantic JSON hashes, stable row ordering, fixed SQLite creation
settings, complete pre-replacement validation, and same-directory atomic
replacement. It records no wall-clock compilation metadata and never imports
the document-index schema or sqlite-vec.

Approved version history is append-only. A new version names the version it
supersedes; generated effective status marks the single head approved and its
ancestors superseded. Recompilation must retain every previously compiled
version with the same source hash. Removal, mutation, missing lineage, or forks
fail before the destination changes.

V1 nodes are `goal`, `capability`, or `constraint`. Controlled relations are
`decomposes_to`, `requires`, `next_step`, `blocked_by`, and `routes_to`, with
kind-compatible endpoints. `decomposes_to`, `requires`, and `next_step` are
independently acyclic. Bindings are typed URI references rather than
cross-database foreign keys. Required unavailable bindings fail compilation;
optional unavailable bindings remain visible but cannot become hints.

Resolution is deterministic: trusted explicit goal ID, exact normalized goal
label/alias, highest-priority scope/token rule, then an explicit no-match
fallback. Tied highest-priority rules selecting different goals refuse. Default
traversal follows sorted `requires` edges depth-first with inclusive limits of
depth 2 and 64 nodes, returns the complete visited node/edge trace, surfaces
constraints as warnings, and emits only capability references present in an
explicit caller allowlist.

**Defensive behavior:** Alias collisions and controlled cycles are rejected in
approved source at compile time. Runtime still detects and refuses either
condition if a corrupt or non-approved store violates those invariants. This
keeps the Phase 1 ambiguity/cycle contracts meaningful without permitting an
invalid approved graph.

**Result contract:** `IntentResolution` carries graph ID/version/source hash,
resolution method, outcome, matched and prerequisite goals, node and edge path,
capability and rejected-capability hints, constraints, warnings, truncation,
and refusal fields. An additive adapter emits the existing fixture-evaluator
candidate shape without importing the evaluation project.

**Consequences:** Phase 2 creates and opens only temporary test artifacts. It
does not install `~/.mindgraph/mainframe-intent.sqlite`, add a CLI command,
change the query/MCP response shape, implement a router, or alter any retrieval
database. Operational installation and deterministic routing require their
later phase gates.

**Rejected alternatives:** Reusing the document `edges` table; embedding intent
nodes; model-first classification; automatic extraction from transcripts;
silently selecting one ambiguous alias/rule; treating a capability hint as
authority; and writing directly to the operational path during compilation.

## 2026-06-19 — Query-time semantic association (MainFrame ADR-034)

**Status:** Accepted; shipped in MainFrame commit `1292c22`.

**Context:** MindGraph uses semantic search for query-to-chunk ranking and
explicit edges for document-to-document traversal (`--expand`). Cross-domain
material can co-rank on well-formed queries but have no wikilink path, so
`--expand` and `graph_neighbors` cannot surface the relationship from one note.

**Decision:** Add an append-only `associated` signal. Starting from fused seed
documents, embed a per-document association text (title plus primary chunk), run
vector nearest-neighbor retrieval, promote results to document level, and append
them with `signal="associated"`, `semantic_distance`, and `weak_fit`. Association
does not enter RRF fusion. The CLI exposes `--associate`; MCP keeps parameter
parity. Association runs within one SQLite scope, while Query Station retains
responsibility for federated grouping.

**Rationale:** This reuses existing chunk embeddings and ranking primitives
without an offline semantic-edge table or LLM entity extraction. It closes a
document-to-document discovery gap while keeping each signal inspectable.

**Consequences:** `Signal` includes `associated`. Evaluation owns cross-domain
probes and latency measurement before association can become a default agent
workflow. Offline precomputed semantic edges remain deferred.

---

## 2026-06-19 — Hybrid explicit graph and chunk RAG (MainFrame ADR-035)

**Status:** Accepted; shipped in MainFrame commit `1292c22`.

**Context:** After dual-channel link fixes and federation research, MainFrame
needed to decide whether to replace MindGraph with GraphRAG, a vector-only
system, learned graph traversal, or a larger embedding model.

**Decision:** Keep the hybrid model as the default: per-scope SQLite with FTS5,
chunk embeddings, and explicit operator-authored edges; RRF for query-to-chunk
ranking; bounded graph expansion for explicit relationships; and semantic
association for implicit document neighborhoods. Do not replace the lifecycle
indexes with one LLM-extracted graph. Embedding and reranking changes require a
frozen `mindgraph-eval` comparison before promotion.

**Rationale:** MainFrame needs lifecycle-aware nominations rather than global
private-corpus answer generation. The current model preserves inspectable
signals and trust zones while allowing measured, incremental improvements.

**Consequences:** Association precedes embedder migration or community-graph
experiments. Optional overview layers remain opt-in. Comparative claims stay
design intent until supported by evaluation.

---

## 2026-06-19 — Dual-channel links and canonical slug resolution (MainFrame ADR-033)

**Status:** Accepted; shipped in MainFrame commit `6fee787`.

**Context:** MainFrame notes declare relationships in both frontmatter `links:`
and body wikilinks. Authors also use trailing slugs while canonical filenames
include date, domain, and type prefixes. Indexing only body links and matching
only full stems left useful relationships sparse or dangling.

**Decision:** `extract_document_graph_edges` indexes frontmatter links and body
wikilinks, deduplicating by target while preserving a body relationship label
when present. `LinkResolver` also resolves unique canonical trailing slugs after
trying scope-relative and sibling paths. Ambiguous slugs remain dangling. Either
authoring channel is sufficient; knowledge notes must not create project
wikilinks until an approved bridge registry exists.

**Rationale:** This aligns engine behavior with established MainFrame authoring
without requiring a corpus-wide rewrite. Existing frontmatter relationships can
participate in expansion immediately.

**Consequences:** Refreshes can change edge counts even when body text is
unchanged. Graph-degree baselines must be rerun after refresh. Project bridges
remain explicit future work.

---

## 2026-06-19 — Namespaced multi-root ingest and provenance rows

**Decision:** MindGraph keeps ordinary `mindgraph ingest <directory>` backward-compatible with path-derived document IDs, but adds `mindgraph ingest-many <manifest>` for multiple Markdown roots that must be indexed as one logical store. Scoped ingest rows carry `index_id`, `trust_profile`, `namespace`, `source_root`, `source_path`, and `display_path`. When `index_id` and `namespace` are present, document IDs are derived from `index_id + namespace + source_path`, so repeated filenames such as `README.md` remain distinct across project roots.

**Rejected alternatives:** Rejected looping over `ingest <project>` because each run prunes against one root and can erase other projects. Rejected a blended MainFrame graph because the knowledge/project trust boundary belongs above the engine. Rejected frontmatter-only identity because many coordination files share titles and filenames across project folders.

**Rationale:** MainFrame needs a project-context database that can index many `30_projects/<slug>/` coordination surfaces without file-name collisions or root-by-root pruning. Provenance fields make trust labels explicit for CLI, MCP, and workstation consumers instead of forcing clients to infer authority from path strings. Keeping the old single-root ID rule avoids surprising existing vault users.

**Measurement:** Engine tests cover duplicate `README.md` files in separate namespaces, scoped link resolution, union pruning, manifest loading through the CLI, and query-result provenance. MainFrame wrapper verification covers dry-run output and a temporary project DB with multiple namespaces.

**Consequences:** Query JSON remains a top-level list of rows, but each row can now carry nullable provenance fields. Clients that know MainFrame can use `source_root + source_path` for safe file links and `path`/`display_path` for human display. Future station features can group by trust profile without changing the database separation rule.

---

## 2026-06-17 — Query scope warnings for lifecycle-state requests

**Decision:** `mindgraph query` adds an additive query-scope warning when the query text appears to ask for inbox/routing state, current live state, or project-status state. The warning is copied onto each returned `QueryResult` as `query_scope_warning` and shown once in human-readable CLI output. It does not affect lexical ranking, semantic ranking, RRF fusion, graph expansion, or `weak_fit`.

**Rejected alternatives:** A response-level JSON object would be cleaner for query metadata, but it would break the current `--json` and MCP list-of-results surface. Automatic multi-DB routing is also out of scope for the engine because MindGraph only knows about the SQLite database it was handed, not the caller's lifecycle architecture or which sibling database should be trusted.

**Rationale:** The MainFrame evaluation exposed a specific trust failure: queries like "current job hunt status this week" and "latest inbox captures waiting for routing" can receive fused lexical/semantic hits from the durable knowledge index. Per-result `weak_fit` cannot catch that because the ranking signal may be strong while the scope is wrong. A query-level warning gives clients and humans a visible guardrail without pretending the engine can verify freshness or choose the right operating surface.

**Measurement:** The closeout test is fixture-bounded: scope-warning classifier tests cover inbox, live/current, project-status, and ordinary durable-knowledge queries; `run_query` tests assert that a fused result can carry a warning even when `weak_fit` is false; CLI and MCP parity tests assert the warning is visible on the existing output shape.

**Consequences:** Consumers can keep treating `mindgraph query --json` and the MCP `query` tool as a list of result rows. Clients that need response-level metadata can lift `query_scope_warning` from the first row for now. A future lifecycle router can use the same intent labels to select a project-status or live-state database before query execution.

## 2026-05-19 — MindGraph scope is retrieval, not generation or verification

**Decision:** MindGraph is a retrieval engine. It does not generate text, summarize chunks, verify claims, or attempt any answer step on top of the retrieved context. The CLI returns ranked chunks with mechanical signal attribution and stops there.

**Reasoning:** The interesting design problem for a local PKM tool is signal quality and graph integration over a single inspectable store, not a downstream answer step. Adding an LLM generation layer would shift the evaluation question from "is the right chunk surfaced" to "is the answer correct," which is a much harder problem and one the local-first framing is not equipped to solve. A reader who wants an answer step can pipe MindGraph output into their own LLM.

**Consequences:**

- Output is ranked chunks plus signal attribution. There is no `mindgraph answer` command, and there will not be one.
- The asset's epistemic claim is calibrated to retrieval: nomination, not verification. A surfaced chunk is a candidate for a reader to read, not a verified source for any downstream claim.
- The MCP wrap exposes the same retrieval surface to MCP clients. The client gets to decide what (if anything) to do with the chunks.

**Rejected alternatives:**

- Add an LLM summarization step on top of `query`. Rejected because the evaluation problem (factual correctness, faithfulness, hallucination rate) is out of scope for a local PKM asset.
- Add a claim-verification step that maps retrieved chunks to a verdict (supports, contradicts, silent). Rejected because the asset has no opinion on what claims its corpus contains and shipping one without a measurement would overstate what retrieval can do.
- Combine retrieval with a downstream content-generation pipeline. Rejected because it conflates two design problems and the result would be evaluated as a generation system, not a retrieval engine.

---

## 2026-05-19 — Single SQLite file with sqlite-vec and FTS5 as the only store

**Decision:** MindGraph uses one SQLite database file. Vector similarity comes from the `sqlite-vec` extension. Lexical search comes from SQLite's built-in FTS5 virtual table. Graph edges live in a plain SQLite table. There is no separate vector database, no graph database, and no external index.

**Reasoning:** The asset's claim depends on the system running end-to-end on one machine with no service dependency. A single SQLite file makes the asset trivially inspectable: a reader can open it with `sqlite3` and see every document, chunk, embedding, and edge. `sqlite-vec` provides vector similarity without a separate process. FTS5 ships with SQLite and provides BM25-like ranking out of the box. The graph is small enough (one row per `[[link]]`) that a plain table beats a graph engine on every axis that matters for a single-developer PKM tool: install size, query latency at the expected scale, and dependency surface.

**Schema shape (Phase 1):**

- `documents(id PRIMARY KEY, title, path, domain, content_hash NOT NULL, timeline_text, metadata_json, created_at, updated_at)`
- `documents_fts USING fts5(id UNINDEXED, title, content)`
- `chunks(rowid PRIMARY KEY, doc_id, chunk_index, text)` with `FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE`
- `vec_chunks USING vec0(embedding float[384])`
- `edges(source_id, target_id, relationship_type, PRIMARY KEY (source_id, target_id, relationship_type))`

Foreign keys are enabled on every connection with `PRAGMA foreign_keys = ON`.

**Consequences:**

- The default DB path is `mindgraph.sqlite` in the working directory. Users override with `--db`.
- `vec_chunks` is dimension-locked to 384 to match the default embedding model. Changing the model requires a schema migration and a re-ingest. This is recorded in the embedding ADR below.
- Dropping a document cascades to its chunks but not to its outgoing edges or `vec_chunks` rows. The `_delete_document_artifacts` helper does that explicitly before each upsert.

**Rejected alternatives:**

- A separate vector store (FAISS, LanceDB, Qdrant). Rejected because the dependency surface and process model do not fit a local PKM tool, and because the relative ranking performance gap is not material at PKM scale.
- A graph database (SQLite + graph extension, KuzuDB, Neo4j). Rejected because the graph is small, the queries are shallow, and a plain edge table reads cleanly in SQL.
- Splitting documents and edges across multiple SQLite files. Rejected because the inspect-by-opening-the-file property breaks once state is spread across files.

---

## 2026-05-19 — Deterministic document ID from path SHA-256, plus content hash for idempotent ingest

**Decision:** A document's `id` is the first 16 hex characters of `sha256(relative_path)`. A document's `content_hash` is the full `sha256(file_bytes)`. Re-ingest with an unchanged `content_hash` skips work.

**Reasoning:** Two properties matter. Identity must be stable so links resolve and edges remain valid across runs. Re-ingest must be cheap so the engine can run on a watch loop without redoing embedding work.

Path-derived IDs give stable identity for free. Two ingests of the same vault produce the same document IDs. A `[[notes/alice]]` link in one document resolves to the same `target_id` as `notes/alice.md`'s own `id` because both go through `compute_doc_id("notes/alice.md")`. The 16-hex truncation is long enough that birthday collisions inside a single PKM vault are not a realistic concern.

Content hashing gives idempotence. `get_document_hash` returns the stored hash, the CLI compares it to the freshly computed one, and unchanged files exit before parsing or embedding.

**Consequences:**

- Renaming a file produces a new document ID. The old document remains in the database until a future Phase prunes orphans. The README states this plainly.
- Link targets are normalized to add a `.md` suffix when missing so that `[[notes/alice]]` and `[[notes/alice.md]]` point at the same `target_id`.
- A link to a path that does not exist as a file still creates an edge. Dangling edges are a designed outcome, not a bug.

**Rejected alternatives:**

- UUIDs minted at ingest time. Rejected because re-ingest of the same vault would produce a new graph each time.
- Title-based IDs. Rejected because titles drift and collide. Paths are the durable identity.
- Full SHA-256 hex as the ID. Rejected because 16 hex characters are enough for PKM-scale vaults and the shorter ID reads better in logs.

---

## 2026-05-19 — Truth / Timeline page-model split on `---` followed by `## Timeline`

**Decision:** The body of a parsed document splits into a `truth_text` and an optional `timeline_text`. The split fires on a `---` horizontal rule on its own line, followed (across optional blank lines) by a `## Timeline` heading. The match is case-insensitive on the heading. A plain `---` HR elsewhere in the body does not trigger the split.

**Reasoning:** PKM notes often mix two kinds of content: a stable description of the concept and a dated log of changes or events. Mixing them in one chunkable body produces drift: the embedding of a note about Alice changes every time Alice's timeline grows, even when the description of Alice is identical. Splitting them at parse time keeps the embedding of Truth stable and gives the Timeline its own column for future timeline-aware queries.

The split rule is conservative on purpose. A reader writing `---` to separate paragraphs does not get a surprise split. Only the specific pattern of a rule followed by a Timeline heading is treated as a section boundary.

**Consequences:**

- Only `truth_text` is chunked and embedded. `timeline_text` is stored on the `documents` row for later use.
- The FTS5 row uses `truth_text` as `content`. Timeline content is not in the lexical index yet. Phase 2 or later may add a separate FTS row keyed by section if timeline-aware queries become useful.
- The heading is case-insensitive so `## Timeline`, `## TIMELINE`, and `## timeline` all split.

**Rejected alternatives:**

- A frontmatter flag to declare the split (e.g., `timeline: true`). Rejected because it requires the writer to remember a flag while editing notes. The structural rule is invisible to a writer who never uses a Timeline section.
- A heading-only split with no `---` requirement. Rejected because plain `## Timeline` is a heading a writer might use for any number of reasons. The `---` plus heading combination is a clearer signal.
- Splitting on any horizontal rule. Rejected because writers use `---` for visual breaks. Triggering on every rule would shred chunks across arbitrary boundaries.

---

## 2026-05-19 — Default embedding is sentence-transformers/all-MiniLM-L6-v2 at 384 dimensions

**Decision:** MindGraph embeds chunks with `sentence-transformers/all-MiniLM-L6-v2`. The `vec_chunks` virtual table is dimension-locked at `float[384]` to match. The model loads lazily on the first chunk in an ingest run.

**Reasoning:** MiniLM at 384 dimensions is small, CPU-friendly, and has acceptable retrieval quality for PKM-scale corpora. It downloads once, caches under the standard sentence-transformers cache, and runs without a GPU. A larger model would improve retrieval quality at a cost the asset's claim does not need to absorb: longer first-run latency, more disk, and a CPU/GPU split that would make the "runs anywhere" framing less honest.

Lazy loading matters because the most common ingest case after the first run is a no-op: every file hashes the same, every file skips. Loading a 100 MB model only to skip every file would make the watch-loop story unpleasant.

**Consequences:**

- Switching models requires a schema migration on `vec_chunks` and a re-embed of every chunk. There is no graceful in-place upgrade. The README states this plainly.
- The 384 dimension is hard-coded in the schema. Future work that wants a configurable dimension will need a versioned `vec_chunks_<dim>` pattern or a settings table.
- First ingest on a fresh machine downloads the model. The CLI logs a single line announcing the load so users know what the delay is.

**Rejected alternatives:**

- bge-small-en or bge-base-en. Rejected for v0.1 because MiniLM is faster on CPU and the quality gap is not material at PKM scale. Worth revisiting if a measured comparison says otherwise.
- A larger MiniLM variant or e5-base. Rejected for the same reasons plus larger disk and load times.
- Per-document configurable embedding model. Rejected because the resulting vector space would be incoherent across documents and ranking would stop being meaningful.

---

## 2026-05-19 — Graph edge syntax is `[[target]]` and `[[target]] (relationship)`

**Decision:** MindGraph extracts graph edges from two link forms inside the Truth text: `[[target]]` produces an edge with `relationship_type = None`, and `[[target]] (relationship)` produces an edge with `relationship_type = "relationship"`. The `target` is normalized to add `.md` when missing.

**Reasoning:** The plain `[[target]]` form is standard across Obsidian, Foam, and similar PKM tools, so vaults written elsewhere work without modification. The `(relationship)` suffix is a lightweight extension that lets a writer declare typed edges without adopting a heavier syntax. The relationship type is free text on purpose so that vault authors are not forced into a controlled vocabulary.

The link regex deliberately rejects nested brackets so that a malformed `[[link[inner]brackets]]` does not produce a corrupt edge. The parser test suite (`test_parser.py::TestExtractGraphEdges`) pins this behavior with a fixture.

**Consequences:**

- Multiple edges between the same two documents with the same relationship type collapse to one row because the `edges` primary key is `(source_id, target_id, relationship_type)`.
- Edges with the same source and target but different relationship types are kept separately. A note can `[[alice]] (knows)` and `[[alice]] (mentors)` at the same time.
- Dangling edges are stored. The graph table carries them so traversal can surface broken links rather than silently dropping them.

**Rejected alternatives:**

- A separate frontmatter block for typed relationships. Rejected because it pulls the graph out of the prose, which is exactly where the writer is already declaring relationships.
- A controlled vocabulary for `relationship_type`. Rejected for v0.1 because a fixed vocabulary would force vault authors to translate, and the asset's claim does not need it. Worth revisiting if graph-aware ranking benefits from canonical types.
- A separate edge form for "no relationship". Rejected because `None` is the natural sentinel and SQL `NULL` carries it cleanly.

---

## 2026-05-19 — Phase 2 query path: lexical plus semantic plus Reciprocal Rank Fusion, with graph as a sibling lookup

**Decision:** The Phase 2 `mindgraph query` command runs two retrieval signals over the existing SQLite store and fuses them with Reciprocal Rank Fusion. Lexical retrieval uses FTS5 BM25 over the `documents_fts` table. Semantic retrieval uses `sqlite-vec` cosine distance over `vec_chunks`. The fused ranking is the default output. Every returned chunk carries a `signal` label so a reader can see which signal nominated it. Graph traversal is exposed in Phase 2 as a separate `mindgraph neighbors <doc_id>` lookup. It is not yet a ranking signal. Phase 3 wires the graph into ranking through `mindgraph query --expand`.

**Reasoning:** MindGraph's claim is that it combines vector, lexical, and graph signals over one inspectable store. RRF over lexical and semantic delivers the first two with the smallest amount of code that still gives a reader something to read. RRF works on ranks, not raw scores, so I do not need to normalize BM25 against cosine distance and explain a tuned weight. The constant `k = 60` is the canonical value from Cormack, Clarke, and Buettcher (2009) and is the value most production systems use as a default. Picking a non-canonical k would create a number to defend without measurement.

Holding the graph out of ranking until Phase 3 is honest. The graph adds value when there is something to expand into, and the natural shape is recursive: walk from a seed document along typed edges to a bounded depth, then merge the walk into the ranked result set. That work is its own design problem (depth bound, edge-type filtering, deduplication, rerank semantics) and bundling it into Phase 2 would weaken both phases. Phase 2 ships the graph table as a queryable lookup so a reader can verify the edges are real, and Phase 3 ships the ranking integration when the design is settled.

**Scope:**

In Phase 2:

- `mindgraph query <text>` over a database produced by `mindgraph ingest`
- FTS5 BM25 retrieval over the chunked Truth text
- `sqlite-vec` cosine retrieval over `vec_chunks`
- RRF fusion at `k = 60`
- Signal attribution per result: `lexical`, `semantic`, or `fused`
- Human-readable CLI output by default, `--json` for machine output, both formats deterministic
- `mindgraph neighbors <doc_id>` for direct graph inspection
- Fixture-bounded tests under `tests/test_query.py` and `tests/test_neighbors.py` that exercise lexical-only, semantic-only, fused, and graph-lookup paths

Out of scope for Phase 2:

- Graph as a ranking signal. Deferred to Phase 3 (`--expand`).
- Reranking with a cross-encoder. Out of scope for this asset.
- Query-time chunk rewriting or LLM rewriting of the query. Out of scope by ADR 1.
- Configurable embedding model at query time. The query embedder is the same model the database was built with (ADR 5).

**Retrieval pipeline:**

1. The CLI receives a query string and a database path.
2. It opens the database through `db.get_db` so foreign keys are on and `sqlite-vec` is loaded.
3. It tokenizes the query for FTS5: splits on whitespace, strips FTS5 operator characters (`"`, `*`, `-`, `(`, `)`, `:`, `^`, `NEAR`, `AND`, `OR`, `NOT`), and joins the remaining tokens with spaces. The resulting MATCH expression is an implicit OR over surviving tokens, which is the safest default for free-text input.
4. It runs an FTS5 `MATCH` against `documents_fts` with `ORDER BY bm25(documents_fts) ASC LIMIT lexical_top_k`. Default `lexical_top_k = 20`.
5. It embeds the query string with `_load_embedder()` (the same MiniLM model the ingest path uses). It runs a `vec_chunks` KNN with `LIMIT semantic_top_k`. Default `semantic_top_k = 20`.
6. It builds two ranked lists. The lexical list is keyed by `doc_id` (FTS5 ranks documents). The semantic list is keyed by `(doc_id, chunk_index)` (the vector index ranks chunks). For RRF I promote the semantic ranking to `doc_id` granularity by keeping the best-ranked chunk per document, so the fusion is over documents and the surfaced chunk per document is the best-ranked one across signals.
7. It computes `rrf_score(doc) = sum_over_signals(1 / (k + rank(doc, signal)))` with `k = 60`. Documents missing from a signal contribute zero from that signal.
8. It sorts by `rrf_score` descending. Ties break by `(doc_id, chunk_index)` lexicographic so the output is deterministic.
9. It returns `final_top_k` results. Default `final_top_k = 10`. The CLI exposes `--lexical-top-k`, `--semantic-top-k`, and `--top-k` overrides.

**Signal attribution rule:**

- `lexical` when the document is in the lexical list and not in the semantic list
- `semantic` when the document is in the semantic list and not in the lexical list
- `fused` when the document is in both lists

The signal field is informational. RRF does not weight a `fused` result more strongly than the math already says; the math itself is what produces the boost.

**Output shape (deterministic, both formats):**

Per-result fields: `doc_id`, `chunk_index`, `path`, `title`, `signal`, `rrf_score`, `lexical_rank`, `semantic_rank`, `chunk_text`. The two rank fields are integers or `null` to make the signal attribution mechanically verifiable. The `rrf_score` is a float reported to six decimal places. Documents and chunks come from the live database at query time, so any unreferenced chunk in a stale database fails the foreign-key check rather than silently returning bad data.

The `neighbors` command output: per-edge `source_id`, `target_id`, `relationship_type`, plus the resolved `source_path` and `target_path` (which may be `null` for a dangling edge). Sorted by `(target_id, relationship_type)`.

**Determinism rules:**

- `k = 60` (RRF) is fixed in code, not configurable.
- Top-k values are configurable via CLI flags. Their defaults are written into `cli.py` constants so a single read of the file gives a reader the full ranking spec.
- Ties break by `(doc_id, chunk_index)` lexicographic in the final ranking. Inside each signal's ranking, ties already break by row order from the SQL query, which is itself deterministic for a fixed schema.
- The query embedding is reproducible because the embedding model is pinned in `cli.py` (ADR 5) and the input string is passed through verbatim.

**Exit measurement (what closes Phase 2):**

A fixture-bounded test suite under `tests/test_query.py` and `tests/test_neighbors.py` that:

1. Builds a small fixture vault under `tests/fixtures/query/` with documents designed to exercise each signal: one document that wins on lexical only, one that wins on semantic only, one that wins on fused, and one that has graph edges to a known neighbor.
2. Ingests the fixture into a temporary database.
3. Runs `mindgraph query` and asserts that the top result, the signal attribution, and the rank fields match the expected values for each scenario.
4. Runs `mindgraph neighbors` against the fixture and asserts the returned edges match the expected set, including one dangling edge to confirm the graph table preserves them.

The phase closes when the suite passes and the README's "What this does" and "What this does not do" sections are updated to reflect the shipped query surface.

**Consequences:**

- A new `src/mindgraph/query.py` module owns the ranking pipeline. The CLI command in `cli.py` becomes thin.
- A new `src/mindgraph/models.py` entry `QueryResult` joins `ParsedDocument` and `GraphEdge` as a Pydantic model.
- The query path adds a runtime dependency on the embedder model loader. The CLI logs the same "Loading embedding model" line on first query that ingest logs on first chunk, so the latency is visible to the reader.
- A future graph ranking integration in Phase 3 will need an ADR amendment here to lock the merge semantics. The ranking output shape will not change; the `signal` enum will gain `graph` and `expanded`.
- The Phase 2 exit-criteria phrase "each signal at least once" is satisfied by the graph signal living in `neighbors` rather than in the ranked output.

**Rejected alternatives:**

- A weighted linear combination of normalized lexical and semantic scores. Rejected because it requires picking and defending a weight without measurement. RRF sidesteps the problem by working on ranks.
- A learned-to-rank reranker (cross-encoder, LLM judge, or similar) at the top of the pipeline. Rejected for v0.1 because it adds a model dependency the README cannot stand behind without a fixture-bounded comparison.
- A non-canonical RRF k. Rejected because a chosen-by-feel k is a number to defend in interviews and a freed-up rule to drift on later. The canonical k is the right default until a measurement says otherwise.
- Combining FTS5 and `sqlite-vec` results inside a single SQL query through a window-function rank merge. Rejected because the SQL is hard to read and the Python-side RRF makes the signal attribution mechanically visible.
- Treating each chunk as a separate ranking unit at fusion time. Rejected because FTS5 ranks documents, not chunks, so the two rankings need to align at the same granularity. Promoting the semantic ranking to document granularity and surfacing the best-ranked chunk is the cleaner symmetry.
- Shipping the graph traversal as part of Phase 2. Rejected because the design choices (depth bound, edge-type filter, merge semantics) are large enough to deserve their own ADR and their own measurement.

---

## 2026-05-20 — Phase 3 graph expansion: outbound walk from Phase 2 seeds, bounded depth, appended results with `expanded` signal

**Decision:** The Phase 3 `mindgraph query --expand` command runs the Phase 2 query path first, then walks outbound `[[link]]` edges from each Phase 2 result to a bounded depth, and appends the walked documents to the result list with `signal = "expanded"` and a new `expansion_depth` integer field on every `QueryResult`. The walk follows the existing `edges` table in the source-to-target direction only. Dangling edges terminate the walk at their depth. Documents already present in the Phase 2 result set are not re-added by the walk. The walk does not affect the Phase 2 ranking; expanded documents follow the Phase 2 block, ordered by `(expansion_depth ASC, doc_id ASC, chunk_index ASC)`.

**Reasoning:** MindGraph's claim is that it combines vector, lexical, and graph signals over one inspectable store. Phase 2 shipped the first two. Phase 3 ships the third in the form most honest to the data: a labeled append rather than a hidden boost. A reader who reads the result list can tell at a glance which documents came from retrieval and which came from a graph walk. That separation is the design lock; everything else in this ADR follows from it.

Outbound-only for v0.1 is honest about what we have. The `edges` table stores directed `source → target` edges as written by note authors. Bidirectional walks (following backlinks from target back to source) are a meaningful PKM feature and worth their own future ADR, but adding them to v0.1 would conflate two design questions. The dataset to measure their value against is a future Phase 4 deliverable.

A bounded depth of 1 by default is also honest. One-hop expansion captures the strongest graph signal in a typical PKM vault (the notes a reader explicitly linked to). Deeper walks add reachability noise that grows with the vault's average outdegree. Configurable up to depth 3 lets a reader experiment without making the default explode.

Appending, not interleaving, keeps the math simple. Reciprocal Rank Fusion is well-understood for lexical plus semantic ranking. Folding a "graph proximity score" into the RRF math would require defining a per-edge weight, picking a graph-distance-to-rank mapping, and defending that math without a measurement. None of that is justified for v0.1.

**Scope:**

In Phase 3:

- `mindgraph query <text> --expand` enables outbound graph expansion from Phase 2 results
- `--depth N` controls walk depth (default `1`, hard cap `3`)
- `--expand-top-k N` caps the appended expanded-result count (default `20`)
- `expansion_depth: int` field added to `QueryResult`. Phase 2 results carry `expansion_depth = 0`. Walked results carry the walk distance from any Phase 2 seed.
- `signal` literal type widened to include `"expanded"`. Walked documents that did not appear in either Phase 2 ranking carry `signal = "expanded"`. Walked documents that were already in the Phase 2 result set keep their existing signal and `expansion_depth = 0`.
- Fixture-bounded tests under `tests/test_expand.py` (or extension of `tests/test_query.py`) exercising: one-hop walk from a seed, multi-hop walk to depth 2, dangling-edge termination, dedup of a walked doc against a Phase 2 hit, depth=0 producing the same result as no `--expand`, and the `--expand-top-k` cap

Out of scope for Phase 3:

- Backlink walks (target-to-source). The walk follows `source → target` only.
- Relationship-type filters (`--rel <type>`). All edges are followed regardless of `relationship_type`.
- Graph signal in the RRF fusion. Expansion appends; it does not rerank.
- Chunk-level selection for expanded documents. Expanded documents surface `chunk_index = 0`, consistent with the lexical-only fallback already documented in the Phase 2 ADR.
- Orphan pruning of stale documents reachable via the walk. A future Phase will add a `prune` command.
- Re-ingest required: the schema does not change. Existing databases work as-is.

**Walk algorithm (deterministic):**

1. Run the Phase 2 query path to produce the ranked list of `QueryResult` records. Each carries `expansion_depth = 0`.
2. If `--expand` is not set, return the Phase 2 list.
3. Otherwise, initialize `frontier = [seed.doc_id for seed in phase2_results]` and `seen = set(frontier)`.
4. For each depth `d` from 1 to `--depth`:
   - Initialize `next_frontier = []`.
   - For each `source_id` in `frontier`:
     - Query `edges` for outbound edges. Resolve each `target_id` against `documents`. Skip dangling targets (no `documents` row).
     - For each resolved target not in `seen`: add to `next_frontier`, add to `seen`, and emit an `expanded` `QueryResult` with `expansion_depth = d`.
   - Set `frontier = next_frontier`. If empty, stop early.
5. Sort the expanded results by `(expansion_depth ASC, doc_id ASC, chunk_index ASC)`. Truncate to `--expand-top-k`.
6. Concatenate: Phase 2 results first, expanded results second. Return.

The walk uses the existing `query.list_neighbors` function as a primitive. The dedup uses the `seen` set seeded by the Phase 2 result doc_ids.

**QueryResult schema growth:**

The `QueryResult` Pydantic model gains one field:

```
expansion_depth: int  # 0 for Phase 2 results; ≥ 1 for expanded results
```

The `Signal` literal widens:

```
Signal = Literal["lexical", "semantic", "fused", "expanded"]
```

JSON output adds the `expansion_depth` field on every result row. Text output appends `depth=N` to the result block header when `expansion_depth > 0` and uses the existing signal label otherwise.

**Determinism rules:**

- Phase 2 results retain their RRF ordering and tie-break rules (per the Phase 2 ADR).
- Expanded results sort by `(expansion_depth ASC, doc_id ASC, chunk_index ASC)`. Lexicographic on `doc_id` is the same tie-break the Phase 2 ADR already uses.
- The walk's BFS order does not affect output ordering because the final sort dominates.
- Dangling edges are skipped during the walk, not appended as expanded results with `null` target paths. (`mindgraph neighbors` still surfaces dangling edges directly; the difference is that expansion produces `QueryResult` rows, which require a real `path` and `title`.)

**Exit measurement (what closes Phase 3):**

A fixture-bounded test suite that:

1. Builds a small vault under `tmp_path` with a known edge topology: seed `A` links to `B` (cites), `B` links to `C` (refers), `C` links to nothing, plus a dangling `[[missing]]` edge from `A`. Plus an unrelated doc `D` with no edges.
2. Ingests the fixture into a temporary database.
3. Runs `mindgraph query "<term matching A>" --expand --depth 1`: asserts that `A` is in the Phase 2 block with `expansion_depth = 0`, and `B` is in the expanded block with `signal = "expanded"` and `expansion_depth = 1`. `C` and `D` and the dangling target are absent.
4. Runs the same query with `--depth 2`: asserts that `B` and `C` are both expanded, with `expansion_depth = 1` and `2` respectively.
5. Runs with `--depth 0` or without `--expand`: asserts the result matches the Phase 2-only output exactly.
6. Runs a query where the Phase 2 result already contains a doc that would be a walk target: asserts no duplicate, and the existing entry keeps its `lexical`/`semantic`/`fused` signal.
7. Runs with `--expand-top-k 1` from a seed with multiple walked neighbors: asserts only one expanded result is appended.

The phase closes when the suite passes and the README's "What this does not do yet" section drops the graph-not-a-ranking-signal bullet.

**Consequences:**

- `src/mindgraph/query.py` gains an `expand_results` function and a small change to `run_query` so the CLI can opt in. The Phase 2 functions (`fetch_lexical_ranking`, `fetch_semantic_ranking`, `rrf_fuse`, `list_neighbors`) are unchanged.
- `src/mindgraph/models.py` adds the `expansion_depth` field and widens `Signal`. Existing tests that asserted on `signal in ("lexical", "semantic", "fused")` need to add `"expanded"` to the allowed set when they pass `--expand`-like inputs.
- The CLI gains `--expand`, `--depth`, and `--expand-top-k` on the `query` command. The default behavior without `--expand` is unchanged.
- The JSON output schema gains the `expansion_depth` field. Consumers reading the schema strictly must update.
- No database migration. The `edges` table is queried as-is.
- The total result count for an `--expand` invocation is bounded by `top_k + expand_top_k`. Without `--expand`, the bound is still `top_k`.

**Rejected alternatives:**

- Folding graph proximity into RRF as a third signal. Rejected because picking a per-edge weight and a graph-distance-to-rank mapping requires a measurement that does not exist for v0.1. Appending preserves the math we already understand and pushes the harder design problem to a future ADR with measurement to back it.
- Bidirectional walks (outbound plus backlinks) in v0.1. Rejected because backlinks are a real PKM feature that deserves a measurement and its own ADR, and bundling it into Phase 3 would muddle the design lock.
- Relationship-type filters (`--rel cites`). Rejected for v0.1 because a controlled vocabulary for `relationship_type` is itself a future ADR (per the Phase 1 edge-syntax ADR), and there is no measurement showing a default filter would help.
- Unbounded walk depth. Rejected because reachability fans out quickly and the result set explodes. A hard cap of 3 makes runaway walks impossible.
- Chunk-level selection for expanded documents (run the query embedding against the expanded doc's chunks to pick the best one). Rejected for v0.1 to keep the expansion path simple and the chunk choice consistent with the lexical-only fallback already in the codebase.
- Replacing Phase 2 results with walked results when the walk produces a "closer" document by graph distance. Rejected because the result list would no longer be honest about why each document is there. Append, label, sort. The reader judges.

---

## 2026-05-21 — Phase 4 public packaging

**Decision:** Phase 4 ships a committed example vault, a real-output "Try it" walkthrough in the README, a CI-safe smoke test that exercises every retrieval path, and a documented entry point for the optional Phase 5 (MCP wrap). Phase 4 is content and docs only. No source changes, no schema changes, no new ranking signals, no new CLI flags.

**Reasoning:** Phases 1, 2, and 3 shipped the feature surface. The asset is feature-complete enough to package. MindGraph's claim ("local retrieval engine that combines vector, lexical, and graph signals over one SQLite file") is intelligible from the current README but a reader has no way to run it without inventing their own vault. A committed example vault, a "Try it" walkthrough with real captured output, and a smoke that exercises every signal class let a reader clone the repo and observe each retrieval path in a single sitting.

**Scope:**

In Phase 4:

- A committed example vault under `examples/example-vault/` with seven Markdown notes
- One paragraph `examples/README.md` pointing at the asset README's walkthrough
- A `## Try it` section in the asset README with real captured output blocks from a fresh run
- A Phase 5 entry-point sentence in the asset README pointing at the optional MCP wrap
- A writing-skill polish pass over the asset README and a re-verification of the "What this does not do yet" bullets after Phase 3
- A `tests/test_examples.py` using the `KeywordEmbedder` stub from `tests/test_query.py` (CI-safe)
- A `scripts/run_example_smoke.py` using the real MiniLM model that produces the README captures

Out of scope for Phase 4:

- MCP wrap. That is Phase 5 (optional, deferred).
- Any new ranking signal, new flag on `mindgraph query`, or new schema.
- Backlink walks, relationship-type filters, orphan pruning, chunk-level selection for expanded docs, RRF folding for graph proximity. All locked out by the Phase 3 ADR.
- Comparative claims against any external tool. The README evidence standard already rules these out without measurement.
- Live retrieval against any vault that has not been committed to the repo. Examples must be reproducible.
- Screenshots. The handoff prompt makes these explicitly optional. Text captures are sufficient.

**Example-vault topology:**

Seven files under `examples/example-vault/`. The systems-thinking domain matches the existing Phase 3 test fixture vocabulary and stays abstract enough to avoid invented domain claims a reader could fact-check.

| File | Role in retrieval coverage | Edges |
|---|---|---|
| `feedback-loops.md` | Seed for graph expansion | `[[reinforcing-loops]] (illustrates)`, `[[balancing-loops]] (illustrates)` |
| `reinforcing-loops.md` | One-hop expansion target from seed | `[[systems-archetypes]] (related)` |
| `balancing-loops.md` | One-hop expansion target; fused-hit candidate | `[[systems-archetypes]] (related)` |
| `systems-archetypes.md` | Two-hop expansion target; dangling-edge source | `[[unicycle-mental-model]] (cites)` (target intentionally absent) |
| `mental-models-overview.md` | Lexical-only doc; unique keyword surfaces only via FTS5 | `[[feedback-loops]] (related)` |
| `bounded-rationality.md` | Semantic-only doc; paraphrases the seed without sharing tokens | `[[feedback-loops]] (related)` |
| `unrelated-noise.md` | Noise; should not appear in seed queries | (none) |

Each file is one to three short paragraphs. Every retrieval-path file carries at least one `[[link]] (relationship)` edge.

Coverage map:

- Lexical-only path: `mental-models-overview.md` matched on its unique keyword.
- Semantic-only path: `bounded-rationality.md` matched on paraphrased concepts the embedding model picks up without shared tokens.
- Fused path: `balancing-loops.md` matches both signals against a query like `balancing feedback`.
- Dangling edge: `mindgraph neighbors systems-archetypes` surfaces the unresolved `unicycle-mental-model` target.
- Graph expansion: `mindgraph query "feedback loops" --expand --depth 2 --top-k 1` reaches `reinforcing-loops.md` and `balancing-loops.md` at depth 1, then `systems-archetypes.md` at depth 2.

**Captured-output policy:**

README captures come from a real-model run, not the `KeywordEmbedder` stub. Honest output beats convenient output. The first capture block keeps the `Loading embedding model (all-MiniLM-L6-v2)...` log line so a reader sees the first-run latency cost up front. Subsequent blocks filter that line to reduce noise. Captures regenerate by re-running `scripts/run_example_smoke.py`. If a future change to the example vault shifts ranking, the script is the single command that refreshes the README.

**Smoke shape:**

Both a script and a test. The two files do different jobs.

- `scripts/run_example_smoke.py` uses the real MiniLM model. It runs the handoff prompt's "Smoke commands" sequence against the committed vault into a temp database and prints fenced Markdown blocks formatted for direct paste into the README. It is not run in CI.
- `tests/test_examples.py` reuses the `KeywordEmbedder` stub from `tests/test_query.py` for deterministic semantic similarity. It asserts the expected retrieval surface per path against the committed vault under `tmp_path`. It runs in CI with no HuggingFace network dependency.

The cost is two small files. The benefit is honest captures in the README plus permanent CI coverage of the example vault topology.

**README structure changes:**

Additive. The current section order (description, "What this does", "What it ranks", "What this does not do yet", "Useful commands", "Architecture", "Test discipline", "Planning") satisfies limits before capabilities in spirit (limits land before the deeper capability sections) and the handoff prompt is explicit that Phase 4 is polish, not redesign.

- Insert `## Try it` after `## Useful commands`. Real captured blocks for `init`, `ingest`, lexical-only query, semantic-only query, fused query, expand query, and `neighbors`. Closes with one sentence noting that `scripts/run_example_smoke.py` regenerates the captures.
- Insert the Phase 5 entry-point sentence in the existing `## Planning` section.
- Polish pass over the whole file per the writing skill: em-dash scan, banned-filler scan, calibrated-language scan, trim any internal-planning language.
- Verify the six "What this does not do yet" bullets still hold after Phase 3. The Phase 3 close already dropped the graph-not-a-ranking-signal bullet. The remaining bullets (no LLM generation, rename produces a new ID, Markdown-only, embedding-model swap requires migration, lexical-only chunk choice, retrieval is nomination) are accurate.

**Consequences:**

- `examples/` is a first-class directory. Future content that ships with the asset adds subdirectories or sibling files here.
- `scripts/` is a first-class directory. Operator scripts (smoke runners, capture regenerators) live here.
- The committed example vault is part of the asset's reproducibility surface. Changing a vault file changes the README captures, which means `scripts/run_example_smoke.py` must be re-run and the captures replaced.
- `tests/test_examples.py` is part of the test floor. Any future change to the example vault that changes ranking must update the test assertions as well as the README captures.

**Rejected alternatives:**

- A larger example vault (10+ files) demonstrating multiple themes. Rejected because a vault a reader cannot inspect in one sitting fails the "Try it" goal. Seven files exercise every retrieval path with enough room for short readable notes.
- Captures from the `KeywordEmbedder` stub instead of the real model. Rejected because the README's job is to show what a reader will see when they run the code. The stub embeddings would produce different ranks and signal labels than the real model, which would be a quiet form of dishonesty.
- A test-only smoke (no real-model script) or a script-only smoke (no CI coverage). Rejected because each option drops a discipline the other provides. The dual approach is two small files for two real properties: honest captures and permanent CI coverage.
- Reorganizing the README to put "What this does not do yet" before "What this does". Rejected because the current order already lands limits before the deeper capability sections, and Phase 4 is polish, not redesign.
- Screenshots. Rejected for v0.1 because text captures are sufficient and screenshots add maintenance cost (terminal theme drift, path noise, screenshot regeneration on every vault change).
- Live retrieval against a user's real PKM vault as an example. Rejected because examples must be reproducible from a clone. A committed vault is the only honest form.

---

## 2026-05-22 — Phase 5 MCP wrap

**Decision:** Phase 5 adds a thin stdio MCP server around the existing MindGraph retrieval surface. The server exposes two MCP tools: `query`, which calls `query.run_query`, and `graph_neighbors`, which calls `query.list_neighbors`. The server does not add retrieval behavior, change ranking semantics, add schema, or change the existing CLI `query` and `neighbors` command signatures.

**MCP SDK choice:** MindGraph uses the official Python MCP SDK (`modelcontextprotocol/python-sdk`, package name `mcp`). The SDK supports multiple transports. Phase 5 uses stdio only.

**Tool split:** Both surfaces are tools, not resources. `query` takes parameters and produces a new ranked nomination for each call. `graph_neighbors` also takes a `doc_id` and produces a lookup result from the current database. Neither surface has a stable URI shape. The existing CLI already presents both as commands, so the MCP shape mirrors that mental model.

**Transport:** The server is stdio only. It is compatible with stdio-MCP-aware clients such as Claude Code, Claude Desktop, Cursor, Cline, and similar local clients. claude.ai web is not compatible with this transport because it requires a remote transport such as Streamable HTTP or SSE over HTTPS. Direct claude.ai compatibility is deferred to a future optional Phase 5b.

**Embedder lifecycle:** The MiniLM model loads once at server start, before the MCP handshake completes. This is different from ingest, which loads lazily because no-op re-ingest should stay cheap. The MCP server is a long-running local process. Eager loading pays the model cost up front, keeps the first `query` tool call fast, and keeps the familiar `Loading embedding model (all-MiniLM-L6-v2)...` log line visible at startup.

**DB resolution:** `serve-mcp` takes one `--db` path. The server opens that SQLite file at startup through `db.get_db`, so foreign keys are enabled and `sqlite-vec` is loaded, and it keeps the connection open for the process lifetime. A missing or unreadable DB fails server start with a clean stderr error. The server does not half-start and then surface database errors one tool call at a time.

**Tool surface:** `query` mirrors the CLI JSON surface:

```
question: str
lexical_top_k: int = 20
semantic_top_k: int = 20
final_top_k: int = 10
expand: bool = false
expand_depth: int = 1
expand_top_k: int = 20
```

It returns a list of `QueryResult.model_dump()` records, the same shape as `mindgraph query --json`.

`graph_neighbors` takes:

```
doc_id: str
```

It returns a list of `NeighborResult.model_dump()` records, the same shape as `mindgraph neighbors --json`, including dangling edges with `target_path = null`.

**Error semantics:** `MindgraphError` and `QueryError` map to explicit MCP tool errors with `isError: true` and a text message that describes the error. Unexpected exceptions are not converted into normal MindGraph payloads. The server logs them to stderr with traceback context, then lets the MCP SDK surface the failed tool call through its error channel while keeping the stdio server alive.

**Logging:** All MCP server logs go to stderr only. Stdout is reserved for the stdio MCP protocol, and log writes to stdout would corrupt the transport. Default level is `INFO`. `--verbose` lifts to `DEBUG`. The eager-load log line is preserved because first-run model cost is part of the local operating behavior a reader should see.

**Testing commitment:** Phase 5 ships fixture-bounded tests under `tests/test_mcp.py` using the MCP SDK and the deterministic `KeywordEmbedder` stub from `tests/test_query.py`. Coverage includes:

1. server-start happy path,
2. server-start with a missing DB surfacing a clean error,
3. `query` tool shape matching `mindgraph query --json` on the same inputs,
4. `graph_neighbors` tool shape matching `mindgraph neighbors --json` on the same inputs,
5. `query` with `expand=True`, `expand_depth=2`, and `expand_top_k` returning expanded rows with the expected `signal` and `expansion_depth`,
6. `graph_neighbors` preserving a dangling edge as `target_path = null`,
7. unknown `doc_id` in `graph_neighbors` surfacing a clean MCP tool error rather than a Python traceback.

The full pytest suite, previously 88 passing tests after Phase 4, must stay green with the new MCP tests added.

**README structure:** The README gains a new `## MCP` section after `## Try it`. The section documents how to start the server, provides a `.mcp.json` snippet for Claude Code, describes the two tools and their parameter lists, gives one concrete example per tool, and states the claude.ai web limitation plainly. Captured output follows the Phase 4 policy: smoke output comes from a real run against the committed example vault, not invented output.

**Consequences:**

- `src/mindgraph/mcp_server.py` becomes the transport boundary. It owns MCP registration and serialization but not retrieval logic.
- `mindgraph serve-mcp --db <path>` is additive. Existing `init`, `ingest`, `query`, and `neighbors` behavior stays unchanged.
- The server holds one SQLite connection and one embedder for its lifetime. Multi-DB routing is out of scope.
- A future Phase 5b can add Streamable HTTP if claude.ai web compatibility becomes needed, but that future phase needs its own ADR for bind address, transport security, and deployment boundary.

**Rejected alternatives:**

- Expose `query` as a tool and `graph_neighbors` as a resource. Rejected because both require call-time parameters and both mirror command-style CLI surfaces.
- Add HTTP, SSE, or WebSocket transport in Phase 5. Rejected because a remote transport would either expose the user's local SQLite-backed knowledge base over the public internet or require hosted deployment. Both paths conflict with the local-first framing in the scope ADR and the single-SQLite-store ADR.
- Load the embedder lazily on the first `query` tool call. Rejected because stdio clients expect tool calls to return promptly after the server is listed. Eager load moves the known model cost to startup and logs it where the user can see it.
- Accept a `db_path` parameter on each tool call. Rejected because it creates multi-DB routing and error handling that the current phase does not need.
- Convert all tool errors into successful JSON payloads with an `error` key. Rejected because MCP already has a tool-error channel and clients know how to surface it.

## 2026-05-31 — Scope-aware wikilink edge resolution

**Decision:** Ingest resolves `[[link]]` edge targets against the full Markdown scope before computing `target_id`. Resolution order is exact scope-relative path, same-directory bare filename, globally unique stem, then globally unique document title. If no candidate is found, or if the global stem/title candidate is ambiguous, MindGraph preserves the existing dangling-edge behavior by hashing the normalized raw target.

**Rationale:** Document IDs are hashes of scope-relative paths. Bare wikilinks from MainFrame notes usually name a file stem, not the full domain path, so hashing the raw label produced target IDs that did not match stored document IDs. Scope-aware resolution keeps existing document identity stable while making graph edges useful for domain-scoped vaults.

**Compatibility:** `extract_graph_edges` keeps its previous behavior when no resolver is provided. CLI ingest is the only path that builds the scope resolver. Dangling edges remain first-class query results in `neighbors`, and graph expansion still stops at unresolved targets.

**Re-ingest behavior:** Unchanged documents skip chunking and embedding, but their outbound edges are replaced during ingest. This lets an existing database repair graph edges after resolver improvements or after a newly added target document appears.

**Rejected alternatives:**

- Require source Markdown to use full paths in every wikilink. Rejected because it would mutate user vault conventions and MainFrame already has body links that carry enough information to resolve safely.
- Change document IDs to stem-based IDs. Rejected because it would break existing databases and make duplicate filenames across domains unsafe.
- Read `links:` frontmatter as the primary graph surface. Rejected for this change because body wikilinks are already the active graph syntax, and frontmatter links can be added later without changing the resolver contract.

---

## 2026-06-14 — Phase 6 engine hardening: defect fixes with no new response surface

**Decision:** Implement the four defect fixes nominated by the 2026-06-12 MainFrame engine audit as a hardening pass that does not change the result schema: (1) an allowlist FTS5 sanitizer plus semantic-only degradation, (2) orphan pruning on ingest, (3) oversized-paragraph chunk splitting, and (6) WAL journal mode + conditional edge-write + a scope-guarded post-commit hook. The audit's items 4 (emit frontmatter metadata) and 5 (weak-fit/no-answer signaling), which change the response surface, are deferred to a separate Phase 7 (trust + ranking).

**Rationale:** Items 1 and 2 break the tool outright rather than merely degrade ranking — natural-language punctuation hard-failed the entire query through both the CLI and the MCP tool, and deleted/renamed files persisted in the index and kept returning confident results. Fixing the hard failures first protects trust more immediately than the calibration work, and each fix is small and self-contained. Holding the result schema fixed keeps existing MCP/JSON consumers unaffected.

**Behavior changes:**

- `sanitize_fts5_query` now extracts `\w+` tokens (unicode-aware) and drops the uppercase operator keywords, rather than substituting a denylist of operator characters. Every surviving token is a valid FTS5 bareword, so an OR-join can never produce a MATCH syntax error. The empty-string contract for operator/punctuation-only input is preserved.
- `chunk_truth` no longer passes an oversized paragraph through whole; it hard-splits on sentence, then line, then fixed-width boundaries so no chunk exceeds `max_chars`. This reverses the prior "paragraphs are kept whole" note. Oversized chunks already in a live database are re-split on the next content change to their source file, or on a full rebuild.
- `get_db` issues `PRAGMA journal_mode=WAL`. Journal mode is a persistent file property, so this self-migrates a pre-existing rollback-journal database and lets the long-lived MCP reader and the ingest/refresh writer coexist without `database is locked`.

**Consequences:**

- New `db.delete_document` (full removal including the `documents` row) is separate from `_delete_document_artifacts` (which the upsert path still uses and which must not drop the row, because upsert reads `created_at` from it). Pruning leaves inbound edges dangling, consistent with the engine's resolve-or-dangle model.
- The ingest skip-path re-resolves edges every run but now writes only when the resolved `(target_id, relationship_type)` set differs from what is stored, so a no-op refresh is read-only instead of running a DELETE+INSERT per document.
- A `pruned` counter is added to ingest stats and the `Done.` log line.
- The MainFrame `.githooks/post-commit` hook refreshes only when the commit touched `10_knowledge/`, removing the unconditional per-commit refresh and the concurrent-refresh window it opened.

**Rejected alternatives:**

- Extend `_delete_document_artifacts` to also drop the `documents` row (the audit's literal suggestion). Rejected because that helper is shared with `upsert_document`, which reads `created_at` from the row after calling it; dropping the row there would silently reset `created_at` on every re-ingest. A dedicated `delete_document` keeps the upsert path untouched.
- Double-quote each surviving token instead of using an allowlist. Rejected as unnecessary: `\w+` tokens are already valid barewords, so quoting adds noise without changing safety.
- Cap `chunk_text` length only in MCP responses. Rejected as the primary fix because it leaves the recall loss unaddressed (the embedder still only sees the first ~256 tokens); splitting at the chunker fixes both recall and the context-dump risk at the source. An MCP-side cap remains available as defense-in-depth for pre-existing oversized chunks.
- Add a file lock around refresh to serialize concurrent ingests. Rejected for this pass in favor of the lighter WAL + hook-guard combination, which removes the lock contention WAL is designed for and cuts the trigger frequency that created the overlap window.

---

## 2026-06-14 — Phase 7 (part 1): expose semantic distance + a weak-fit signal

**Decision:** Add two fields to `QueryResult`: `semantic_distance` (the raw vec_chunks distance of the result's surfaced chunk, null for lexical-only and expanded results) and `weak_fit` (a boolean). `weak_fit` is true only when a result was nominated purely by semantics — no lexical/fused overlap to corroborate it — and its distance exceeds a calibrated threshold, `WEAK_FIT_DISTANCE_THRESHOLD = 1.0`. The distance is threaded from `fetch_semantic_ranking` through `rrf_fuse` to `run_query` without entering the RRF math. The MCP server instructions now state that a `weak_fit` result means the index has no strong answer. This is "part 1" of Phase 7; emitting frontmatter `type`/`domain`/`status` shipped alongside it, and the lifecycle multi-DB experiment remains future work.

**Rationale:** RRF scores are rank-based and scale-free (Phase 2 ADR), so by construction they cannot carry a fit threshold — a top-ranked result looks identical whether it is a strong hit or the least-bad of a bad set. The 2026-06-12 audit reproduced this false-confidence failure. Exposing the raw distance is the most honest fix and is faithful to ADR-1 ("nomination, not verification"): the engine surfaces the signal and lets the reader judge. `weak_fit` is a thin, transparent convenience over that exposed number — a labeled view, not a hidden judgment — so it does not cross into claim verification.

**Calibration:** The threshold was set against the live MainFrame index. Best-chunk distances measured: a strongly in-corpus query at 0.71 (top-5 ≤ 0.82), a real but out-of-scope query at 1.10, and a nonsense query at 1.22. 1.0 sits in the gap between confident hits and weak/no-answer cases. This is a first calibration over a small probe set, not a tuned value; because the raw distance is exposed, a consumer can apply its own cutoff, and the threshold can move without an API change.

**Heuristic scope (per-result, not response-level):** The audit sketched a response-level `weak_fit`. We attach it per result instead, because `run_query` returns a flat `list[QueryResult]` that the CLI and MCP serialize as a JSON array, and because per-result is strictly more informative: a consumer reads the top result (or all results) to answer "does the index have an answer," and also sees exactly which results are weak. Gating on "no lexical/fused overlap" at the result level reproduces the audit's intent — a lexically-corroborated hit is never flagged.

**Consequences:**

- `fetch_semantic_ranking` returns `(doc_id, chunk_index, rank, distance)` and `rrf_fuse` returns a 6-tuple carrying `semantic_distance`. The RRF scoring, sort, and tiebreak are unchanged; the distance rides alongside.
- `QueryResult` gains `semantic_distance: float | None` and `weak_fit: bool` (defaults None/False), so existing consumers and the CLI/MCP `model_dump` shape extend additively. Expanded (graph-walk) results carry null distance and `weak_fit=False`.
- The CLI text block shows `dist=` and a `[weak-fit]` marker; the MCP instructions document the field.

**Rejected alternatives:**

- A response-level envelope (`{results, weak_fit}`). Rejected because it breaks the established list-of-QueryResult contract that the Phase 5 MCP wrap and every consumer depend on, for less information than the per-result flag.
- `weak_fit` boolean only, without the raw distance. Rejected because it hides the number the judgment is built from, which is exactly the opacity ADR-1 argues against.
- Fold the distance into the RRF score so weak hits rank lower. Rejected because it would break the locked, scale-free RRF math and conflate ranking with fit; the two are deliberately separate signals.
- Tune the threshold per domain or per query. Rejected as premature: one exposed, documented global cutoff is enough for the current vault, and the raw distance leaves the door open.
