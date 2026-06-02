# MindGraph decisions

Architectural decision records for MindGraph. Each entry records what was decided, what was rejected, and why. A future reader should be able to recover the full reasoning from the entry alone, without access to planning files or conversation history.

---

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
