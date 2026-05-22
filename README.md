# MindGraph

MindGraph is a local, graph-augmented retrieval engine for personal Markdown knowledge bases. It ingests a directory of notes, extracts a typed `[[link]]` document graph, chunks the body text, embeds the chunks with a small CPU model, and stores everything in one SQLite file. The retrieval surface combines vector similarity, lexical search, and graph traversal over the same store.

This is the engine I run against Mainframe, my own Markdown knowledge base. Any vault of Markdown notes with `[[wikilink]]` syntax (Obsidian, Foam, Logseq with the right setting) works the same way.

## What this does

- Parses Markdown files. Reads optional YAML frontmatter for a `title` and a `domain`.
- Splits each note into a Truth body and an optional Timeline section on a `---` rule followed by a `## Timeline` heading.
- Extracts `[[target]]` and `[[target]] (relationship)` links as typed graph edges, with link targets normalized to add `.md` when missing.
- Computes a stable document ID from `sha256(relative_path)` and a content hash from the file bytes.
- Skips re-embedding when the content hash matches an existing row.
- Chunks the Truth body into paragraphs packed up to `max_chars`, keeping paragraphs whole.
- Embeds chunks with `sentence-transformers/all-MiniLM-L6-v2` at 384 dimensions.
- Writes documents, chunks, embeddings, FTS5 rows, and edges to one SQLite file with `sqlite-vec` and FTS5 attached.

## What it ranks

`mindgraph query <text>` returns ranked chunks. Two signals run over the same SQLite store and fuse with Reciprocal Rank Fusion at the canonical `k = 60`:

- Lexical: FTS5 BM25 over the chunked Truth text
- Semantic: `sqlite-vec` cosine over `vec_chunks`

Each result carries a `signal` label (`lexical`, `semantic`, `fused`, or `expanded`), a `rrf_score`, and the per-signal `lexical_rank` and `semantic_rank` integers, so the attribution is mechanically verifiable. Ties break by `(doc_id, chunk_index)` lexicographic for deterministic output. Free-text queries pass through an FTS5 sanitizer that strips operator characters and the uppercase keywords `AND`, `OR`, `NOT`, `NEAR`, then OR-joins the surviving tokens.

`mindgraph query --expand` adds a third signal as a labeled append. After the fused list returns, the query walks outbound `[[link]]` edges from each fused result to a bounded depth (`--depth N`, default 1, cap 3) and appends walked documents to the result list with `signal = "expanded"` and an `expansion_depth` integer. The walk is outbound only, deduplicates against the fused set, terminates at dangling edges, and does not interact with the RRF math. `--expand-top-k N` (default 20) caps the number of appended expanded rows.

`mindgraph neighbors <doc_id>` lists outbound edges for a document, including dangling edges (links to files that do not exist as documents).

## What this does not do yet

- There is no LLM generation step. MindGraph retrieves and ranks. It does not write summaries, answers, or explanations.
- Renaming a file produces a new document ID. The old document remains in the database until a future cleanup pass prunes orphans.
- Only Markdown is a first-class input. PDFs and other formats are out of scope for this asset.
- Switching the embedding model requires a schema migration on `vec_chunks` and a re-embed of every chunk. There is no graceful in-place upgrade.
- Lexical-only results surface chunk index 0 because there is no semantic ranking to pick a better chunk from. Fused and semantic results surface the best-ranked chunk per document.
- Retrieval is nomination, not verification. A retrieved chunk is a candidate for a reader to read, not a verified source for any claim.

## Useful commands

```bash
mindgraph init --db mindgraph.sqlite
mindgraph ingest path/to/your/vault --db mindgraph.sqlite
mindgraph ingest path/to/your/vault --db mindgraph.sqlite --verbose
mindgraph query "what does this vault say about X" --db mindgraph.sqlite
mindgraph query "..." --db mindgraph.sqlite --top-k 5 --json
mindgraph query "..." --db mindgraph.sqlite --expand
mindgraph query "..." --db mindgraph.sqlite --expand --depth 2 --expand-top-k 10
mindgraph neighbors <doc_id> --db mindgraph.sqlite
mindgraph neighbors <doc_id> --db mindgraph.sqlite --json
mindgraph serve-mcp --db mindgraph.sqlite
mindgraph serve-mcp --db mindgraph.sqlite --verbose
```

