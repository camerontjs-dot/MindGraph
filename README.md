# MindGraph

MindGraph is a local, graph-augmented retrieval engine for personal Markdown knowledge bases. It ingests a directory of notes, extracts a typed `[[link]]` document graph, chunks the body text, and stores everything in one SQLite file. The core profile provides lexical search and graph traversal; an optional semantic profile adds vector similarity over the same store.

This is the engine I run against Mainframe, my own Markdown knowledge base. Any vault of Markdown notes with `[[wikilink]]` syntax (Obsidian, Foam, Logseq with the right setting) works the same way.

## Installation profiles

The base package is deliberately local and dependency-light. It does not
install Torch, CUDA, or an MCP SDK.

```bash
pip install .
pip install '.[semantic]'  # add sentence-transformers for vector retrieval
pip install '.[mcp]'       # add the supported MCP v1 SDK
pip install '.[full]'      # semantic + MCP
```

The `dev` extra adds test dependencies without pulling either runtime profile.
Install the extra required by the operation you intend to run.

## What this does

- Parses Markdown files. Reads optional YAML frontmatter for a `title` and a `domain`.
- Splits each note into a Truth body and an optional Timeline section on a `---` rule followed by a `## Timeline` heading.
- Extracts `[[target]]` and `[[target]] (relationship)` links as typed graph edges, with link targets normalized to add `.md` when missing.
- Computes a stable document ID from `sha256(relative_path)` for ordinary single-root ingest, or from `index_id + namespace + source_path` for scoped multi-root ingest.
- Skips re-embedding when the content hash matches an existing row.
- Chunks the Truth body into paragraphs packed up to `max_chars`, keeping paragraphs whole.
- Removes fenced code, table structure, reference-only lists, metadata rows, and explicit Markdown alert/callout blocks from semantic chunks. Ordinary blockquotes remain because they may contain substantive evidence; the full Truth body remains in the lexical lane.
- Optionally embeds chunks with a selectable model (`--embedder`: `minilm`, `bge-small`, `e5-small`; default MiniLM) at 384 dimensions per DB.
- Optional `--embed-template mainframe` prefixes domain/type/title at ingest and `[intent=query]` at query time.
- Writes documents, chunks, optional embeddings, FTS5 rows, and edges to one SQLite file with `sqlite-vec` and FTS5 attached.

## What it ranks

`mindgraph query <text>` returns ranked chunks. Two signals run over the same SQLite store and fuse with Reciprocal Rank Fusion at the canonical `k = 60`:

- Lexical: FTS5 BM25 over the chunked Truth text
- Semantic: `sqlite-vec` cosine over `vec_chunks`

Each result carries a `signal` label (`lexical`, `semantic`, `fused`, `expanded`, or `associated`), a `rrf_score`, and the per-signal `lexical_rank` and `semantic_rank` integers, so the attribution is mechanically verifiable. Ties break by `(doc_id, chunk_index)` lexicographic for deterministic output. Free-text queries pass through an FTS5 sanitizer that strips operator characters and the uppercase keywords `AND`, `OR`, `NOT`, `NEAR`, then OR-joins the surviving tokens.

Result rows also carry trust and provenance metadata for consumers that need to decide what to inspect next: `doc_type`, `domain`, `status`, `index_id`, `trust_profile`, `namespace`, `source_path`, `display_path`, `semantic_distance`, `weak_fit`, and `query_scope_warning`. The local `source_root` is retained for internal provenance but is omitted from serialized CLI/MCP results so host-specific absolute paths do not cross the boundary. `weak_fit` marks semantic-only rows beyond the current distance threshold. `query_scope_warning` appears when the query itself seems to ask for inbox, live/current, or project-status state that may belong in a different lifecycle database.

### Tuning the scope warnings

`query_scope_warning` fires on a keyword heuristic, and the shipped defaults
describe one vault's vocabulary. They are a starting point, not a claim about
how anyone else labels current state. If your notes say "unfiled" rather than
"inbox", or "standup" rather than "project status", retune it with a JSON file:

