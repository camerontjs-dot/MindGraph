# MindGraph vNext: nomination and retrieval plan

Status: planning candidate  
Base inspected: `main@6501024a19dd9feae415a7197a65c70ff214c40a`

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
- a stated authority boundary: retrieval is nomination, not verification.

vNext should improve that system by measurement rather than replacing it by intuition.

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

### Stage 0: freeze the evaluation harness

Establish the MainFrame-specific retrieval corpus and baseline current `main`.

Record lexical-only, semantic-only, current RRF, graph expansion, and association behavior.

Do not tune against the held-out split.

### Stage 1: nomination contract and progressive expansion

Implement the canonical result contract and compact human/agent projections without changing retrieval ranking.

Measure whether compact nominations let consumers choose useful expansions without materially reducing required-context recall.

### Stage 2: improve representation before training

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

### Stage 3: retrieval and graph ablations

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

### Stage 4: reranking

If first-stage retrieval has good recall but poor ordering or excessive irrelevant admission, evaluate a lightweight reranker over a bounded top-N candidate set.

Reranking is preferable to embedding fine-tuning when the correct material is already being retrieved but ranked poorly.

Keep the first-stage retrieval receipt available so reranking does not erase why an item was originally nominated.

Initial reranker experiments should favor small, local cross-encoders over large general models. Suitable families to benchmark include MS-MARCO MiniLM cross-encoders and compact BGE rerankers. Candidate-set size, latency, memory, input-length limits, and Apple Silicon behavior are part of the result.

A reranker is indicated when first-stage Recall@K is already high but the required item is consistently ordered poorly. It cannot repair a source that never entered the candidate set.

### Stage 5: embedding adaptation experiment

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

### Stage 6: optional reranker adaptation

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

## Current decision after research

Do **not** initiate embedding fine-tuning as the next vNext implementation step.

The first successor should be research infrastructure: freeze a MainFrame retrieval benchmark and baseline the current engine. After that, change one retrieval variable at a time.

The decision tree is:

- **representation failure** -> improve chunk/context representation;
- **candidate recall failure** -> improve first-stage retrieval, query formulation, graph candidate generation, or only then the embedder;
- **ordering failure with adequate recall** -> evaluate reranking;
- **graph-only misses** -> evaluate bounded graph candidate/ranking features;
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

## Initial acceptance for vNext planning

The planning phase is ready to move into bounded implementation/research when:

1. the nomination schema is reviewable;
2. direct MainFrame and Conduit projections are both represented;
3. the progressive expansion boundary is explicit;
4. a MainFrame retrieval-evaluation corpus design is frozen;
5. current retrieval is baselined on that corpus;
6. the first experimental slice can change one retrieval variable at a time;
7. embedding fine-tuning remains a gated experiment rather than a presumed destination.
