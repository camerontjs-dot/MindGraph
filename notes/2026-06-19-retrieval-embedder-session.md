# Session notes — 2026-06-19 retrieval & embedder research

Operator session spanning MindGraph graph fixes, retrieval architecture ADRs, embedder literature, and vault ingest.

## Shipped (tracked in git)

### ADR-033 — Dual-channel graph + slug resolution

- `extract_document_graph_edges()` merges frontmatter `links:` and body wikilinks; dedupes on `target_id`.
- `LinkResolver` resolves unique trailing slugs from canonical filename stems.
- Tests in `mindgraph/tests/test_parser.py`; ingest wired in `cli.py`.
- Authoring contract in `.context/primitives.md`; ingest-source skill updated (`.agents/` + `.claude/` mirror).

**Post-ship:** `bin/mindgraph-refresh` run — 594 docs, edge count increased (903 edges vs 854 pre-ADR-033 on prior refresh).

### ADR-034 / ADR-035 — Retrieval model direction

- **ADR-034:** Query-time semantic association (`--associate`) — fourth signal, append-only.
- **ADR-035:** Keep hybrid FTS + chunk semantic + explicit graph; embedder changes gated on eval.

Planning lives in `30_projects/mindgraph/plans/` (local, gitignored).

## Vault work (local, gitignored)

### Embedder source literature

Eight peer-reviewed stubs ingested to `10_knowledge/graph-memory/`:

| Slug | arXiv |
|------|-------|
| mteb-massive-text-embedding-benchmark | 2210.07316 |
| sentence-bert-sentence-embeddings | 1908.10084 |
| beir-zero-shot-retrieval-benchmark | 2104.08663 |
| e5-text-embeddings | 2212.03533 |
| bge-c-pack-embeddings | 2309.07597 |
| refine-scarce-data-embedding-finetune | 2410.12890 |
| clp-finetuning-embeddings | 2412.17364 |
| sparse-meets-dense-hybrid-retrieval | 2401.04055 |

### Synthesis

`10_knowledge/graph-memory/2026-06-19__graph-memory__note__text-embedding-models-for-mindgraph.md`

**Working conclusions:**

1. Hybrid FTS + dense + explicit graph is literature-supported — tune the dense channel, don't swap architecture.
2. MTEB/BEIR inform model choice; MainFrame probes (q01–q18) are the promotion gate.
3. MainFrame-native embedder = fine-tune/template-tune (REFINE/CLP), not train-from-scratch (~584 docs).
4. Recommended path: template prefixes → Phase A bake-off → ensemble or swap → Phase C fine-tune.

Run note: `embedder-retrieval-literature-run`. Eval runbooks updated in `30_projects/mindgraph-eval/plans/`.

## Shipped (2026-06-19 continuation)

### Track 1 — `--embedder` + template prefixes
- `mindgraph/embedders.py` registry (minilm, bge-small, e5-small).
- `--embedder` on init/ingest/query/serve-mcp; `--embed-template mainframe`.
- Per-DB `embedding_dims` in `index_meta`; parallel DBs for bake-off.

### Track 2 — Phase 9 `--associate` (ADR-034)
- `associate_results()` in `query.py`; CLI/MCP flags shipped.
- `tests/test_associate.py`; 154 tests pass.

### Track 3 — Embedder bake-off automation
- `30_projects/mindgraph-eval/scripts/embedder_bakeoff.py`.

### Track 4 — Research resolutions
- `plans/research-tracks/2026-06-19-vocabulary-vs-structure.md`
- `plans/research-tracks/2026-06-19-finetune-timing.md`

### Track 5 — Eval probes + graph baseline
- `association-probes.yaml` for Phase 9 eval.
- Graph baseline `2026-06-19-post-adr033-graph-baseline` (952 edges, 594 docs).
- Link cultivation on federation + embedder synthesis notes.

## Open / next

| Item | Blocker |
|------|---------|
| Parallel BGE/E5 DB ingest | Operator GPU/time for model download |
| Phase A bake-off run | Parallel DBs |
| Phase B ensemble | Multi-vec storage ADR |
| Association eval pass | Run `association-probes.yaml` matrix |

## Discussion threads offered

1. Unblock bake-off (`--embedder` flag)
2. Template prefix experiment (no weight change)
3. Dimension/schema for BGE non-384d
4. Fine-tune timing vs Phase 9 association
5. Vocabulary vs structure bottleneck