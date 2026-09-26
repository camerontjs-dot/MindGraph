# MindGraph vNext: nomination and retrieval plan

Status: active vNext plan with preserved convergence record  
Original planning base: `main@6501024a19dd9feae415a7197a65c70ff214c40a`  
Reconciled through: `main@2bf90517da2dedca17deede0ecdf51ebd2d3d489`

## Objective

Improve MindGraph as MainFrame's retrieval layer so it can surface the right context with less context-window waste while remaining useful in two first-class modes:

1. direct use from MainFrame by a human or agent;
2. use through Conduit as a context source for the Context Compiler.

The next version should not turn retrieval into hidden prompt construction. MindGraph nominates potentially useful context. A consumer decides what to inspect or admit.

## Current baseline

The live standalone engine already provides:

- lexical FTS5 BM25 retrieval;
- optional semantic retrieval through MiniLM, BGE-small, or E5-small;
- Reciprocal Rank Fusion over lexical and semantic ranks;
- graph expansion over typed wikilink edges;
- semantic association from fused seeds;
- a MainFrame embedding template that prefixes document metadata at ingest and query intent at retrieval time;
- provenance and trust metadata in query results;
- explicit weak-fit and semantic-distance signals;
- CLI and MCP access over the same retrieval implementation;
- an opt-in bounded typed `GraphAdmission` sidecar over already-produced depth-1 graph expansion, with legacy defaults unchanged;
- a stated authority boundary: retrieval is nomination, not verification.

vNext should improve that system by measurement rather than replacing it by intuition.

## Convergence record through 2026-09-23

This plan began on the original planning base above. The branch was later reconciled with promoted `main` by ancestry-preserving merge rather than rebasing or rewriting the experimental history.

The durable evidence chain now includes:

- **#6 — frozen RC1 evaluation authority.** The MainFrame-specific retrieval apparatus was repaired, frozen, independently reproduced, and accepted as `PASS_FOR_RC1_AUTHORITY`.
- **#7 — bounded reranking experiment.** A pinned MiniLM cross-encoder improved one held-out authority-ordering case under the locked top-10 reranking protocol.
- **#8 — reranking replication.** The same pinned MiniLM candidate produced zero ordering benefit on a new document/family-disjoint set. Disposition: `RERANKING_NOT_REPLICATED`. The #7 observation remains preserved, but reranking was not promoted.
- **#9 — graph admission experiment.** The isolated graph-only miss was reproduced as a consumer-cutoff failure: the required leaf was already present in frozen expanded output beyond the fused window. A bounded authored-link-gated admission policy rescued it with lower context cost. Disposition: `GRAPH_ADMISSION_SUPPORTED`.
- **#10 — implementation decision.** The production contract was narrowed to additive, typed, bounded graph admission with explicit provenance, exact duplicate suppression, no invented currentness, and unchanged legacy defaults.
- **#11 / PR #12 — implementation and independent qualification.** The candidate passed focused and full repository tests, actual CLI/single-MCP/shared-MCP transports, scoped provenance checks, deterministic identity checks, and frozen RC1/#9 replay. Independent disposition: `PASS_FOR_BOUNDED_GRAPH_ADMISSION`.
- **#13 — promotion review.** PR #12 was promoted and squash-merged as `main@2bf90517da2dedca17deede0ecdf51ebd2d3d489`.
- **Conduit #58 — deferred consumer adapter.** A note records that Conduit should eventually consume the typed `graph_admissions` sidecar without flattening graph provenance or moving final context-admission authority out of the Context Compiler. No Conduit implementation was started as part of this MindGraph work.

Negative and irregular evidence remains part of the record:

- #8's reranking non-replication is preserved rather than averaged away or reframed as a win.
- The historical #9 runner receipt remains `GRAPH_ADMISSION_INCONCLUSIVE / prefix_integrity` because its fixed ten-row comparison trips on a short-output case. Independent length-aware qualification demonstrated the intended prefix property without rewriting that receipt.
- Raw freshness remains `UNKNOWN` unless stronger source evidence exists. Graph linkage does not upgrade source authority.
- No reranker, embedding adaptation, graph-traversal rewrite, or Conduit adapter has been promoted.

