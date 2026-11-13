# Contributing to MindGraph

Try [searching seven example notes](README.md#try-mindgraph-on-seven-notes), then [tell us how it went](https://github.com/camerontjs-dot/MindGraph/issues/new?template=search-report.md). A query that found the right note, one that returned plausible but unhelpful notes, or the command that stopped you are all worth sharing.

## Working together

If you disagree, point to the query, passage or test result that would change your conclusion. Personal attacks and harassment are unwelcome. You can [report abuse or spam to GitHub](https://docs.github.com/en/communities/maintaining-your-safety-on-github/reporting-abuse-or-spam).

Link the original public search report when you turn someone else’s case into a test, and preserve any required credits on shared notes or code. Ask before quoting a private report or naming its author. Share material you have permission to redistribute under the [project’s MIT license](LICENSE).

## Share a search

Give the query and the paths you would have opened. If several notes would answer the question, name them and say what each contains. Include the first few paths MindGraph returned in order, or say that it returned no rows. If the search never ran, the command and a short error excerpt are enough.

Mention whether you used the bundled vault or your own notes, whether you used lexical-only or semantic search, and any flags that affected the result, such as `--expand`. For setup problems, the operating system, Python version and checkout (`git rev-parse --short HEAD`) help reproduce what you saw. Use relative paths and shareable note text. A private vault or SQLite database can stay on your machine.

## Suggest a retrieval case

[Describe a case](https://github.com/camerontjs-dot/MindGraph/issues/new?template=retrieval-case.md) with a query, one or more acceptable paths, and the passage or link that makes each worth opening. It is fine to expect no useful match. For the bundled vault, for example, searching `antinet` should lead to `mental-models-overview.md` because that note describes the Antinet method.

For a case that needs different notes, include small Markdown excerpts labelled with their relative filenames. Plain Markdown works. For a link traversal case, show both the source and target note, the literal link and its direction. The [feedback-loops example](examples/example-vault/feedback-loops.md) links to `[[balancing-loops]] (illustrates)`. If the target is deliberately missing, say so. A similar-looking note that should be skipped can make a ranking problem much easier to reproduce.

If you want to start with a small coding task, [add a model-free CLI regression for the seven-note walkthrough](https://github.com/camerontjs-dot/MindGraph/issues/65).

## Turn a case into a pull request

For a query over the seven existing notes, [the example tests](tests/test_examples.py) show how to assert a returned path or a link. For different notes, [the query tests](tests/test_query.py) show how to create small Markdown files in pytest's temporary directory and ingest them into a fresh database. State which behavior you expect before changing retrieval code. If several paths are acceptable, assert the condition the use case actually needs, such as finding at least one in the first three results. The keyword embedder in these tests is deterministic. To report what a real embedding model retrieved, give its name and the actual results separately.

From the repository root, run the focused checks in a Python 3.10+ environment. These commands use a macOS or Linux shell.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q tests/test_examples.py tests/test_query.py
```

The full suite also collects MCP tests. Add the MCP dependencies in the same environment and run:

```bash
.venv/bin/python -m pip install -e '.[dev,mcp]'
.venv/bin/python -m pytest -q
```

Open a PR against `main` with the query, expected and observed behavior, and the checks you ran. For a fix, include the test that demonstrated the original failure. Describe where your expectation comes from so a reviewer can evaluate the case before it becomes a regression test.
