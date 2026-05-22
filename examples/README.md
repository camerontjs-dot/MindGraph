# MindGraph example vault

A seven-file Markdown vault under `example-vault/` that exercises every retrieval path MindGraph exposes: lexical-only matches, semantic-only matches, fused matches, dangling graph edges, and graph expansion from a seed document. The walkthrough that ingests this vault and shows the captured output for each path lives in the asset README's "Try it" section.

The CI-safe smoke test under `../tests/test_examples.py` asserts the expected retrieval surface against this same vault using the deterministic `KeywordEmbedder` stub. The real-model captures in the README come from `../scripts/run_example_smoke.py`, which uses `sentence-transformers/all-MiniLM-L6-v2`.
