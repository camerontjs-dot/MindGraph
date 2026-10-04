# Stage 2A section-boundary representation apparatus

Owner: issue #50.

This directory is research apparatus only. It does not change MindGraph ingestion,
chunking, ranking, indexes, or public output.

The experiment compares one variable:

- **A:** current `chunk_truth` over the entire Truth body;
- **B:** split the same Truth body at existing ATX Markdown headings, then call the
  unchanged current `chunk_truth` inside each section.

Both arms use the same current `mainframe` passage/query formatting and the same
embedder. No generated text or new authority metadata enters the embedding.

## Public synthetic preflight

From a checkout with the semantic profile installed and MiniLM already bootstrapped:

```bash
python research/stage2a-section-boundaries/harness.py \
  --fixture research/stage2a-section-boundaries/fixtures.synthetic.json \
  --validate-only

python research/stage2a-section-boundaries/harness.py \
  --fixture research/stage2a-section-boundaries/fixtures.synthetic.json \
  --output /tmp/stage2a-synthetic.json
```

Do not commit result files until the issue's freeze/order requirements are met.
The first real-model result is evidence even if it is negative.

## Private MainFrame panel

Create a private fixture with exactly the same schema as
`fixtures.synthetic.json`. It must contain parsed Truth text, not mutable paths
to live files, so the fixture bytes can be frozen before scoring.

Private fixture requirements are defined in issue #50. Do not commit private
source text, queries, labels, or result payloads to this public repository.

Run the same harness against the frozen private fixture. Publish only the
sanitized fixture hash, case counts, rank/metric deltas, runtime identity and
terminal disposition.

## Interpretation

The harness emits a per-panel rule result. Issue #50 requires both the public
synthetic panel and the private MainFrame panel to satisfy the preregistered rule
before `SUPPORTED_SECTION_BOUNDARY_REPRESENTATION` is available.

A supported experiment still does not authorize product chunking changes. A
production-shaped implementation would be a separate successor.