What remains open in vNext:

- a canonical typed nomination contract for ordinary retrieval results, not only graph admissions;
- compact human/agent projections and progressive expansion handles;
- representation/chunk/context experiments before any model adaptation;
- broader retrieval and graph ablations beyond the bounded admission mechanism;
- query rewriting or multi-query retrieval only when a frozen failure class justifies it;
- embedding adaptation only if the later fine-tuning gate is actually met;
- clean portability/public-core work tracked separately in #2.

## Failure model

The evaluation should classify retrieval failures before selecting a remedy. At minimum distinguish:

- **lexical mismatch**: relevant material uses different terms from the query;
- **dense semantic mismatch**: the embedder fails to bridge the intended concept or domain vocabulary;
- **chunk/context misalignment**: the right source exists but the indexed representation omits the title, section, neighboring context, or other information needed to retrieve or interpret it;
- **first-stage recall failure**: the relevant item never enters the candidate pool;
- **ranking failure**: the relevant item is in the candidate pool but ranks too low;
- **graph-only relevance**: linked material is useful but not recoverable from text similarity alone;
- **duplicate/redundant admission**: repeated material consumes budget without adding information;
- **stale/superseded admission**: historically related material outranks the current source when currentness is known;
- **query ambiguity**: one query mixes intents or leaves the intended scope underdetermined;
- **authority confusion**: a derived summary, agent output, or weaker-trust source is surfaced without preserving its different authority;
- **context-budget failure**: useful retrieval is technically correct but too expensive to hand to the consumer.

A retrieval change should be justified by the failure class it measurably improves. Do not treat every miss as an embedding-model problem.

## Product contract: canonical nomination

Introduce a stable, typed nomination object as the canonical retrieval product. Human and agent surfaces should be projections of the same underlying result.

A nomination should carry enough information for a consumer to decide whether to spend more context on it without requiring the full source chunk up front.

Candidate fields:

- stable nomination identity;
- nomination kind, such as source section, document, graph neighbor, community/cluster, receipt, or derived item;
- concise title;
- compact contextual description;
- exact source identity and path;
- source range or chunk identity where available;
- authority/provenance class;
- freshness or staleness state when evidenced;
- retrieval reasons;
- signal-specific ranks or diagnostics;
- graph relationship/path where relevant;
- representation level;
- token estimate;
- expansion handle.

Raw embedding distances and low-level diagnostic scores should remain available for debugging and evaluation, but should not be the primary human or agent explanation.

## Human-readable projection

Direct MainFrame use and Conduit should both have a compact human surface.

A result should make these questions cheap to answer:

- What is this?
- Why did MindGraph nominate it?
- Where did it come from?
- Is it current?
- What kind of authority does it carry?
- How expensive is it to inspect?
- What happens if I expand it?

The human view should support progressive disclosure:

`nomination -> contextual excerpt -> surrounding section -> graph neighborhood -> source`

Conduit may render this as interactive cards or graph nodes, but those UI choices must not become part of MindGraph's retrieval semantics.

## Agent-readable projection

Expose the canonical nomination as structured output through the CLI/MCP boundary.

The first response should be compact enough that an agent can scan several nominations cheaply and choose which ones to expand.

The agent-facing surface should preserve:

- typed authority/provenance;
- exact source identity;
- retrieval reason;
- graph relationship;
- freshness;
- compact preview;
- explicit expansion handle.

Do not require an agent to parse human prose or reconstruct provenance from a display string.

## Projection invariant: one retrieval, separate human and agent apertures

MindGraph should produce one canonical retrieval event and expose different projections of that same event for different consumers.

**Agent projection:** default to the minimum sufficient nomination surface that still lets an agent decide whether expansion is worth the context cost. The normal agent-facing response should not contain full source chunks. It should preserve the fields needed for selection and safe expansion: stable nomination identity, concise title, exact source identity, typed authority/provenance, freshness when evidenced, retrieval reason, compact exact preview, and expansion handle. Low-level scores and diagnostics remain available for debugging/evaluation but are not part of the default agent aperture unless evidence shows they improve selection.

