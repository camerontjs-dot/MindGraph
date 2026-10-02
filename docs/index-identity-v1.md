# Producer-bound index identity v1

`mindgraph-index-identity/v1` records a corpus producer's explicit authority in
`index_meta`, under the key `index_identity`. `init` does not create it. Queries
never derive it from filenames, caller aliases or returned documents.

The producer passes a JSON declaration to `bind-index --db DB --identity-file FILE`:

```json
{
  "schema_version": "mindgraph-index-identity/v1",
  "index_id": "mainframe-operations",
  "trust_profile": "operations_status",
  "retrieval_scope": "operations",
  "lifecycle_root": "40_operations",
  "producer": "mainframe-live",
  "manifest_sha256": "<64 lowercase hexadecimal characters>",
  "source_document_map_sha256": "<64 lowercase hexadecimal characters>"
}
```

The engine validates all stored document index/trust/path provenance, adds
`document_count` and `database_document_map_sha256`, and writes once in a
writer transaction. A different or stale existing binding is rejected.
The canonical database map includes each document's id, index, trust, namespace,
source root/path, display path, stored path and content hash, sorted by id, encoded
as compact sorted-key JSON. No query result is used to establish this binding.

The source-document map uses the same stored fields except `path`, ordered by
`(namespace, source_path)` and encoded as compact sorted-key UTF-8 JSON.
`source_document_map_sha256` must equal the SHA-256 of that projection. The
producer checks selected source bytes against this map before binding; the
engine independently recomputes it at bind time and on every identity read.
A valid-format source-map hash from another index is rejected even when the
query returns no rows. This check adds no response fields or schema version.
Existing declarations with arbitrary source-map hashes must be rebuilt through
their producer; queries never repair or rebind them.

`index-identity --db DB` reads and validates the binding without mutation.
`query QUESTION --db DB --json --identity-envelope` returns:

```json
{
  "schema_version": "mindgraph-query-identity/v1",
  "database_identity": {"schema_version": "mindgraph-index-identity/v1"},
  "results": []
}
```

The abbreviated example omits the required stored fields. The actual identity
is complete. A read transaction covers identity, current document-map validation
and query execution. Missing, malformed, stale or contradictory identity fails
before returning nominations. Zero results remain legitimate for an identified
index. Results retain the complete legacy row fields and citation classes.
This envelope is separate from the existing `--envelope` intent contract; the
two flags cannot be combined. Legacy arrays and intent envelopes are unchanged.

Shared MCP `query` accepts optional `identity_envelope=true`. The selected alias
and configured trust must match the stored binding. `serve-daemon` and
`daemon-start` can require identity for one configured alias using
`--require-scope-identity NAME=INDEX_ID:LIFECYCLE_ROOT`. This also checks the
stored alias/trust, validates before startup, and forces the identity envelope
on that scope's queries. Neighbor calls validate required scope identity too.
Unconfigured/unknown scopes fail; there is no fallback.

MainFrame's explicit three-scope configuration requires the operations binding.
Its producer stamps only after direct manifest/source-map verification, then
records and rechecks that exact binding during promotion. Conduit requires the
v1 envelope for operations in both Session API and Query Station consumption.

Database identity authorizes index selection. It does not verify a source, make
a citation admissible, establish freshness, enter a ContextSet, expand files,
deliver context or grant write authority. The hash detects stale or substituted
document maps; it is not authentication against a malicious authorized writer.
