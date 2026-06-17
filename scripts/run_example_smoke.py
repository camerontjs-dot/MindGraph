"""Regenerates the asset README's "Try it" captures.

Runs the handoff prompt's smoke sequence against the committed
examples/example-vault/ vault, using the real MiniLM model. Prints fenced
Markdown blocks formatted for direct paste into the asset README's "Try it"
section.

First-run latency note: the first ingest downloads the MiniLM model on a fresh
machine; the script keeps the `Loading embedding model (all-MiniLM-L6-v2)...`
log line on the first block so the cost is visible. Subsequent blocks filter
the log line to reduce noise.

Usage (from the asset root):

    .venv/bin/python scripts/run_example_smoke.py > /tmp/captures.md

Inspect `/tmp/captures.md` and paste blocks into the README's "Try it" section.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from mindgraph import parser

ASSET_ROOT = Path(__file__).resolve().parent.parent
VAULT_PATH = ASSET_ROOT / "examples" / "example-vault"
DB_DIR = Path("/tmp/mindgraph-example")
DB_PATH = DB_DIR / "db.sqlite"
DB_PATH_STR = str(DB_PATH)


def reset_db_dir() -> None:
    if DB_DIR.exists():
        shutil.rmtree(DB_DIR)
    DB_DIR.mkdir(parents=True)


_NOISE_SUBSTRINGS = (
    "HTTP Request:",
    "huggingface_hub.utils._http",
    "sentence_transformers.base.model",
    "Loading weights:",
    "Batches:",
    "Warning: You are sending unauthenticated",
    "transformers_modules",
)


def _filter_noise(stderr: str, *, keep_loading_log: bool) -> str:
    """Drop HuggingFace and sentence-transformers chatter.

    Keeps the canonical `Loading embedding model (all-MiniLM-L6-v2)...` line
    when `keep_loading_log` is True so first-run latency stays visible in the
    initial capture block.
    """
    kept: list[str] = []
    for line in stderr.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "Loading embedding model" in stripped:
            if keep_loading_log:
                kept.append(line)
            continue
        if any(noise in stripped for noise in _NOISE_SUBSTRINGS):
            continue
        kept.append(line)
    return "\n".join(kept)


def run(cmd: list[str], *, keep_loading_log: bool = False) -> tuple[str, str]:
    """Run a subprocess and capture stdout + stderr.

    Filters HuggingFace and sentence-transformers chatter from stderr so the
    README captures stay readable. Keeps the canonical model-loading log line
    when `keep_loading_log` is True.
    """
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = proc.stdout
    stderr = _filter_noise(proc.stderr, keep_loading_log=keep_loading_log)
    if proc.returncode != 0:
        print(
            f"WARNING: command exited with code {proc.returncode}: "
            f"{' '.join(cmd)}",
            file=sys.stderr,
        )
    return stdout, stderr


def emit_block(
    heading: str, display_cmd: list[str], stdout: str, stderr: str
) -> None:
    """Print a README-ready subsection: heading + fenced code block."""
    print(f"### {heading}")
    print()
    print("```")
    print("$ " + " ".join(display_cmd))
    if stderr.strip():
        print(stderr.rstrip())
    if stdout.strip():
        print(stdout.rstrip())
    print("```")
    print()


def main() -> None:
    reset_db_dir()
    mindgraph_bin = str(ASSET_ROOT / ".venv" / "bin" / "mindgraph")

    # 1. init
    out, err = run([mindgraph_bin, "init", "--db", DB_PATH_STR])
    emit_block(
        "Initialize the database",
        ["mindgraph", "init", "--db", DB_PATH_STR],
        out,
        err,
    )

    # 2. ingest — keep the loading log on the first model use for honesty
    out, err = run(
        [mindgraph_bin, "ingest", str(VAULT_PATH), "--db", DB_PATH_STR],
        keep_loading_log=True,
    )
    emit_block(
        "Ingest the example vault",
        [
            "mindgraph",
            "ingest",
            "examples/example-vault",
            "--db",
            DB_PATH_STR,
        ],
        out,
        err,
    )

    # 3. lexical-heavy query (unique keyword 'antinet' in mental-models-overview)
    out, err = run(
        [
            mindgraph_bin,
            "query",
            "antinet",
            "--db",
            DB_PATH_STR,
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
            DB_PATH_STR,
            "--top-k",
            "3",
        ],
        out,
        err,
    )

    # 4. concept query (paraphrase-friendly term)
    out, err = run(
        [
            mindgraph_bin,
            "query",
            "satisficing",
            "--db",
            DB_PATH_STR,
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
            DB_PATH_STR,
            "--top-k",
            "3",
        ],
        out,
        err,
    )

    # 5. fused query
    out, err = run(
        [
            mindgraph_bin,
            "query",
            "balancing feedback",
            "--db",
            DB_PATH_STR,
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
            DB_PATH_STR,
            "--top-k",
            "3",
        ],
        out,
        err,
    )

    # 6. expansion query — walk two hops from the seed
    out, err = run(
        [
            mindgraph_bin,
            "query",
            "feedback loops",
            "--db",
            DB_PATH_STR,
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
            DB_PATH_STR,
            "--top-k",
            "1",
            "--expand",
            "--depth",
            "2",
        ],
        out,
        err,
    )

    # 7. neighbors lookup — surfaces the dangling unicycle-mental-model target
    archetypes_id = parser.compute_doc_id("systems-archetypes.md")
    out, err = run(
        [mindgraph_bin, "neighbors", archetypes_id, "--db", DB_PATH_STR]
    )
    emit_block(
        "Inspect outbound edges and surface a dangling target",
        ["mindgraph", "neighbors", archetypes_id, "--db", DB_PATH_STR],
        out,
        err,
    )


if __name__ == "__main__":
    main()