Do not minimize the aperture by intuition alone. The current Stage 1A surface, including the exact preview budget already qualified in PR #22, is the baseline. Any further reduction in preview length or fields is a new selector surface and should be evaluated for mandatory-context loss before promotion.

**Human projection:** Conduit and direct human use may present a richer inspection surface over the same nominations, including the complete retrieved chunk, surrounding section, graph neighborhood, retrieval diagnostics, and navigation to the source. Rich human visibility is an inspection affordance, not context admission.

Product invariant:

> **Human visibility must not imply agent-context admission.**

A human may inspect every retrieved candidate without those chunks entering an agent's working context. Agent context admission remains an explicit downstream decision by the operator, worker, or Context Compiler under its own authority.

The human surface should populate detail by resolving the **same expansion handles** produced by the original retrieval. It should not silently rerun retrieval merely to render a richer view, because that can create two different retrieval moments under one displayed query. A failed or stale expansion must remain a visible failure rather than being replaced by a nearby source.

This yields three distinct objects that must remain distinguishable:

1. the MindGraph retrieval/nomination set;
2. the human inspection surface over that set;
3. the subset actually admitted to an agent context manifest.

Conduit may eagerly resolve nominations for operator inspection if useful, but that eager inspection must not alter the agent projection or automatically attach the expanded text to a worker.

## Progressive retrieval

Add an explicit two-stage operating model:

1. nominate compact candidates;
2. expand only selected nominations into source-backed context.

This should allow a worker to receive, for example, several low-cost nominations and request full excerpts only for the few that appear consequential.

Progressive retrieval is a context-efficiency mechanism, not an authority mechanism. Expansion does not strengthen the epistemic status of a result.

## MainFrame-specific retrieval evaluation

Before changing the embedder, ranking policy, graph weighting, or chunk format, build a frozen evaluation corpus from real MainFrame retrieval needs.

Each evaluation case should contain:

- a realistic query or task objective;
- a frozen source corpus;
- mandatory relevant sources/chunks;
- useful optional sources;
- irrelevant/trap material;
- stale or superseded variants where available;
- near-duplicate material;
- hard negatives that are topically similar but operationally wrong;
- graph-linked relevant material that lexical or semantic retrieval may miss;
- authority-confusion traps, such as an agent summary beside an authoritative source.

Split the corpus before tuning so later changes cannot be evaluated only on the examples used to design them.

Prefer real historical MainFrame queries where they can be used safely. Synthetic queries may augment the set, but they should not replace a human-judged held-out evaluation set.

## Metrics

Primary retrieval metrics:

- mandatory-context Recall@K;
- Precision@K where top-K cleanliness matters;
- MRR and/or nDCG where ordering or graded relevance is useful;
- irrelevant-context admission;
- hard-negative confusion rate;
- stale/superseded admission;
- graph-rescue rate;
- duplicate token waste.

Context-efficiency metrics:

- tokens surfaced per successful query;
- tokens expanded per successful query;
- percentage of nominations expanded by an agent or human;
- nomination selection precision/recall: whether a consumer chooses the right items to expand from the compact projection;
- missed-required-context caused by compact nomination;
- context budget required to hit the target recall.

Operational metrics:

- query latency;
- rerank latency if enabled;
- model memory;
- index size;
- incremental re-index cost;
- deterministic/reproducible result identity where expected.

Authority metrics:

- provenance loss;
- source/derived confusion;
- missing/unknown state converted into an invented fact;
- stale state presented as current.

## Retrieval optimization sequence

### Stage 0: freeze the evaluation harness — completed for RC1

Establish the MainFrame-specific retrieval corpus and baseline current `main`.

Record lexical-only, semantic-only, current RRF, graph expansion, and association behavior.

Do not tune against the held-out split.

### Stage 1: nomination contract and progressive expansion — partially advanced

