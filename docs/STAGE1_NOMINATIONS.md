# Compact nominations and explicit expansion

Opt in to canonical nominations over the existing ranked results:

```sh
mindgraph query "question" --db index.sqlite --json --envelope --nominations
mindgraph expand-nomination 'exp2:...' --db index.sqlite --json
```

Single-database MCP requires `envelope=true` with `nominations=true`. Shared
MCP accepts `nominations=true` with a registered `scope`. Both expose
`expand_nomination(expansion_handle)`; shared MCP also requires the scope.
CLI callers using an alias can mint with `--nomination-scope ALIAS` and expand
with `--scope ALIAS`. The alias is separate from the stored `index_id`.

Compact mode omits the full `results` and `not_citable` arrays and all
`chunk_text` fields. Citation counts are integers, not source rows. Previews
are exact extracts, flattened and capped at 280 characters, not summaries.
`preview_truncated` reports omitted text; the preview adds no ellipsis or other
characters absent from the stored extract.
Short chunks may fit entirely in the preview. Order, source coordinates,
provenance, citation authority and retrieval reasons survive projection.
Nomination identity is deterministic; freshness stays `UNKNOWN` and source
status is retained separately. The default list and envelopes are unchanged.

An exp2 handle is a transparent locator, not a credential. Expansion checks
the selected index's coherent stored identity, caller scope when supplied,
document, chunk, namespace, path and known content hash before returning the
indexed chunk. Invalid, missing, mixed, contradictory or stale bindings fail
closed without source text. Unknown hashes remain unknown. Legacy exp1
handles require requery and are never reinterpreted.

Shared expansion returns document trust as `trust_profile` and registration
trust as `scope_trust_profile`. Its `scope` and `scope_index` identify the
caller alias; `index_id` identifies the stored index.

Expansion is an explicit caller decision. No automatic expansion or budget
policy is included. This surface does not establish current raw-file bytes,
authentication, truth, retrieval-quality improvements or Conduit integration.

Run the focused regression gate with `pytest -q tests/test_nominations.py
tests/test_expansion_binding.py tests/test_expansion_binding_transports.py
tests/test_stage1_boundaries.py`. The boundary suite starts real CLI, stdio
single-database MCP and private loopback shared-MCP processes, then stops
only the processes it created. It requires the `full` and `dev` extras and
a locally cached MiniLM model; unavailable runtime prerequisites fail visibly.
