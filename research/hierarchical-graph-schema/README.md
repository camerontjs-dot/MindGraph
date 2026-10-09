# Hierarchical graph schema research: MainFrame project views

**Research owner:** [MindGraph issue #58](https://github.com/camerontjs-dot/MindGraph/issues/58)  
**Result:** bounded *structural* schema proposed in [SCHEMA.md](SCHEMA.md), with the exact run and failures in [RESULTS.md](RESULTS.md). **No product/schema migration or graph-specific utility claim.**

This directory contains a reproducible, read-only, standard-library prototype of per-project code/file graphs, a lightweight global directory catalogue, optional previous frozen Knowledge and Operations index projections, source-bound graph edge witnesses and controls.

The [preregistered questions](PREREGISTRATION.md), [code navigation cases](CASES.json) and [adversarial authority fixture](FIXTURE.json) were frozen on GitHub before the first candidate execution. **Do not edit the historical frozen expectations to repair a failed candidate.** Prior RC0/RC1 and public-portability failures are retained in the source experiment ledger.

## Profiles and API boundaries

- **Project code:** exact tracked files at `repo@full_git_sha`; directory containment, top-level Python declarations and resolvable static imports, with Git blob/source-line witnesses. A Swift source file is a navigable tracked file but no Swift symbol/call facts are inferred.
- **Knowledge:** an optional separately frozen index projection. Historical documents/links are treated as unverified retrieval nominations, not attested knowledge or current state.
- **Operations:** same versioned source/index binding concepts; an optional frozen operations corpus projection is intentionally not equivalent to a current-incident/decision schema.
- **Evidence Room:** canonical evidence/decision/supersession owner; this research does not reimplement its warehouse or expose its private records.
- **Directory catalogue:** a content-hash-bound reference to each view, not a cross-scope semantic assertion. Missing private views are marked unavailable.

Portable script and [JSON Schema 2020-12 envelope](CONTRACT.schema.json) check machine shape and source binding. **A valid JSON document alone is not a verified source:** `attest.py` must also read the exact Git tree/blob and AST source to establish the code relationships claimed. Cross-scope path traversal requires a separately justified scope grant and complete path witness.

## Public-only reproduction

Run from a checkout of this research branch, with your own independently cloned or locally cached public MindGraph and Conduit Git object repositories. Both exact commits must be locally available. No local worktree checkout/change is required because the script uses `git show` on immutable commit objects.

```bash
MG=/absolute/path/to/MindGraph
CO=/absolute/path/to/Conduit
RESEARCH=research/hierarchical-graph-schema
OUT="$(mktemp -d)"

python3 "$RESEARCH/projection.py" \
  --mindgraph-repo "$MG" --conduit-repo "$CO" \
  --cases "$RESEARCH/CASES.json" --out "$OUT/projected"

python3 "$RESEARCH/evaluate.py" \
  --projection "$OUT/projected" \
  --cases "$RESEARCH/CASES.json" --fixture "$RESEARCH/FIXTURE.json" \
  --mindgraph-repo "$MG" --conduit-repo "$CO" \
  --output "$OUT/cases.json"

python3 "$RESEARCH/attest.py" \
  --projection "$OUT/projected" --cases "$RESEARCH/CASES.json" \
  --mindgraph-repo "$MG" --conduit-repo "$CO" \
  --output "$OUT/attestation.json"

MGRAPH_MINDGRAPH_REPO="$MG" MGRAPH_CONDUIT_REPO="$CO" \
MGRAPH_PROJECTION_DIR="$OUT/projected" \
MGRAPH_CASES_FILE="$RESEARCH/CASES.json" \
MGRAPH_PRESSURE_OUTPUT="$OUT/pressure.json" \
  python3 "$RESEARCH/pressure.py"

python3 "$RESEARCH/catalogue.py" \
  --projection "$OUT/projected" \
  --manifest "$OUT/projected/manifest.json" \
  --cases "$RESEARCH/CASES.json" \
  --output "$OUT/catalogue.json"
```

Expected public-only receipts: both project source attestations `PASS`, 7/7 source navigation / 7/7 direct Git / 7/7 recursive SQLite parity, 7/7 synthetic path tests, 6/6 named negative cases, and 11/11 source-custody pressure controls. Knowledge/Operations are `NOT_RUN_UNAVAILABLE` in the attestor; the associated extra pressure check is also correctly `NOT_RUN_UNAVAILABLE`. The directory catalogue has only the two project views. **Never count a missing private test as passing.**

The code uses only Python standard-library modules and the Git CLI; JSON Schema validation separately used `jsonschema` 4.25.1 in the recorded research environment, but `jsonschema` is not necessary to run the baseline scripts.

## Optional private MainFrame reproduction

To reproduce the full four-view experiment, supply **authorized, frozen, transaction-consistent SQLite backups**, and their externally frozen SHA-256 identifiers:

```bash
python3 "$RESEARCH/projection.py" \
  --mindgraph-repo "$MG" --conduit-repo "$CO" \
  --cases "$RESEARCH/CASES.json" --out "$OUT/full" \
  --knowledge-snapshot PRIVATE_KNOWLEDGE_SNAPSHOT.sqlite \
  --knowledge-sha256 PREDECLARED_KNOWLEDGE_SNAPSHOT_SHA256 \
  --operations-snapshot PRIVATE_OPERATIONS_SNAPSHOT.sqlite \
  --operations-sha256 PREDECLARED_OPERATIONS_SNAPSHOT_SHA256
```

Use the same evaluate, pressure, attestor and catalogue commands, replacing the projection directory and supplying the two snapshot/hash argument pairs to `attest.py`. The private bytes, source paths, raw graph outputs and any private cross-project source-reference trace **stay in the authorized local evidence location**, not this public repository. The full run produced a 4-view catalogue and 12/12 pressure controls with its own exact receipt, not a public reproduction of private graph contents.

For fresh source identities, create a distinct successor run and record the difference; never silently overwrite historical raw artifacts. The scripts refuse overwriting their output destination.

## Stronger than a filename graph, weaker than a qualified MainFrame ontology

The built graphs are *syntax and filesystem observations* with exact version/source ancestry, not evidence that code works or a feature is approved. We intentionally did not infer `TESTED_BY`, `DEPENDS_ON`, `CALLS`, `QUALIFIED_BY` or `SUPERSEDES` from similar names or imports. Conduit Swift symbols, whole-MainFrame source recrawl and production cross-scope rights are unqualified. The Knowledge and Operations graphs are legacy snapshot views only, with 691 and 13 existing unresolved target IDs withheld respectively.

The strongest flat `git ls-tree`/source lookup control and equivalent recursive SQLite graph traversal matched every predeclared real navigation case. The **additional utility of a graph layer over simpler methods was not demonstrated**. Separate task-oracle and isolated-agent evidence will be needed to justify broader implementation or a physical graph database.

Relevant prior evidence: [MindGraph graph census #56](https://github.com/camerontjs-dot/MindGraph/issues/56), [retrieval comparison #54](https://github.com/camerontjs-dot/MindGraph/issues/54), [Evidence Room GN0 #71](https://github.com/camerontjs-dot/the-evidence-room/issues/71), [promoted Evidence Room warehouse #46](https://github.com/camerontjs-dot/the-evidence-room/pull/46). The architecture is compatible with source provenance principles in [W3C PROV-O](https://www.w3.org/TR/prov-o/), code navigation ideas in [SCIP](https://github.com/sourcegraph/scip), and [SQLite recursive common table expressions](https://www.sqlite.org/lang_with.html), but has not adopted their full ontologies/formats.
