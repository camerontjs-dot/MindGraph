# MainFrame search vs. graph ablation

Research-infrastructure companion to [MindGraph #54](https://github.com/camerontjs-dot/MindGraph/issues/54). The study asks whether one bounded graph-linked admission recovers a required source that ordinary lexical/hybrid search plus an equal extra-result allowance does not retrieve.

This directory contains the actual local experiment harness and a separately written read-only verifier. Neither modifies product source or the installed indexes. No private MainFrame corpus, query text, source label, raw ranking, local machine path, model cache, or SQLite snapshot is committed here.

## Experiment

The four frozen arms are:

- **A10:** lexical FTS5 top 10.
- **B10:** lexical + MiniLM semantic RRF top 10.
- **B11:** same hybrid top 11; controls for an additional result slot.
- **C10plus1:** exact B10 prefix, then at most one existing one-hop graph target from a top-3 B10 seed, chosen in deterministic seed/target/edge order without gold access. Only citable source-backed targets are eligible. No edge traversal or ranking modifications.

All arms use the same frozen corpus snapshots, source labels and product code. The graph comparison that matters is **C10plus1 versus B11**, not versus B10 alone. Per-case source recall, first-hit rank, MRR, row/citation counts, text exposure, and runtime characterization are recorded. An indexed source missing from the frozen DB is an explicit unreachable gold label, not a failed ranking algorithm.

The Knowledge query anchors predate this run (June 1, 2026); the Projects anchors also predate it (September 15, 2026). They are previous operator judgment anchors, **not independently adjudicated answer truth**. End-to-end agent task completion and progressive expansion are NOT_RUN; this harness measures retrieval only.

## Reproduction with authorized private inputs

Obtain exact committed MindGraph `8df9ae7fccdb742558950ef9bdedb96cc74df6d0`, a Python environment with MindGraph's semantic extra, the cached MiniLM model, source corpora already indexed in two independent MindGraph SQLite files, and pre-existing labelled query YAML files with `queries[].query` and `queries[].expected_paths`. Keep input source content private.

```sh
python research/mainframe-search-graph/harness.py selftest
python research/mainframe-search-graph/harness.py prepare \
  --out PRIVATE_EXPERIMENT_DIRECTORY \
  --repo CLEAN_OR_OBJECT_ACCESSIBLE_MINDGRAPH_REPO \
  --knowledge-db PRIVATE_KNOWLEDGE_INDEX.sqlite \
  --projects-db PRIVATE_PROJECTS_INDEX.sqlite \
  --knowledge-gold PRIVATE_KNOWLEDGE_CASES.yaml \
  --projects-gold PRIVATE_PROJECTS_CASES.yaml

# Inspect freeze.json before the decisive run.
python research/mainframe-search-graph/harness.py run \
  --out PRIVATE_EXPERIMENT_DIRECTORY
python research/mainframe-search-graph/verify_results.py \
  PRIVATE_EXPERIMENT_DIRECTORY
```

The preparation step materializes the exact git-archived product object, copies the pre-written cases, makes consistent read-only SQLite backups, records which labelled source paths are actually indexed, and freezes all identities. Scoring refuses to run if a frozen target has drifted and will not overwrite a decisive output. The verifier recomputes source recall from stored raw rankings and checks frozen file identities, prefix invariance, and the actual graph edge/source path for admitted graph targets.

Do not publish the private input/output directory. The public result is [RESULTS.md](RESULTS.md). The run can be reconstructed only by a reader with legitimate access to the same private source snapshots; this public code alone is **not** a self-contained public replication of the MainFrame-specific scores.

## Interpretation boundaries

- An edge is only retrieval provenance, not evidence that its target is true or current.
- The one-target policy is deliberately simple; a negative result does not falsify all graph traversal or ranking strategies.
- The two existing label sets are known task-finding workloads, not graph-only stress sets. Scores include old or missing gold and must not be represented as a representative production evaluation.
- Semantic-query model/cache/runtime identity should be checked when reproducing, especially as cache revision was not directly attested by this run.
- A release or product migration is a separate decision. This Draft research apparatus does not authorize either.