```json
{
  "inbox_terms": ["unfiled", "to sort"],
  "project_terms": ["sprint status", "standup"],
  "live_state_terms": ["oncall", "deploy", "incident"]
}
```

```bash
export MINDGRAPH_SCOPE_VOCABULARY=~/.mindgraph/scope-vocabulary.json
```

Four keys are recognized: `inbox_terms`, `project_terms`, `freshness_terms`, and
`live_state_terms`. Any key you omit keeps its default, so you can retune one
branch without restating the rest. An empty list disables that warning entirely.

Entries are regular-expression *fragments*, not literals, so `captures?` and
`state\.md` behave as written. They are joined with `|`, wrapped in `\b(...)\b`,
and matched case-insensitively.

The `live_state` warning needs a hit in **both** `freshness_terms` and
`live_state_terms`, so "latest incident" warns and a bare "incident" does not.
That keeps ordinary durable-knowledge queries quiet.

In Python, pass a vocabulary directly instead:

```python
from mindgraph.query import ScopeVocabulary, classify_query_scope

vocab = ScopeVocabulary(inbox_terms=("unfiled", "to sort"))
classify_query_scope("some unfiled notes", vocab)
```

`mindgraph query --expand` appends graph-walk results. After the fused list returns, the query walks outbound `[[link]]` edges from each fused result to a bounded depth (`--depth N`, default 1, cap 3) and appends walked documents with `signal = "expanded"` and an `expansion_depth` integer. The walk is outbound only, deduplicates against the fused set, terminates at dangling edges, and does not interact with the RRF math. `--expand-top-k N` (default 20) caps appended expanded rows.

`mindgraph query --associate` appends semantic doc-neighbor results (ADR-034). From fused seeds (not expand results), embeds title + chunk excerpt per seed, runs vec kNN, and appends rows with `signal = "associated"`, `association_depth = 1`, and `semantic_distance`. `--associate-top-k` and `--associate-seed-k` cap output and seed count.

`mindgraph neighbors <doc_id>` lists outbound edges for a document, including dangling edges (links to files that do not exist as documents).

## What this does not do yet

- There is no LLM generation step. MindGraph retrieves and ranks. It does not write summaries, answers, or explanations.
- Renaming a file produces a new document ID. The old document remains in the database until a future cleanup pass prunes orphans.
- Only Markdown is a first-class input. PDFs and other formats are out of scope for this asset.
- Use a **separate SQLite file per embedder** (`--embedder` sets `vec_chunks` dimensions at init). Re-ingest the full scope into each eval DB; do not swap models in-place on one DB.
- Lexical-only results surface chunk index 0 because there is no semantic ranking to pick a better chunk from. Fused and semantic results surface the best-ranked chunk per document.
- A lexical-only index has no semantic vectors; use `--lexical-only` for both ingest and query, and create a fresh index when switching to semantic retrieval.
- Retrieval is nomination, not verification. A retrieved chunk is a candidate for a reader to read, not a verified source for any claim.
- Normal ingest and query never acquire models from the network. Run the explicit `mindgraph bootstrap-model` setup command once before semantic ingest/query.

## Useful commands

```bash
mindgraph init --db mindgraph.sqlite
mindgraph bootstrap-model --embedder minilm
mindgraph ingest path/to/your/vault --db mindgraph.sqlite
mindgraph ingest path/to/your/vault --db lexical.sqlite --lexical-only
mindgraph ingest path/to/your/vault --db mindgraph.sqlite --embedder bge-small --embed-template mainframe
mindgraph ingest path/to/your/vault --db mindgraph.sqlite --verbose
mindgraph ingest path/to/your/vault --db mindgraph.sqlite --index-id knowledge --trust-profile durable_knowledge --namespace knowledge --display-prefix knowledge
mindgraph ingest-many path/to/manifest.json --db mindgraph.sqlite
mindgraph query "what does this vault say about X" --db mindgraph.sqlite
mindgraph query "..." --db mindgraph.sqlite --top-k 5 --json
mindgraph query "..." --db mindgraph.sqlite --top-k 5 --json --envelope
mindgraph query "..." --db mindgraph.sqlite --expand
mindgraph query "..." --db mindgraph.sqlite --expand --depth 2 --expand-top-k 10
mindgraph query "..." --db mindgraph.sqlite --associate --associate-top-k 10
mindgraph query "..." --db mindgraph.sqlite --embedder e5-small --embed-template mainframe
mindgraph query "..." --db lexical.sqlite --lexical-only
mindgraph neighbors <doc_id> --db mindgraph.sqlite
mindgraph neighbors <doc_id> --db mindgraph.sqlite --json
mindgraph serve-mcp --db mindgraph.sqlite
mindgraph serve-mcp --db mindgraph.sqlite --verbose
```

