# Which note would you keep?

One query has a distinctive keyword. The other describes a concept that two notes explain. This example searches the same [seven Markdown files](../example-vault/) with MindGraph's lexical search and [BlackBelt's Pi Dashboard KB](https://github.com/BlackBeltTechnology/pi-agent-dashboard/tree/e78b4c2c0474a8ab04941e76942e5a5b9d92c2d6/packages/kb). The [two-query fixture](golden.json) uses the format accepted by BlackBelt's `kb eval`.

## Run the example

From the MindGraph repository root, use a macOS or Linux shell with Python 3.10+, Node.js 22.19+ and npm. The commands install both CLIs locally and index only the copied notes. In Pi Dashboard KB, use the default ranking settings for this comparison.

```bash
trial=$(mktemp -d)
cp -R examples/example-vault "$trial/vault"
cp examples/blackbelt-comparison/golden.json "$trial/golden.json"
python3 -m venv "$trial/venv"
"$trial/venv/bin/python" -m pip install .
npm install --prefix "$trial/tools" --ignore-scripts --no-audit --no-fund @blackbelt-technology/pi-dashboard-kb@0.11.0

(
  cd "$trial" || exit 1
  mg=./venv/bin/mindgraph
  kb=./tools/node_modules/.bin/kb
  "$mg" ingest vault --db mindgraph.sqlite --lexical-only
  "$kb" init --source vault
  "$kb" index --source vault --db blackbelt.sqlite
  "$kb" eval --source vault --db blackbelt.sqlite --golden golden.json --limit 5 --json
  for query in "antinet" "what brings a system back toward its target"
  do
    "$mg" query "$query" --db mindgraph.sqlite --lexical-only --top-k 5
    "$kb" search "$query" --source vault --db blackbelt.sqlite --limit 5 --json
  done
)
```

## Read the results

Observed with MindGraph at [`8df9ae7`](https://github.com/camerontjs-dot/MindGraph/commit/8df9ae7fccdb742558950ef9bdedb96cc74df6d0) and Pi Dashboard KB 0.11.0.

| Query | Expected file in the fixture | MindGraph rank | Pi Dashboard KB rank |
|---|---|---:|---:|
| `antinet` | `mental-models-overview.md` | 1 | 1 |
| `what brings a system back toward its target` | `balancing-loops.md` | 3 | 2 |

Both tools put [feedback-loops.md](../example-vault/feedback-loops.md) first for the second query. It explains that balancing loops push a system back toward a setpoint. The [dedicated note](../example-vault/balancing-loops.md) also explains how a delay can produce oscillation. If you want the name of the mechanism, either note answers the question. If you want the explanation of oscillation, the dedicated note answers it more fully. The fixture scores only the dedicated path.

To [contribute a search example](../../CONTRIBUTING.md), send a question in your own words, the file you hoped to find, and any other file you would accept. One sentence about why each file qualifies helps a reviewer distinguish a ranking problem from a second good answer.
