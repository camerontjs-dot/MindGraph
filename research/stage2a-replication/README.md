# Stage 2A replication apparatus

Owner: issue #52.

This successor exists because #50's public synthetic panel saturated at rank 1
while its private panel showed a small positive result. It does not reinterpret
#50.

The apparatus enforces the reveal order:

1. validate fixture;
2. run baseline A;
3. freeze the baseline report;
4. candidate B is refused unless the baseline report:
   - matches the same fixture hash;
   - matches the same declared/runtime manifest;
   - satisfies the fixture's preregistered headroom gate.

## Public mechanism panel

The public fixture may be iterated using **baseline mode only** until the issue
#52 public headroom gate is met. Do not execute candidate mode during fixture
construction.

After the gate is met, freeze/commit the public fixture and freeze its baseline
report before the first candidate run.

Example:

```bash
python research/stage2a-replication/harness.py \
  --fixture /path/to/public-fixture.json \
  --mode validate

python research/stage2a-replication/harness.py \
  --fixture /path/to/public-fixture.json \
  --mode baseline \
  --runtime-id 'minilm@CACHE-REVISION/mps:0' \
  --output /tmp/public-baseline.json

python research/stage2a-replication/harness.py \
  --fixture /path/to/frozen-public-fixture.json \
  --mode candidate \
  --runtime-id 'minilm@CACHE-REVISION/mps:0' \
  --baseline-report /tmp/frozen-public-baseline.json \
  --output /tmp/public-candidate.json
```

## Private replication panel

The private fixture is different: it must be frozen **before baseline scoring**.
Do not replace cases after seeing the baseline. If its baseline does not satisfy
the issue #52 private headroom requirement, stop inconclusive and do not run B.

Private source/query/label text stays outside public GitHub.

## Runtime identity

`--runtime-id` is an operator-declared immutable label for the exact cached
model/runtime object observed locally. The harness also records Python, package
versions, model id and model device and refuses candidate mode when that manifest
differs from the frozen baseline report.

The local receipt must separately substantiate the declared cache revision.

## Scope

This is research apparatus only. No MindGraph product source, index, retrieval
ranking, source authority, Conduit integration or release behavior is changed.
