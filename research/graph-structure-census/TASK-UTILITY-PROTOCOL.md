# Task Utility Protocol: Graph relationships versus ordinary search

**Status: PREREGISTRATION TEMPLATE / NO REAL TASK GOLD / NO MODEL ACTORS RUN.**  
Owned by [MindGraph research #56](https://github.com/camerontjs-dot/MindGraph/issues/56), following the structural census in [RESULTS.md](RESULTS.md).

## Decision

Can a competent agent recover a real, source-backed **relationship-dependent answer** using MainFrame's existing document graph that it cannot recover as reliably or economically with ordinary search under the same permitted source scope and context budget?

If the graph cannot represent the relationship at all, record this as a representation/coverage failure. Do not author a link or widen an index to make the graph arm pass.

## What the task oracle must establish before candidate exposure

A fresh, appropriately isolated task author should inspect **authorized source documents and real operator tasks**, without reading graph-topology diagnostics, retrieval outputs, benchmark scores or implementation-driven candidate suggestions.

For every proposed task, freeze in a private, checksummed fixture:

1. **Question and operational provenance:** why this question would be asked, task family, dated source state, and whether it pertains to Knowledge, Projects, Operations, or an explicitly cross-index question.
2. **Required source set:** all mandatory relative source paths; exact stored content hash and/or separately frozen source bytes; source role (current policy, historical decision, test, incident, supporting evidence, etc.); explicit missing/unknown cases.
3. **Relationship claim:** an independently defensible `source --predicate--> target` relation *with a literal documentary basis*, including evidence document identity and a stable locator. A wikilink is not proof the proposition is true.
4. **Authority/temporal requirement:** which source is authoritative; whether a supersession is actually documented; when currentness is unknown.
5. **Negative controls:** unrelated but lexically overlapping files, stale predecessor, missing endpoint, irrelevant graph neighbor, or query answerable directly with no relationship traversal. Unknown relevance remains unknown, not a forced negative.
6. **Eligibility:** whether every mandatory source is represented by the frozen index and index scope under the test. Out-of-index sources are kept as honest separate failure cases, not removed after scores.

Use **separately labelled strata**, not a pooled score that hides a scope failure: (a) within-Projects graph relationship; (b) within-Operations graph relationship; (c) cross-Projects/Operations relation requiring multiple indexes; (d) Knowledge controls and authority traps. As an initial bounded target, try to qualify 8–12 real positive cases across Projects and Operations, plus at least three negative/direct-lookup controls, **only if such tasks actually exist**. Record insufficiency rather than manufacture cases.

## Frozen comparison arms

For each eligible task, use the same snapshot bytes, question, allowed source scopes, information isolation, model/selector identity when required, and total result/context budget:

- **A: FTS5 search.** Existing lexical ranking and explicitly permitted file openings, no graph tools.
- **B: Hybrid search.** Existing lexical + semantic fusion and the same file-opening budget, graph disabled.
- **C: Graph navigation.** Same search starting candidates as B, plus only recorded, explicitly typed or authored source links. Preserve the original retrieval order and show the exact author/edge/source evidence for expansions. An untyped link remains a document association, not a newly invented `depends_on` assertion.

Control for the possibility that C simply surfaces **one more source**: include an equal-budget B expansion, or cost-normalize the additional source openings. No outcome may be graded against gold that C's actor was given.

For cross-index cases, mark which implementation can actually traverse the boundary. If no maintained consumer can do so, report `CROSS_INDEX_TRAVERSAL_NOT_AVAILABLE` and do **not** compare an imagined graph against real search.

## Measurement

**Retrieval-only, initially:** mandatory-source recall; required relationship-chain coverage; additional wrong/unknown/stale/noncitable nominations; same-budget context bytes and tool calls; extra missing-state requests; exact source and edge paths; and representative failure classes.

**Actual agent outcomes, later:** correct final answer with source-attributed claims, correct uncertainty/abstention, task completion, avoidable context cost, and latency. These require independent source-derived answer judgments and a qualified isolated actor runtime. A model selecting handles is not automatically verified task success.

## Oracle and harness controls

Before any decisive B/C result:
- Freeze input IDs and hashes; prevent task/label edits after reveal.
- Independently inspect negative/hard controls and label ambiguity. Seed wrong `supersedes`, reversed `depends_on`, missing-link and unrelated-neighbor mutations in **synthetic evaluation-only** copies and require the evaluator to reject incorrect relation claims.
- Keep authored relations separate from inferred similarity; verify that withheld/unknown source authority cannot silently become current.
- Prove the evaluation apparatus detects a deliberately weak 'always pick first result' selector. If weak systems pass, the apparatus is inconclusive.
- Confirm the actual privacy/isolation boundary; prompt-only instructions are not proof an actor cannot read hidden gold or reference outputs. Prior Stage 1C #33–35 and MainFrame isolated-actor attempts do not confer qualification.

## Dispositions

- `SUPPORTED_WITH_BOUNDS`: source-backed matched-task superiority attributable to explicit graph relationships, without material authority/context regression, under qualified apparatus.
- `NO_OBSERVED_GRAPH_ADVANTAGE`: valid matched controls show no graph benefit.
- `MIXED_OR_COSTLY`: graph rescues some tasks while introducing documented errors/costs.
- `INCONCLUSIVE`: insufficient eligible relationships, unqualified oracle or model isolation, scope mismatch, ceiling/floor saturation, or invalid benchmark.
- `NOT_RUN`: task gold or qualified execution absent.

Only after such evidence should the project decide between improving link hygiene, introducing a small typed-edge contract, adding a derived entity/episode graph, or retaining existing search and navigation. Do not change maintained schemas or project source files in this research PR.

## Smallest next execution packet

A fresh task-authoring role, with permitted *source-file-only* aperture, must first freeze a source-backed private task/gold cohort and record whether it was able to avoid reading graph-run or scorer outputs. If the available computer cannot enforce that isolation, use a clearly marked developer/evaluation **exploratory** cohort and do not claim independent task-selection results.

Then a separate verifier should check source identities, relevance labels and evaluator controls without seeing the graph implementation author's conclusions. Only then activate matched retrieval or agent actors within the independently qualified runtime/cost boundary. This sequence is not permission to restart unrelated Conduit/MainFrame vNext actor programmes.
