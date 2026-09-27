# Index-bound expansion repair

Status: implementation kit; CLI/test assembly pending. Not independently qualified.

Owner: #28. Predecessor: blocked PR #23 at
`5c25f41c1954e1515f6c35e92943aa889556778d`.
Consumer repair: camerontjs-dot/Conduit#88.

The predecessor collision and its BLOCK disposition remain unchanged:
https://github.com/camerontjs-dot/MindGraph/pull/23#issuecomment-5851517360

## Implemented source

`src/mindgraph/expansion_binding.py` provides an explicit exp2 locator with
separate caller scope alias and stored index identity. The selected database's
identity is read from coherent, nonempty stored document declarations; an
optional index_meta declaration must agree. Mixed, missing, or contradictory
identity cannot qualify index-bound expansion.

`src/mindgraph/nominations.py` now uses that validator and one read snapshot
through index/target validation, chunk read, and metadata resolution. It rejects
cross-index collisions even when the document identifier, chunk index, content
hash, and trust profile collide. Namespace and source path also remain bound.
A known hash cannot silently become an unknown successful match.

The nom1 identity algorithm, exact 280-character preview, rank, query result
order, and provenance projection are retained. Explicitly changing a query's
scope alias is a different nomination identity input, not an identical query.
There is no retrieval model, ranking, chunking or benchmark-label change.

## Compatibility decision

exp2 carries scope, index_id, namespace, path, doc_id, chunk_index and
content_hash as distinct fields. It uses canonical sorted compact UTF-8 JSON
inside padded base64url. Unsupported/duplicate fields, malformed encoding,
ambiguous numeric types and oversized handles are rejected.

exp1 handles must be requeried. They are not silently reinterpreted because
their overloaded scope field cannot establish the corrected independent index
binding. A compact nomination can preserve null index identity, but such a
handle cannot be redeemed until a coherently identified index is available.
Legacy query calls that do not opt into nominations keep their existing shapes.

Handles remain unauthenticated locators. This repair checks logical index/source
identity in the selected database. It does not authenticate storage, verify
claims, infer source currentness, or establish writer-byte custody. A corrupted
producer that falsifies every stored identity is outside this claim.

## Pending mechanical assembly

The repository connector could not safely apply the remaining large-file edits
in this development pass. The exact reviewed changes are supplied in
`tools/assemble_expansion_repair.py`, not left as a request to redesign them.

The script changes only:

- `src/mindgraph/cli.py`: adds `--nomination-scope` for compact queries and
  `--scope` for explicit expansion;
- `tests/test_nominations.py`: supplies explicit fixture index identity and
  updates version-specific expectations, preserving the earlier test purposes.

It requires a clean checkout at an explicitly supplied expected head and checks
the original Git blob identity for every input. It computes all outputs before
writing. A guard failure is an assembly BLOCK, not permission to bypass the
check, adapt anchors, or alter expected source identities.

From the exact kit checkout, with receipts outside the worktree:

```sh
python tools/assemble_expansion_repair.py --expected-head "$KIT_SHA"
python tools/assemble_expansion_repair.py --expected-head "$KIT_SHA" --write
```

Inspect the bounded diff, verify dry-run/write postimage hashes, then commit the
mechanical result on the successor branch. Record that child commit and tree.
Qualification starts in a new clean detached checkout of the child, not in the
assembly worktree. The kit head itself is not the integrated candidate.

## Tests and evidence boundary

Developer helper checks in this pass:

- `tests/test_expansion_binding.py`: 29 passed on Linux using the proposed
  production helper, covering collision, same-trust/different-index, missing or
  mixed index identity, conflicting metadata, path/namespace, known/unknown hash,
  scope aliases, strict parser and read-only transaction behavior.
- Python syntax compilation passed for the supplied Python modules and tools.
- Call-site assembly syntax was checked on three reduced syntax fixtures.

The helper's tested SHA-256 is
`dc97fe375ad8fb9cfc13fb289a2ac82d4d377414411616b8fd3b2ff216399a4b`.
Its Git blob is `fb4499f017f24af33c80f307a16f7c65cfa2f4dc`.
These are developer checks, not independent qualification or full-repository
results. The assembly script has not been executed against the full predecessor
checkout in this pass.

`tests/test_expansion_binding_transports.py` adds real subprocess CLI,
single-database MCP and shared-MCP collision controls. Those tests are authored
but unrun here. After assembly, run them, the retained nomination tests and the
full repository suite. Preserve errors and legacy compatibility checks.

`tools/prepare_repair88_fixture.py` creates a new-only synthetic isolated home
for the consumer's opt-in populated-model test. Its owned wrapper normally calls
the exact candidate CLI with lexical-only querying; a separately documented
marker selects a malformed expansion response for a negative control. This is
not a model benchmark or a substitute for an installed Conduit acceptance run.

## Qualification gate

Freeze one assembled MindGraph/Conduit pair. Reproduce the original collision,
plus same-trust/different-index, missing identity, contradictory metadata,
wrong-scope, wrong-response-handle, stale-hash and positive exact-source cases.
Conduit must independently reject a defective producer response rather than
rely only on the repaired producer.

The local qualifier must also verify live tool discovery, the complete Conduit
product gate and populated-context non-admission. No experiment #25-#27, merge,
release or promotion is authorized by this kit or its helper test results.
