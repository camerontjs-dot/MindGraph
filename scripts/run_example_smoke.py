"""Regenerates the asset README's "Try it" captures.

Runs the handoff prompt's smoke sequence against the committed
examples/example-vault/ vault, using the real MiniLM model. Prints fenced
Markdown blocks formatted for direct paste into the asset README's "Try it"
section.

Model acquisition is explicit: the script runs `mindgraph bootstrap-model`
before creating the index. Normal ingest/query then exercise the cache-only
runtime contract.

Usage (from the asset root):

    .venv/bin/python scripts/run_example_smoke.py > /tmp/captures.md

Inspect `/tmp/captures.md` and paste blocks into the README's "Try it" section.
"""

import shlex
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from mindgraph import parser

ASSET_ROOT = Path(__file__).resolve().parent.parent
VAULT_PATH = ASSET_ROOT / "examples" / "example-vault"
_NOISE_SUBSTRINGS = (
    "HTTP Request:",
    "huggingface_hub.utils._http",
    "sentence_transformers.base.model",
    "Loading weights:",
    "Batches:",
    "Warning: You are sending unauthenticated",
    "transformers_modules",
)


def _filter_noise(stderr: str) -> str:
    """Drop HuggingFace and sentence-transformers chatter.

    Model acquisition is represented by the explicit bootstrap block, so
    loader chatter is not needed in later captures.
    """
    kept: list[str] = []
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "Loading embedding model" in stripped:
            continue
        if any(noise in stripped for noise in _NOISE_SUBSTRINGS):
            continue
        kept.append(line)
    return "\n".join(kept)


def run(cmd: list[str]) -> tuple[str, str]:
    """Run a subprocess and capture stdout + stderr.

    Filters HuggingFace and sentence-transformers chatter from stderr so the
    README captures stay readable.
    """
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = proc.stdout
    stderr = _filter_noise(proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command exited with code {proc.returncode}: {' '.join(cmd)}\n"
            f"{stderr}"
        )
    return stdout, stderr


def emit_block(
    heading: str, display_cmd: list[str], stdout: str, stderr: str
) -> None:
    """Print a README-ready subsection: heading + fenced code block."""
    print(f"### {heading}")
    print()
    print("```")
    print("$ " + shlex.join(display_cmd))
    if stderr.strip():
        print(stderr.rstrip())
    if stdout.strip():
        print(stdout.rstrip())
    print("```")
    print()


def generate_captures(db_path: str) -> None:
    mindgraph_bin = str(ASSET_ROOT / ".venv" / "bin" / "mindgraph")

    # 1. explicit model acquisition
    out, err = run([mindgraph_bin, "bootstrap-model", "--embedder", "minilm"])
    emit_block(
        "Bootstrap the semantic model",
        ["mindgraph", "bootstrap-model", "--embedder", "minilm"],
        out,
        err,
    )

    # 2. init
    out, err = run([mindgraph_bin, "init", "--db", db_path])
    emit_block(
        "Initialize the database",
        ["mindgraph", "init", "--db", db_path],
        out,
        err,
    )

    # 3. ingest — normal runtime is cache-only after bootstrap
    out, err = run(
        [mindgraph_bin, "ingest", str(VAULT_PATH), "--db", db_path]
    )
    emit_block(
        "Ingest the example vault",
        [
            "mindgraph",
            "ingest",
            "examples/example-vault",
            "--db",
            db_path,
        ],
        out,
        err,
    )

    # 4. lexical-heavy query (unique keyword 'antinet' in mental-models-overview)
    out, err = run(
        [
            mindgraph_bin,
            "query",
            "antinet",
            "--db",
            db_path,
            "--top-k",
            "3",
        ]
    )
    emit_block(
        "Query a unique keyword",
        [
            "mindgraph",
            "query",
            "antinet",
            "--db",
            db_path,
            "--top-k",
            "3",
        ],
        out,
        err,
    )

    # 5. concept query (paraphrase-friendly term)
    out, err = run(
        [
            mindgraph_bin,
            "query",
            "satisficing",
            "--db",
            db_path,
            "--top-k",
            "3",
        ]
    )
    emit_block(
        "Query a concept term",
        [
            "mindgraph",
            "query",
            "satisficing",
            "--db",
            db_path,
            "--top-k",
            "3",
        ],
        out,
        err,
    )

    # 6. fused query
    out, err = run(
        [
            mindgraph_bin,
            "query",
            "balancing feedback",
            "--db",
            db_path,
            "--top-k",
            "3",
        ]
    )
    emit_block(
        "Query that fuses lexical and semantic signals",
        [
            "mindgraph",
            "query",
            "balancing feedback",
            "--db",
            db_path,
            "--top-k",
            "3",
        ],
        out,
        err,
    )

    # 7. expansion query — walk two hops from the seed
    out, err = run(
        [
            mindgraph_bin,
            "query",
            "feedback loops",
            "--db",
            db_path,
            "--top-k",
            "1",
            "--expand",
            "--depth",
            "2",
        ]
    )
    emit_block(
        "Walk the graph from a seed",
        [
            "mindgraph",
            "query",
            "feedback loops",
            "--db",
            db_path,
            "--top-k",
            "1",
            "--expand",
            "--depth",
            "2",
        ],
        out,
        err,
    )

    # 8. neighbors lookup — surfaces the dangling unicycle-mental-model target
    archetypes_id = parser.compute_doc_id("systems-archetypes.md")
    out, err = run(
        [mindgraph_bin, "neighbors", archetypes_id, "--db", db_path]
    )
    emit_block(
        "Inspect outbound edges and surface a dangling target",
        ["mindgraph", "neighbors", archetypes_id, "--db", db_path],
        out,
        err,
    )


if __name__ == "__main__":
    with TemporaryDirectory(prefix="mindgraph-example-") as trial_dir:
        generate_captures(str(Path(trial_dir) / "db.sqlite"))
