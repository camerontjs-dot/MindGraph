# MindGraph vNext RC0: provenance-aware retrieval baselines

Owned by [vNext programme #60](https://github.com/camerontjs-dot/MindGraph/issues/60). These are two distinct bounded research experiments with separate frozen input and code identities: [link target diagnosis #61](https://github.com/camerontjs-dot/MindGraph/issues/61) and [compact CLI payload study #62](https://github.com/camerontjs-dot/MindGraph/issues/62). See [RESULTS.md](RESULTS.md) for exact read-only observations, negative results, hashes and nonclaims.

## Scope and guardrails

The apparatus reads **only transaction-consistent frozen SQLite copies**, exact archived MindGraph Git source and the historical, *private* #54 query cohort. It makes no changes to production indexes, source files, local dirty worktrees, installed services, Conduit or GitHub release state. No model inference, network downloads or hidden selector is necessary.

The private indexes, real document text, raw link labels, per-case queries, case gold, graph topology and CLI output **must never be committed to this public repo**. The scripts are portable; they do not contain the corpus or query identities.

## Reproduction requirements

- Standard Python 3.10+; candidate MindGraph's declared Python packages (notably Typer, PyYAML, Pydantic, sqlite-vec) and a Git archive of the exact source SHA for the intended experiment.
- Freeze the three index snapshots using SQLite's **online backup API** from read-only installed connections. Record the exact SHA-256 of each copy. Do not copy a live SQLite file with uncheckpointed WAL by filesystem `cp` and claim a coherent snapshot.
- For #61, use the maintained MindGraph parser exact revision, no alternate parser/source code. `documents_fts.content` and `documents.metadata_json` are the sole permitted source bytes.
- For #62, use the exact unmodified Draft PR #46 Git archive and the complete private frozen #54 `cases.frozen.json`, preserving original 34 query IDs/order. Add the separately declared Operations `O_SMOKE` only. Test the same top-K lexical-only/no-intent query in legacy and compact CLI modes.
- An offline Python interpreter with full dependency lock/cached exact versions is needed to claim environment identity beyond these source/formula observations. Runtime identity beyond the observed local interpreter is not asserted.

### #61 parser link reproduction

```sh
# Set the exact maintained source tree's src on PYTHONPATH.
PYTHONPATH=/exact/archive/maintained/src python research/vnext-context-baselines/link_resolution_audit.py selftest

# Freeze a manifest containing script_sha256 and input_sha256:
# { "script_sha256":"...", "input_sha256":{
#   "knowledge":"...", "projects":"...", "operations":"..." } }
PYTHONPATH=/exact/archive/maintained/src python research/vnext-context-baselines/link_resolution_audit.py analyze \
  --folder /authorized/private/frozen-snapshots \
  --freeze /authorized/private/LINK-FREEZE.json \
  --out /authorized/private/new-link-result
```

The analyser recomputes raw authored links and parser-produced edge tuples for every indexed FTS document. Each unresolved edge is categorized as an *index membership finding*, never as a confirmed physical-file error or approved cross-scope relationship. Distinct labels mapping to one opaque target hash remain explicitly ambiguous when their explanations differ.

### #62 full CLI transport

```sh
python research/vnext-context-baselines/compact_transport_audit.py selftest

# Freeze all 34 existing queries, the new unlabelled O_SMOKE, exact
# query cohort SHA-256, script SHA-256, archived product SHA-256,
# three snapshot SHAs and top_k=10 before scoring.
python research/vnext-context-baselines/compact_transport_audit.py analyze \
  --folder /authorized/private/vnext-rc0 \
  --freeze /authorized/private/TRANSPORT-FREEZE.json \
  --cases /authorized/private/existing-cases.frozen.json \
  --out /authorized/private/new-transport-result \
  --python /authorized/offline/venv/bin/python
```

Required private folder layout for the transport program: `source-candidate46/` (exact Git tree), `candidate46.source.tar`, and the files `snapshot.knowledge.sqlite`, `snapshot.projects.sqlite`, `snapshot.operations.sqlite`. The runner itself reads them, but does not install or activate any service. It emits full raw CLI JSON to a private result directory and only aggregated/source-identity conclusions to the public JSON.

### Mechanical result verification

```sh
python research/vnext-context-baselines/verify_baselines.py \
  /authorized/private/vnext-rc0 \
  /authorized/private/existing-cases.frozen.json
```

The separately authored verifier reads the actual frozen SQLite edge/chunk rows and saved raw CLI results. It checks per-case JSON identity/rank/citability and verifies selected expanded chunk text against the SQLite `chunks` table.

## What we learned, and what we did not

**Learned:** the original parser perfectly reconstructs existing stored link IDs from frozen source; missing targets are mostly *not indexed in any of the three scopes*. The current #46 compact transport retains source identity but serializes more bytes than the legacy list; operations source binding is available while Knowledge/Projects strict producer binding is refused on the inspected snapshots. Selected expansion validates exact source bytes and rejects malformed/foreign handles on this limited cohort.

**Not learned:** full source-file existence or currentness, a graph-specific benefit over strong Git/SQL search, safe installed MCP/Conduit acceptance, end-to-end model-token savings, correct final task outcomes, or readiness to merge/release/cut over installed clients.

## Next programme work

A separate, freshly frozen experiment should evaluate a **lean producer-bound nomination representation** while preserving authority metadata and handle safety, on unbiased source/task cohorts with actual tokenizer and full-context cost measurements. The current result is a negative wire-size control, not permission to strip fields after inspecting them. Stage 2A chunk representation and the hierarchical graph schema remain separate research lineages under #52/#58. Production promotion and independent actor evaluation have independent gating.

No schema migration, reindex, latent link inference or automatic graph-weight tuning is justified by these artifacts.
