# MindGraph vNext RC0: link resolution and compact-transport characterization

**Date:** 2026-10-09  
**Class:** Research / Research Infrastructure, no product changes.  
**Owners:** [Programme #60](https://github.com/camerontjs-dot/MindGraph/issues/60), [link diagnosis #61](https://github.com/camerontjs-dot/MindGraph/issues/61), [compact transport #62](https://github.com/camerontjs-dot/MindGraph/issues/62).

**Overall disposition:** BOUNDED_MECHANICAL_CHARACTERIZATION_COMPLETE. Source or model/task utility and production integration remain **UNQUALIFIED**. These independent experiments answer different questions; do not combine their source identity, parser or retrieval claims.

## Exact source and input identity

| Object | Frozen identity |
|---|---|
| Maintained MindGraph | `8df9ae7fccdb742558950ef9bdedb96cc74df6d0`, tree `44111ff75c53a1862001b9d041944e81a2d1430a` |
| Candidate compact/expansion MindGraph | [Draft PR #46](https://github.com/camerontjs-dot/MindGraph/pull/46), `11f6f8161dbf6cb78ebc1363a28948a4606fe6e0`, tree `d436bc7e6b1b9aaa0b2f58c8da6114729f57230c` |
| Maintained source archive | SHA-256 `7af0237dbe55043d2f99d2af0b8373fafa0a0936be4ef1473a9950e8cd46dffb` |
| Candidate source archive | SHA-256 `f2420bedac813f45969a67152f9f10054c431f5cb06565f67090f814137e1fe4` |
| Original pre-native-test freeze | SHA-256 `ca21b3f198c17a8cdca4857b713f85bc278937c2dff75c5892888d26e10c35e8` |
| Knowledge frozen index | SHA-256 `68df830edc854d5a207905ac71abca7d43e8c0b2e4594acce2c13c801bbf3ba1` |
| Projects frozen index | SHA-256 `516c49feb8dbc3620da7d2b9d9430b242009ef09e6c9fc92146be4ad7975fb95` |
| Operations frozen index | SHA-256 `5fd094da67ebe4184cfb616e1f07d9659fddaac817851c8b5b1d66dec776f546` |

SQLite online backups were used, with integrity `ok` in all three; production indexes and installed daemons were not mutated. The source was extracted from exact Git archive objects into a disposable local private experiment directory, not run from the dirty local MindGraph checkout.

### Native candidate checks

Exact archived PR #46 source executed under local Python 3.14, offline HF/Transformers environment, candidate-local `PYTHONPATH`, using its existing independent Python environment. Full suite **476/476 PASS** in 176.42 seconds. This is an **owner-controlled local source run**, not new independent semantic qualification, not installed Daemon/MCP/Conduit acceptance, and not evidence for a safe production cutover. Past client quiescence blocker remains.

## #61: Original source-text link reconstruction

The parser was used against **original frozen indexed Truth text in `documents_fts` and indexed metadata**, not current mutable Markdown files. It regenerated every stored link tuple exactly (source ID, target ID, edge label): Knowledge 6,742/6,742; Projects 189/189; Operations 19/19. No mismatched stored-only or reconstructed-only tuples. All original out-of-index target edges were attributable to a preserved source link label.

| Category | Knowledge | Projects | Operations |
|---|---:|---:|---:|
| Stored edges | 6,742 | 189 | 19 |
| Both endpoints indexed in selected scope | 6,051 | 56 | 6 |
| Target absent from selected scope | 691 | 133 | 13 |
| Of missing targets: matched a candidate in another frozen index | 19 | 10 | 1 |
| Of missing targets: ambiguous matching label in selected scope | 36 | 2 | 0 |
| Of missing targets: no resolvable target in any of these three frozen indexes | 636 | 121 | 12 |
| Stored parser tuples not reproduced | **0** | **0** | **0** |

**Observed:** the maintained parser exactly reproduces stored authored edge tuples in all three frozen indexes. In Projects and Operations, **most of the unresolved target labels are not resolvable anywhere in the three installed index snapshots**, not simply resolving to the other indexed scope. Only 10 Projects and one Operations unmatched targets had a unique other-scope candidate under the present resolver. Ambiguous links remain unresolved; an extra target in another index is a candidate nomination, not a validated edge or authorization to traverse.

**Inference:** automatically “repairing” the parser is poorly justified by these results; the next useful source-corpus question is indexing eligibility/coverage, explicit source relationship authoring and target existence or status *at the source owner*, without auto-creating provenance. A link's absence from an index does **not** prove its physical file is missing. There is no demonstrated general source-link health score, causal explanation for missing documents, or end-to-end graph value.

#61 frozen apparatus SHA-256 `4bf1fa48dd7ec6ffb531cbdc3541d3566874d81a2fc1da4d898523ca4b812140`, pre-result freeze SHA-256 `c9edc387f83ec38eac779f696210a8edd57d7c67a3bbe4870793b2e4524344bb`, private result SHA-256 `a05d54e84d9d671ff270526d425a0216b852490cad5e7e1badfa325d01bb5f93`.

## #62: Same-retrieval actual CLI payload costs

The exact PR #46 source was tested in **two forms** on every existing historical [#54](https://github.com/camerontjs-dot/MindGraph/issues/54) query: `--json --lexical-only --no-intent --top-k 10` legacy full list versus the same retrieval with `--envelope --nominations --nomination-scope <scope>`. All 34 historical cases were used, including negative/missing-gold cases, plus a separately identified Operations **unlabelled smoke** query. No embedding or model was invoked for this comparison. These historical questions are **not new independent relevance labels**.

**35/35** source identity/order/hash/citability comparisons passed between the actual legacy and compact output. However, the nominally compact serialization delivered **more wire bytes**:

| Scope | Queries | Legacy JSON bytes | Compact JSON bytes | Compact/legacy |
|---|---:|---:|---:|---:|
| Knowledge | 12 | 239,437 | 292,314 | 1.221 |
| Projects | 22 | 393,051 | 486,169 | 1.237 |
| Operations smoke | 1 | 17,447 | 21,959 | 1.259 |

The initial source text exposed was smaller: Knowledge source chunk characters 89,647 (legacy) versus 32,791 (compact source previews); Projects 163,156 versus 58,709; Operations 7,336 versus 2,654. **Less preview text does not imply smaller complete payload.** Metadata, repeated identity and handles dominate serialization. Strict producer-bound identity was **refused (nonzero, zero output)** against the frozen Knowledge and Projects indexes, and **present** on Operations. We did not force a fallback or repair the existing index.

A selected first nomination was explicitly expanded via the actual CLI in each scope: source ID/chunk/hash and, in an independent-formula read-only verifier, exact SQLite chunk bytes all matched. A foreign caller scope and malformed handle returned nonzero and **no source text** in all three scopes. This measures only selected source transport, **not** an agent selecting a correct nomination.

**Post-result exploratory diagnostic, not the preregistered primary comparison:** Symmetrical JSON minification left compact larger (Knowledge 251,409 vs 216,386 bytes; Projects 412,418 vs 354,698; Operations 18,665 vs 15,749). The nominal `expansion_handle` field alone was the largest approximate repeated-field contributor. Minifying JSON or dropping fields cannot establish reliable total model context savings; encoded handles may be particularly expensive in actual model tokens. No optimization was shipped or tuned to the historical corpus.

#62 original frozen case input SHA-256 `7ed9f2cc6c2933097987393bb7e458b23cea14b7d565154be00f49f0741e8d68`. Apparatus SHA-256 `fa8c80a981ffe260d03a30972579dea5418777d5feeca24159d5e490f199466a`. Freeze SHA-256 `3519b03a09803cfbc9441c40f22886452551a4b010a2c8ed0a361841a1a07521`. Private decisive result SHA-256 `7d6952f8b9ff19d6d914dcad2719d63bf27c2abd4670eb26f381186677f7489c`.

## Validation, source separation and decision

A separately implemented [verify_baselines.py](verify_baselines.py) recomputed direct SQLite stored/missing edge totals and per-category sums from the private raw ledger, then verified **every** raw legacy/compact CLI payload against its original output digest and identical source/rank/citation fields. It also re-opened the actual frozen `chunks` tables to verify selected expansion text against indexed bytes and refused wrong-scope/malformed handles. Result: `VERIFY_PASS`.

This verifier is independent-formula code in the same operator development context, **not a context-free/independently isolated model or a new semantic oracle**. Hidden model runtime qualification in #33–35 and the MainFrame #80 actor isolation boundary remain unresolved.

**Engineering decision:** preserve maintained hybrid retrieval and its graph-admission mechanism; keep #46 unmerged pending independent consumer/cutover gates. Prioritize:
1. Exact indexed scope and source/currentness controls, especially Knowledge/Projects producer binding and Projects/Operations overlay from [MainFrame #99](https://github.com/camerontjs-dot/mainframe-live/issues/99).
2. A new, separately frozen **lean nomination representation** benchmark that preserves exact source identity, citation/eligibility, retrieval reasons and index-bound expansion semantics, with both byte and actual model-token/task measurements. Do not claim wire savings now.
3. A source-only, independently labelled real relationship-task corpus to establish whether optional per-project graph navigation adds value over direct Git/SQL; use [#59](https://github.com/camerontjs-dot/MindGraph/pull/59) as a bounded source-bound research schema, not a production migration.
4. Stage 2A representation replication [#52](https://github.com/camerontjs-dot/MindGraph/issues/52) only when its preregistered public and private baseline-headroom gates pass. No opportunistic candidate scoring.
5. Qualified isolated selector/consumer before declaring actual downstream savings or production readiness.

**No maintained product, source corpus, embedding weights, installed index, service/client, PR base or release was modified.** Raw case queries, source text, source paths and detailed per-edge graph material were preserved privately only; public artifacts contain portable scripts and sanitized aggregates.
