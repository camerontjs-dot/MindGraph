# MainFrame Knowledge / Projects search-vs-graph RC0 result

**Date:** 2026-10-08  
**Issue:** [MindGraph #54](https://github.com/camerontjs-dot/MindGraph/issues/54)  
**Study class:** exploratory retrieval research on **private installed-index snapshots**, not independent end-to-end agent evaluation.

## Disposition

**NO_OBSERVED_GRAPH_ADVANTAGE**, separately for Knowledge and Projects, under the preregistered deterministic top-3-seed, one-hop, citable-only, one-addition graph policy.

The bounded graph policy returned no additional labelled mandatory source compared with hybrid search allowed one extra result. This result is limited by the historical query-and-gold selection and does **not** falsify other graph-driven retrieval policies or graph-specific tasks. Agent task completion and progressive source expansion remain **NOT_RUN**.

## Frozen objects

- Product: `8df9ae7fccdb742558950ef9bdedb96cc74df6d0` (tree `44111ff75c53a1862001b9d041944e81a2d1430a`). Clean source obtained through `git archive`, not by executing the dirty local checkout.
- Freeze manifest SHA-256: `6f01637809c13664c99f8fee7d1275933e4d5a14cb859c06388b513a4c5dc114`
- Harness SHA-256: `8db1e62cbe71fe50f4824d3c4e7b20d60ba305d8890d8e7f966b19c467523d38`
- Frozen case manifest SHA-256: `7ed9f2cc6c2933097987393bb7e458b23cea14b7d565154be00f49f0741e8d68`
- Knowledge snapshot SHA-256: `68df830edc854d5a207905ac71abca7d43e8c0b2e4594acce2c13c801bbf3ba1`
- Projects snapshot SHA-256: `516c49feb8dbc3620da7d2b9d9430b242009ef09e6c9fc92146be4ad7975fb95`
- Private decisive output SHA-256: `afb70a939f9db80bf4077568f0167153c6abeb9516837c15676423a6846bf20e`
- Private source and gold objects remain under the local, untracked evaluation operation; no sensitive corpus or raw per-query results are published.

## Results

The numerator is **positive queries with at least one preregistered gold document returned**, not task answers judged correct.

| Scope | Positive queries | Gold-complete-in-index | A10 lexical | B10 hybrid | B11 hybrid | C10+1 graph | Graph admissions |
|---|---:|---:|---:|---:|---:|---:|---:|
| Knowledge | 10 | 7 | 7/10 | 7/10 | 7/10 | 7/10 | 11/12 queries |
| Projects | 19 | 15 | 14/19 | 13/19 | 13/19 | 13/19 | 1/22 queries |

Five additional negative/out-of-scope queries (Knowledge 2; Projects 3) were retained but not scored as positive retrieval. Across all 34 queries, both fixed index snapshots were read-only. There were **zero C-vs-B11 retrieval improvements and zero C-vs-B11 retrieval regressions** on the labelled sources.

Among positive queries whose *entire* gold set was in the tested index: Knowledge 7/7 with all methods; Projects 14/15 lexical versus 13/15 hybrid/graph. Thus one Projects historical required source was retrievable via plain keyword search but absent from the hybrid top 11. The graph arm did not repair it.

### Ranking quality (all positives)

| Scope | A10 MRR | B11 MRR | C10+1 MRR |
|---|---:|---:|---:|
| Knowledge | 0.5500 | 0.5333 | 0.5333 |
| Projects | 0.4288 | 0.4667 | 0.4667 |

MRR measures rank, not correctness of the answer.

### Admission and context costs

- Knowledge: C10+1 admitted 11 graph targets over 12 queries and exposed 12,746 whitespace-token proxies across all returned source chunks. B10 exposed 11,473, and B11 12,863. This study does not establish tokenizer-accurate downstream context cost.
- Projects: C10+1 admitted one graph target across 22 queries; source proxy 21,221 versus B10's 21,102 and B11's 23,286.
- In Knowledge, B11 included 55 non-citable rows across all 12 queries versus 51 in C10+1 and 43 in A10. These are returned-result counts, not graded authority errors or evidence that any claim was correct.
- No observed graph source contributed a labelled required document that hybrid B11 missed.

## Apparatus and bounded verification

The actual experiment used Python 3.14.4, MiniLM `all-MiniLM-L6-v2` loaded offline on `mps:0`, sentence-transformers 5.6.0, torch 2.12.1, numpy 2.4.6 and sqlite-vec 0.1.9. An immutable model cache revision was **not identified by the harness** and is explicitly `UNKNOWN` in the decisive receipt.

Both corpus snapshots are contemporaneous index backups, not source-file recrawls or verified current-file manifests. Knowledge held 4,075 documents / 19,219 chunks / 6,742 edges; Projects 812 documents / 8,841 chunks / 189 edges. Three Knowledge gold anchors and four Projects gold anchors were missing from their respective indexed source sets **before scoring**. They remained missing rather than being relabelled after the result.

Ten deterministic self-checks passed before the freeze. After the decisive run, a separately written read-only verifier passed across 34 cases, recomputing per-arm source recall from preserved rankings, verifying graph edge membership and top-three seed lineage, comparing all B10 prefixes, and checking frozen artifact hashes. The verifier is a separate implementation of the scoring check, but **not an independently isolated research actor or independent human oracle**.

## Engineering consequence

There is no evidence from this workload to justify a mandatory graph expansion step for every MainFrame query, graph-driven default ranking, or a production migration. Ordinary keyword retrieval remains competitive, and the Projects hybrid miss deserves its own failure-class analysis without changing this frozen record.

A genuinely discriminating *new* study would use source-selected graph-dependency tasks and matched agent outcomes, with independent gold and actor-context/isolation controls. Its prerequisite model-runner qualification must not be bypassed by reusing the blocked Stage 1C apparatus (#33–#35). Such a successor must preserve this negative RC0 unchanged.