Implement the canonical result contract and compact human/agent projections without changing retrieval ranking.

The promoted `GraphAdmission` object is the first production-shaped typed nomination, but it is deliberately only a graph-derived sidecar. The broader contract for ordinary lexical/semantic/fused nominations and explicit expansion handles remains open.

Measure whether compact nominations let consumers choose useful expansions without materially reducing required-context recall.

### Stage 2: improve representation before training — not yet run

Evaluate changes that do not alter model weights:

- better deterministic metadata/context prefixes;
- document title/domain/type/section context;
- neighboring heading or bounded structural context;
- chunk boundaries aligned to Markdown structure;
- deterministic contextualized chunks;
- generated chunk-specific context as a separate, explicitly derived indexing feature;
- query intent templates;
- deduplication and stale/superseded handling.

Generated indexing context must remain distinguishable from source text. It may improve retrieval, but it must not become source authority or silently alter what the source says.

The purpose is to determine whether the apparent embedding weakness is actually a representation problem.

### Stage 3: retrieval and graph ablations — bounded graph-admission slice completed

Compare:

- lexical only;
- semantic only;
- current RRF;
- lexical + semantic + graph features;
- association;
- graph expansion;
- graph proximity or typed-edge features as explicit ranking signals;
- bounded query rewriting or multi-query retrieval when ambiguity/lexical mismatch is demonstrated;
- alternative fusion/ranking only when a failure case justifies it.

Do not assume that every graph edge is a relevance edge. Graph features should preserve edge type and be evaluated for both rescue and noise.

Keep signal attribution inspectable.

### Stage 4: reranking — evaluated once, not promoted

If first-stage retrieval has good recall but poor ordering or excessive irrelevant admission, evaluate a lightweight reranker over a bounded top-N candidate set.