The commands above are the compatibility path: one database, stdio transport,
and legacy list-shaped query/neighbor JSON by default.

### Optional shared daemon

The opt-in shared server loads one embedder and opens each declared index
read-only. You choose the scope names and their trust labels. It binds only to
loopback and requires every tool call to select exactly one of your scopes. Its
responses expose the selected scope and trust profile and never blend stores.

```bash
mindgraph daemon-start --scope notes=~/.mindgraph/notes.sqlite \
                       --scope archive=~/.mindgraph/archive.sqlite
mindgraph daemon-status
mindgraph daemon-health
mindgraph mcp-proxy --url http://127.0.0.1:8000/mcp
mindgraph daemon-stop
```

The proxy does not start the daemon for you. See the [MCP](#mcp) section for
declaring scopes, connecting clients, and what is not established.

Semantic model acquisition is an explicit setup step. `bootstrap-model` may
use the configured model hub and caches the selected model; normal ingest and
query then load it cache-only. If the cache is missing, the command fails with
an instruction to install the semantic extra and run `bootstrap-model`.

## Try it

A small seven-file Markdown vault under `examples/example-vault/` exercises every retrieval path the engine exposes: lexical-only matches, semantic-only matches, fused matches, dangling graph edges, and graph expansion. Run the sequence below from the asset root after `pip install -e '.[full]'` and `mindgraph bootstrap-model` to reproduce the semantic captures. The captures regenerate from `scripts/run_example_smoke.py` against a fresh temporary database.

### Bootstrap the semantic model

```
$ mindgraph bootstrap-model --embedder minilm
INFO    mindgraph | Acquiring embedding model (all-MiniLM-L6-v2)...
Embedding model ready: all-MiniLM-L6-v2 (384 dimensions)
```

### Initialize the database

```
$ mindgraph init --db /tmp/mindgraph-example/db.sqlite
INFO    mindgraph | Initialized database at /tmp/mindgraph-example/db.sqlite
```

### Ingest the example vault

The model was acquired by the explicit bootstrap step before ingest. Ingest
only loads the local cache.

```
$ mindgraph ingest examples/example-vault --db /tmp/mindgraph-example/db.sqlite
INFO    mindgraph | ingested: balancing-loops.md (1 chunks, 1 edges)
INFO    mindgraph | ingested: bounded-rationality.md (1 chunks, 1 edges)
INFO    mindgraph | ingested: feedback-loops.md (1 chunks, 2 edges)
INFO    mindgraph | ingested: mental-models-overview.md (1 chunks, 1 edges)
INFO    mindgraph | ingested: reinforcing-loops.md (1 chunks, 1 edges)
INFO    mindgraph | ingested: systems-archetypes.md (1 chunks, 1 edges)
INFO    mindgraph | ingested: unrelated-noise.md (1 chunks, 0 edges)
INFO    mindgraph | Done. total=7 ingested=7 skipped=0 failed=0
```

### Query a unique keyword

`antinet` appears in only one note (`mental-models-overview.md`). It lands at rank 1 as a fused hit because the keyword also pulls the doc on the semantic side. The second and third results carry `signal=semantic` because they have no lexical match.

```
$ mindgraph query antinet --db /tmp/mindgraph-example/db.sqlite --top-k 3
#1  signal=fused  rrf_score=0.032787  lex_rank=1  sem_rank=1
    path: mental-models-overview.md
    title: Mental models overview
    chunk_index: 0
    excerpt: I keep a running file of mental models I have found useful, separate from the structural systems-thinking notes...
#2  signal=semantic  rrf_score=0.016129  lex_rank=-  sem_rank=2
    path: feedback-loops.md
    title: Feedback loops
    chunk_index: 0
    excerpt: A feedback loop is a circular causal structure where the output of a process becomes part of its own input on a later pass...
#3  signal=semantic  rrf_score=0.015873  lex_rank=-  sem_rank=3
    path: bounded-rationality.md
    title: Bounded rationality
    chunk_index: 0
    excerpt: Herbert Simon coined this term to describe how people decide when full information and unlimited compute are not available...
```

### Query a concept term

`satisficing` is unique to `bounded-rationality.md`. The signal attribution shows the same pattern: a fused top hit plus two semantic-only neighbors that the embedder pulled in by topical similarity.

```
$ mindgraph query satisficing --db /tmp/mindgraph-example/db.sqlite --top-k 3
#1  signal=fused  rrf_score=0.032787  lex_rank=1  sem_rank=1
    path: bounded-rationality.md
    title: Bounded rationality
    chunk_index: 0
    excerpt: Herbert Simon coined this term to describe how people decide when full information and unlimited compute are not available...
#2  signal=semantic  rrf_score=0.016129  lex_rank=-  sem_rank=2
    path: mental-models-overview.md
    title: Mental models overview
    chunk_index: 0
    excerpt: I keep a running file of mental models I have found useful, separate from the structural systems-thinking notes...
#3  signal=semantic  rrf_score=0.015873  lex_rank=-  sem_rank=3
    path: balancing-loops.md
    title: Balancing loops
    chunk_index: 0
    excerpt: A balancing loop is a feedback structure that pushes a system back toward a setpoint...
```

### Query that fuses lexical and semantic signals

`balancing feedback` matches multiple notes both ways. The top three all carry `signal=fused` with different `lex_rank` and `sem_rank` values, so the attribution is mechanically checkable.

```
$ mindgraph query "balancing feedback" --db /tmp/mindgraph-example/db.sqlite --top-k 3
#1  signal=fused  rrf_score=0.032522  lex_rank=1  sem_rank=2
    path: feedback-loops.md
    title: Feedback loops
    chunk_index: 0
    excerpt: A feedback loop is a circular causal structure where the output of a process becomes part of its own input on a later pass...
#2  signal=fused  rrf_score=0.032522  lex_rank=2  sem_rank=1
    path: balancing-loops.md
    title: Balancing loops
    chunk_index: 0
    excerpt: A balancing loop is a feedback structure that pushes a system back toward a setpoint...
#3  signal=fused  rrf_score=0.031258  lex_rank=5  sem_rank=3
    path: systems-archetypes.md
    title: Systems archetypes
    chunk_index: 0
    excerpt: A systems archetype is a recurring pattern of feedback structure that shows up across very different domains...
```

### Walk the graph from a seed

With `--top-k 1` scoping the fused step to just the seed, `--expand --depth 2` walks the outbound `[[link]]` edges from `feedback-loops.md`. Depth-1 hits are the two `(illustrates)` targets; the depth-2 hit is `systems-archetypes.md`, reached through both one-hop docs and deduplicated. Walked rows carry `signal=expanded` and a `depth=N` suffix, with `lex_rank` and `sem_rank` blank because expansion is a labeled append, not a rerank.

```
$ mindgraph query "feedback loops" --db /tmp/mindgraph-example/db.sqlite --top-k 1 --expand --depth 2
#1  signal=fused  rrf_score=0.032787  lex_rank=1  sem_rank=1
    path: feedback-loops.md
    title: Feedback loops
    chunk_index: 0
    excerpt: A feedback loop is a circular causal structure where the output of a process becomes part of its own input on a later pass...
#2  signal=expanded  rrf_score=0.000000  lex_rank=-  sem_rank=-  depth=1
    path: reinforcing-loops.md
    title: Reinforcing loops
    chunk_index: 0
    excerpt: A reinforcing loop is a feedback structure where a change in one direction produces more change in the same direction on the next cycle...
#3  signal=expanded  rrf_score=0.000000  lex_rank=-  sem_rank=-  depth=1
    path: balancing-loops.md
    title: Balancing loops
    chunk_index: 0
    excerpt: A balancing loop is a feedback structure that pushes a system back toward a setpoint...
#4  signal=expanded  rrf_score=0.000000  lex_rank=-  sem_rank=-  depth=2
    path: systems-archetypes.md
    title: Systems archetypes
    chunk_index: 0
    excerpt: A systems archetype is a recurring pattern of feedback structure that shows up across very different domains...
```

### Inspect outbound edges and surface a dangling target

`systems-archetypes.md` links to `[[unicycle-mental-model]] (cites)`, but the target file does not exist in the vault. `mindgraph neighbors` returns the row with `target_path: (dangling)` so broken links surface during traversal rather than getting silently dropped. The `doc_id` argument is the hex ID from any earlier `--json` query output for the source document.

```
$ mindgraph neighbors c8a1be119b7ad0c3 --db /tmp/mindgraph-example/db.sqlite
#1  -> 885be168decf4005  rel=cites
    target_path: (dangling)
```

## MCP

MindGraph ships MCP transports around the same retrieval code used by the CLI.
It does not add ranking behavior, change the database schema, or turn retrieved
chunks into verified claims.

Install the `mcp` extra for the transport surface. The MCP server's default
query path also needs the `semantic` extra and a bootstrapped model; the
dependency-light core remains usable independently through lexical-only CLI
ingest/query.

Two transports ship. Pick by how many databases you need open at once.

| Transport | Command | Use it when |
|---|---|---|
| Streamable HTTP daemon | `serve-daemon` / `daemon-start` | You want several named indexes, and several MCP clients sharing one loaded embedder |
| stdio server | `serve-mcp` | You want one database bound to one client, or you are debugging in isolation |

### Recommended: shared daemon over Streamable HTTP

Each stdio server process loads its own copy of the embedding model. If you run
several MCP clients at once, that is several copies of MiniLM resident at the
same time. The daemon exists to make that cost once instead of once per client.

One loopback process opens each index read-only, holds a single embedder, and
serves them over the MCP Streamable HTTP transport.

**You choose the scopes.** A scope is a name, a database, and a trust profile
label that rides along on every result from that store. Name them after whatever
distinction actually matters in your vault. Build one database per scope:

```bash
mindgraph init   --db ~/.mindgraph/recipes.sqlite
mindgraph ingest ~/vault/recipes --db ~/.mindgraph/recipes.sqlite \
  --index-id recipes --namespace recipes --display-prefix recipes

mindgraph init   --db ~/.mindgraph/journal.sqlite
mindgraph ingest ~/vault/journal --db ~/.mindgraph/journal.sqlite \
  --index-id journal --namespace journal --display-prefix journal
```

Then declare them with a repeatable `--scope`:

```bash
mindgraph daemon-start \
  --scope recipes=~/.mindgraph/recipes.sqlite \
  --scope journal:personal_log=~/.mindgraph/journal.sqlite
mindgraph daemon-status
mindgraph daemon-health
mindgraph daemon-stop
```

The spec is `NAME=PATH`, or `NAME:TRUST_PROFILE=PATH` when you want the trust
label to differ from the scope name. Only the first `=` is a separator, so
database paths may contain `=` and `:`. Trust profile defaults to the scope
name. The health endpoint reports back exactly what you declared:

```json
{"status":"ok","scopes":[{"scope":"journal","trust_profile":"personal_log"},
                         {"scope":"recipes","trust_profile":"recipes"}]}
```

Every shared tool call must name one `scope`. There is no blended query: a call
with no scope fails validation, and an unrecognized scope is rejected with the
list of names you declared. Responses report the scope and trust profile they
were served from, so a caller can always tell which store an answer came from.

Pass `--index-id`, `--namespace`, and `--display-prefix` at ingest if you want
provenance fields populated on result rows; without them those fields are null.

`serve-daemon` runs the same server in the foreground if you would rather
supervise it yourself. Both accept `--host` (default `127.0.0.1`), `--port`
(default `8000`), `--path` (default `/mcp`), and `--embedder`.

<details>
<summary>Two-index shorthand</summary>

`--knowledge-db` and `--projects-db` are a fixed shorthand for a durable-notes
plus active-work split, kept for existing deployments:

```bash
mindgraph daemon-start \
  --knowledge-db ~/.mindgraph/knowledge.sqlite \
  --projects-db  ~/.mindgraph/projects.sqlite
```

That is equivalent to `--scope knowledge:durable_knowledge=...` plus
`--scope projects:project_status=...`. Any `--scope` you pass replaces this pair
entirely. Prefer `--scope` for new setups.

</details>

### Connecting clients

Clients that speak Streamable HTTP connect to the daemon directly:

```
http://127.0.0.1:8000/mcp
```

Clients that speak stdio connect through the bundled proxy, which is a thin
Streamable HTTP client that re-exposes the daemon's tools over stdio:

```json
{
  "mcpServers": {
    "mindgraph": {
      "command": "mindgraph",
      "args": ["mcp-proxy", "--url", "http://127.0.0.1:8000/mcp"]
    }
  }
}
```

Use the absolute path to the `mindgraph` entry point if it is not on your
client's `PATH` (for example `/path/to/.venv/bin/mindgraph`). The proxy does not
start the daemon for you.

### Single-database stdio server

For one index bound to one client, or for isolated debugging:

```bash
mindgraph init   --db /tmp/mindgraph-mcp/db.sqlite
mindgraph ingest examples/example-vault --db /tmp/mindgraph-mcp/db.sqlite
mindgraph serve-mcp --db /tmp/mindgraph-mcp/db.sqlite --verbose
```

Stdout is reserved for MCP protocol frames; logs go to stderr.

### What is not established

Re-ingesting does not hot-reload a running daemon; restart it to pick up new
content. The daemon binds to loopback and ships no authentication, so it is not
safe to expose beyond the local machine. Process supervision, concurrency
behavior under load, and RAM and latency figures are unmeasured.

### Tools

`query` runs the same retrieval path as `mindgraph query --json`. Parameters: `question`, `lexical_top_k`, `semantic_top_k`, `final_top_k`, `expand`, `expand_depth`, `expand_top_k`, `associate`, `associate_top_k`, `associate_seed_k`, and `envelope`. By default, the MCP response content is a JSON array of `QueryResult` records. With `envelope=true` (or CLI `--json --envelope`), it returns an object containing `schema_version`, `intent_resolution`, `routing`, and `results`; legacy list output remains unchanged when the flag is omitted. `routing` is single-database metadata for the bound index (not multi-index federation). In a smoke run against the example vault, the default list path matched the CLI JSON output exactly:

```json
{
  "question": "feedback loops",
  "final_top_k": 1,
  "expand": true,
  "expand_depth": 2
}
```

Observed result paths from that smoke were `feedback-loops.md`, `reinforcing-loops.md`, `balancing-loops.md`, and `systems-archetypes.md`, with `expansion_depth` values `[0, 1, 1, 2]`.

`graph_neighbors` runs the same lookup as `mindgraph neighbors --json`. Parameter: `doc_id`. The MCP response content is a JSON array of `NeighborResult` records, including dangling edges with `target_path = null`. In the same smoke, calling `graph_neighbors` with `doc_id = "c8a1be119b7ad0c3"` returned the single dangling edge the CLI lookup also returns.

### claude.ai web

The stdio transport does not connect directly to claude.ai web. The web product requires a remote transport such as Streamable HTTP or SSE over HTTPS. Exposing a local SQLite-backed knowledge base over the public internet would contradict MindGraph's local-first framing, so a remote transport is out of scope for now.

## Architecture

MindGraph runs entirely locally. There is no service to start and no remote dependency at retrieval time.

1. **Ingestion.** `parser.parse_document` reads YAML frontmatter, splits Truth from Timeline, and returns a `ParsedDocument`. `parser.extract_document_graph_edges` collects `GraphEdge` records from both frontmatter `links:` and body `[[wikilinks]]` (ADR-033). `LinkResolver` resolves unique canonical trailing slugs (`…__slug`) as well as full stems and titles. `parser.chunk_truth` packs paragraphs into bounded chunks.
2. **Storage.** `db.init_db` creates the schema: `documents`, `documents_fts` (FTS5 over title and Truth content), `chunks`, `vec_chunks` (sqlite-vec, 384-dim), and `edges`. Foreign keys are on.
3. **Re-ingest.** `db.get_document_hash` compares the stored hash to the freshly computed one. Unchanged files exit before parsing or embedding.
4. **Retrieval.** `query.fetch_lexical_ranking` runs FTS5 BM25 over `documents_fts`. `query.fetch_semantic_ranking` runs a `sqlite-vec` KNN over `vec_chunks` and promotes to document granularity by keeping the best chunk per document. `query.rrf_fuse` combines the two ranked lists at the canonical `k = 60` and returns deterministic, signal-attributed results.
5. **Graph lookup.** `query.list_neighbors` resolves edges from a source document against the `documents` table, preserving dangling edges with `null` resolved paths.
6. **Graph expansion.** `query.expand_results` walks outbound edges from the fused result set in a deterministic BFS, deduplicating against the seed doc_ids and skipping dangling targets. Walked documents are appended with `signal = "expanded"`, `expansion_depth` set to the walk distance, and sorted by `(expansion_depth, doc_id, chunk_index)` before the `--expand-top-k` cut.

## Test discipline

The parser test suite (`tests/test_parser.py`) covers frontmatter parsing, the page-model split rule, internal `---` rules that must not split, the link extraction regex including nested-bracket rejection, chunk packing, and end-to-end `parse_document`. The ingest test suite (`tests/test_ingest.py`) covers the ingest happy path against a fixture vault.

The query test suite (`tests/test_query.py`) covers FTS5 input sanitization, RRF fusion math, lexical and semantic ranking against a small ingested vault, end-to-end signal attribution, and the CLI surface. It uses a deterministic `KeywordEmbedder` stub so semantic similarity is reproducible without depending on the real MiniLM model.

The neighbors test suite (`tests/test_neighbors.py`) covers outbound edge resolution, dangling edges, multiple relationship types per edge, sources with no edges, and the CLI surface.

The expand test suite (`tests/test_expand.py`) covers one-hop walk, two-hop walk, no-expand and depth-0 equivalence to the un-expanded path, dedup of walked targets already in the fused set, the `--expand-top-k` cap, dangling-edge termination, and unreachable-doc absence. The CLI tests also assert the `expansion_depth` field in JSON output and the hard `--depth` cap of 3.

The examples test suite (`tests/test_examples.py`) ingests the committed `examples/example-vault/` into a temp database with the same deterministic `KeywordEmbedder` and asserts each retrieval path: the unique lexical keyword lands on the expected doc, the semantic-only synonym path lands on `bounded-rationality.md` with `signal=semantic`, the fused query lands on `balancing-loops.md`, the dangling edge from `systems-archetypes.md` is preserved by `list_neighbors`, and the depth-2 expansion topology reaches the expected docs and skips the dangling target.

The MCP test suite (`tests/test_mcp.py`) uses the official Python MCP SDK's in-memory client plus the deterministic `KeywordEmbedder` stub. It asserts server startup, missing-DB startup failure, `query` and `graph_neighbors` output shape parity with CLI JSON, opt-in envelope metadata, expansion parameter routing, dangling-edge preservation, and a clean tool error for unknown `doc_id` values.

Run the full suite from the asset root:

```bash
.venv/bin/python -m pytest
```

## Design notes

`DECISIONS.md` is the architectural decision log. Each entry records what was decided, what was rejected, and why, so the reasoning is recoverable without reading the code or chasing planning files.