First ingest on a fresh machine downloads and caches the embedding model. First query also loads the model to embed the query text, and logs the same line. Subsequent runs reuse the cached model.

## Try it

A small seven-file Markdown vault under `examples/example-vault/` exercises every retrieval path the engine exposes: lexical-only matches, semantic-only matches, fused matches, dangling graph edges, and graph expansion. Run the sequence below from the asset root after `pip install -e .` to reproduce the captures. The captures regenerate from `scripts/run_example_smoke.py` against a fresh `/tmp/mindgraph-example/db.sqlite`.

### Initialize the database

```
$ mindgraph init --db /tmp/mindgraph-example/db.sqlite
INFO    mindgraph | Initialized database at /tmp/mindgraph-example/db.sqlite
```

### Ingest the example vault

The first ingest downloads the MiniLM model and logs one canonical line so the first-run latency is visible.

```
$ mindgraph ingest examples/example-vault --db /tmp/mindgraph-example/db.sqlite
INFO    mindgraph | Loading embedding model (all-MiniLM-L6-v2)...
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

MindGraph ships a stdio MCP server for local clients that already speak MCP. It is a transport wrap around the same retrieval code used by the CLI. It does not add ranking behavior, change the database schema, or turn retrieved chunks into verified claims.

### Start the server

Build or reuse a database first, then start the server from the asset root:

```bash
.venv/bin/mindgraph init --db /tmp/mindgraph-mcp/db.sqlite
.venv/bin/mindgraph ingest examples/example-vault --db /tmp/mindgraph-mcp/db.sqlite
.venv/bin/mindgraph serve-mcp --db /tmp/mindgraph-mcp/db.sqlite --verbose
```

The server runs in the foreground and writes logs to stderr only. Stdout is reserved for MCP protocol frames. A smoke run against `examples/example-vault/` showed the eager model load at startup:

```text
21:30:39 INFO    mindgraph | Loading embedding model (all-MiniLM-L6-v2)...
```

### Claude Code config

Add a `.mcp.json` at the repo root, adjusting the absolute paths for your checkout and database:

```json
{
  "mcpServers": {
    "mindgraph": {
      "command": "/absolute/path/to/mindgraph/.venv/bin/mindgraph",
      "args": [
        "serve-mcp",
        "--db",
        "/absolute/path/to/mindgraph.sqlite"
      ]
    }
  }
}
```

Claude Code, Claude Desktop, Cursor, Cline, and other stdio-MCP-aware local clients can use the same server shape.

### Tools

`query` runs the same retrieval path as `mindgraph query --json`. Parameters: `question`, `lexical_top_k`, `semantic_top_k`, `final_top_k`, `expand`, `expand_depth`, and `expand_top_k`. The MCP response content is a JSON array of `QueryResult` records. In a smoke run against the example vault, this call matched the CLI JSON output exactly:

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

1. **Ingestion.** `parser.parse_document` reads YAML frontmatter, splits Truth from Timeline, and returns a `ParsedDocument`. `parser.extract_graph_edges` walks the Truth text and returns `GraphEdge` records. `parser.chunk_truth` packs paragraphs into bounded chunks.
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

The MCP test suite (`tests/test_mcp.py`) uses the official Python MCP SDK's in-memory client plus the deterministic `KeywordEmbedder` stub. It asserts server startup, missing-DB startup failure, `query` and `graph_neighbors` output shape parity with CLI JSON, expansion parameter routing, dangling-edge preservation, and a clean tool error for unknown `doc_id` values.

Run the full suite from the asset root:

```bash
.venv/bin/python -m pytest
```

## Design notes

`DECISIONS.md` is the architectural decision log. Each entry records what was decided, what was rejected, and why, so the reasoning is recoverable without reading the code or chasing planning files.