The first bounded MiniLM experiment (#7) produced a gain on one held-out authority case, but the document/family-disjoint replication (#8) produced no ordering benefit. Treat reranking as parked unless new frozen evidence reopens the failure class; do not integrate it from #7 alone.

Reranking is preferable to embedding fine-tuning when the correct material is already being retrieved but ranked poorly.

Keep the first-stage retrieval receipt available so reranking does not erase why an item was originally nominated.

Initial reranker experiments should favor small, local cross-encoders over large general models. Suitable families to benchmark include MS-MARCO MiniLM cross-encoders and compact BGE rerankers. Candidate-set size, latency, memory, input-length limits, and Apple Silicon behavior are part of the result.

A reranker is indicated when first-stage Recall@K is already high but the required item is consistently ordered poorly. It cannot repair a source that never entered the candidate set.

### Stage 5: embedding adaptation experiment — not authorized

Fine-tune an embedding model only if the frozen evaluation shows a persistent domain-specific dense-retrieval gap after representation, hybrid retrieval, graph signals, and reranking have been tested.

Candidate training data:

- real query -> relevant passage pairs from MainFrame use;
- manually judged positives;
- hard negatives mined from current retrieval failures;
- synthetic queries generated from MainFrame documents, then filtered;
- graph-informed positives only where the graph relation actually implies retrieval relevance.

Candidate methods should include contrastive retrieval training with hard negatives. Evaluate training recipes such as MultipleNegativesRankingLoss or cached large-batch variants, guided-negative approaches such as GIST-style training where justified, and Matryoshka-style objectives when reduced embedding dimensions are operationally valuable.

If labeled data remains sparse, evaluate weakly supervised domain-adaptation methods such as GPL-style generative pseudo-labeling, InPars/Promptagator-style synthetic query generation, and teacher/reranker pseudo-labeling or distillation. These are candidate apparatus, not assumed improvements.

Avoid training on all graph neighbors as positives. A wikilink establishes a relationship, not necessarily that either document answers the same query.

### Stage 6: optional reranker adaptation — not authorized

If a general reranker remains the limiting step, evaluate fine-tuning a small reranker on the same judged query/positive/hard-negative triples.

Do not fine-tune both the embedder and reranker simultaneously before isolating which component is limiting retrieval quality.

## Fine-tuning decision gate

An embedding fine-tune is justified only when all are true:

1. a frozen MainFrame evaluation set exists;
2. baseline retrieval failures are characterized;
3. representation/chunking/context changes have been tested;
4. lexical + dense + graph behavior has been measured;
5. reranking has been evaluated where appropriate;
6. there is enough training signal to avoid merely memorizing the evaluation corpus;
7. the adapted model improves held-out MainFrame retrieval by a preregistered margin;
8. gains survive negative controls and do not materially degrade general retrieval cases;
9. inference/index cost remains acceptable for normal local MainFrame use;
10. the improvement survives a held-out test split whose documents/queries were not used to generate or tune the training pairs.

The required improvement margin should be preregistered after the baseline is measured. Do not hard-code a recall threshold or percentage gain before the benchmark reveals the current error distribution and operational cost.

If these conditions are not met, retain the general embedder.

## Candidate model posture

Do not select a new default model in this planning PR.

The current selectable MiniLM, BGE-small, and E5-small models are useful baselines. Later evaluation may add newer compact or long-context families such as GTE/ModernBERT, Nomic-style Matryoshka embeddings, EmbeddingGemma, Qwen embedding models, or stronger BGE/E5 variants when they are locally practical and their licenses/runtime requirements are acceptable.

Do not copy public leaderboard ordering into the product decision. Long context, multilingual support, code retrieval, or larger parameter counts are useful only if they improve the MainFrame benchmark enough to justify their memory, latency, index-size, and re-index cost.

If training is attempted, keep the model/backend pluggable and preserve the ability to rebuild an index against the previous model for comparison. Verify exact model revision, license, dimensions, context length, and runtime requirements at experiment time rather than freezing fast-moving model-card facts in this plan.

## Training-data governance

MainFrame data is not an ordinary public training corpus.

Local/offline processing is the default for MainFrame-specific retrieval evaluation, synthetic-query generation, reranking, and model adaptation. Sending MainFrame text to an external service requires a separate explicit authorization and a defined data-egress boundary.

Any model-adaptation experiment should state:

- what data classes are eligible;
- whether model training remains entirely local;
- whether any text leaves the machine;
- how synthetic queries are generated;
- how positives and negatives are judged;
- how train/dev/test leakage is prevented;
- how model artifacts are versioned;
- whether the resulting weights are private or publishable.

Do not publish adapted weights or training examples by default.

Weak labels need special care:

- a wikilink establishes a relationship, not query relevance;
- project membership establishes scope proximity, not answer relevance;
- an agent opening a file does not prove the file was useful;
- an unclicked or unselected result is not automatically a negative;
- hard negatives should be manually judged, teacher-scored, or otherwise justified strongly enough for the training claim;
- train/dev/test separation should prevent the same source-derived synthetic task from leaking into evaluation.

## Compatibility

Preserve the current retrieval authority boundary.

The existing list-shaped CLI/MCP query result should remain available during migration unless a separately justified breaking version is chosen.

Prefer an additive vNext envelope/projection or explicit mode first, then remove legacy shapes only under normal version governance.

Database/index format changes should be explicit. Changing the embedding model must continue to require a separately identified/rebuilt index unless compatibility is established.

## Relationship to Conduit

Conduit issue #58 owns context compilation and final agent handoff.

MindGraph owns retrieval nominations and expansion.

The boundary should remain:

`MindGraph retrieves/nominates -> Conduit ranks/combines context sources -> operator/agent receives a manifest-backed handoff`

Conduit should not need a private understanding of MindGraph internals to interpret a nomination.

Direct MainFrame consumers should receive the same canonical nomination semantics without requiring Conduit.

## Research basis

The vNext evaluation should explicitly test ideas supported by current retrieval literature and tooling rather than adopting them wholesale:

- hybrid lexical + dense retrieval;
- chunk-specific contextualization before indexing;
- reranking over a high-recall candidate set;
- hard-negative mining;
- contrastive embedding training;
- synthetic query generation for domain adaptation;
- pseudo-labeling or teacher scoring when labeled queries are sparse;
- graph retrieval as a distinct signal rather than hidden dense-score modification.

These are hypotheses for MainFrame, not assumed improvements.

Primary references for the research plan:

- Cormack, Clarke, and Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and individual rank learning methods* (SIGIR 2009): https://doi.org/10.1145/1571941.1572114
- Anthropic, *Contextual Retrieval*: https://www.anthropic.com/engineering/contextual-retrieval
- Sentence Transformers training/loss documentation: https://www.sbert.net/docs/package_reference/sentence_transformer/losses.html
- FlagEmbedding fine-tuning and hard-negative guidance: https://github.com/FlagOpen/FlagEmbedding
- Wang et al., *GPL: Generative Pseudo Labeling for Unsupervised Domain Adaptation of Dense Retrieval*: https://arxiv.org/abs/2112.07577
- Bonifacio et al., *InPars: Data Augmentation for Information Retrieval using Large Language Models*: https://arxiv.org/abs/2202.05144
- Dai et al., *Promptagator: Few-shot Dense Retrieval From 8 Examples*: https://arxiv.org/abs/2209.11755

The external results above justify testing these mechanisms, not assuming that their reported gains transfer to MainFrame.

## Current decision after convergence

Do **not** initiate embedding fine-tuning or a reranker integration as the next vNext implementation step.

The frozen evaluation and first behavioral branches have now done useful discrimination:

- the RC1 evaluation authority is frozen and independently reproduced;
- bounded reranking was tested and did not replicate on a new disjoint set;
- the graph-only miss was isolated to a consumer cutoff and the bounded typed admission mechanism was independently qualified and promoted.

The next preferred vNext slice is therefore **Stage 1 canonical nomination and progressive retrieval for ordinary results**, implemented without changing ranking. Use the promoted `GraphAdmission` as a concrete design precedent for deterministic identity, provenance, authority preservation, explicit unknowns, and additive compatibility.

Keep that slice small: define a canonical nomination envelope/projection for existing lexical/semantic/fused results plus explicit expansion handles, then evaluate whether a human or agent can select the right sources from compact nominations on the frozen benchmark without increasing context waste or losing required context.

After that, proceed to Stage 2 representation experiments before considering any new model training.

The current decision tree is:

- **nomination/selection failure** -> improve the canonical nomination or progressive-expansion surface;
- **representation failure** -> improve chunk/context representation;
- **candidate recall failure** -> improve first-stage retrieval, query formulation, or justified graph candidate generation;
- **ordering failure with adequate recall** -> reranking may be reopened only with new frozen evidence;
- **graph-only miss already present in expansion** -> use the promoted bounded typed admission mechanism;
- **persistent domain-specific dense-recall failure after the above** -> authorize a separate embedding-adaptation experiment.

## Non-goals

This plan does not:

- make MindGraph an answer generator;
- make retrieval output verified truth;
- move Conduit's context-compiler authority into MindGraph;
- choose a new embedding model by benchmark ranking alone;
- authorize publishing MainFrame data or adapted weights;
- require embedding fine-tuning;
- collapse lexical, semantic, graph, and reranker signals into an unexplained confidence score;
- replace exact-source provenance with generated summaries;
- optimize only for a single agent or Conduit client.

## Current acceptance state for vNext planning

The original planning gates are now partly satisfied by the frozen RC1 apparatus and the promoted graph-admission slice. The remaining near-term acceptance target is the broader canonical nomination/progressive-retrieval contract, not additional model tuning.

The planning phase is ready to move into each bounded implementation/research slice when:

1. the nomination schema is reviewable;
2. direct MainFrame and Conduit projections are both represented;
3. the progressive expansion boundary is explicit;
4. a MainFrame retrieval-evaluation corpus design is frozen;
5. current retrieval is baselined on that corpus;
6. the first experimental slice can change one retrieval variable at a time;
7. embedding fine-tuning remains a gated experiment rather than a presumed destination.
