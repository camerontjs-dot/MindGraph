"""Apply the reviewed CLI/test compatibility edits once to exact source blobs.

This is a developer assembly operation, never a test-time repair. It refuses
unexpected input and computes all output before any write. No Git commit,
network, index mutation, qualification, or promotion is performed here.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

BLOBS = {
    "src/mindgraph/cli.py": "560b8d97f8bd666f1a74d2087432949e01ca129d",
    "tests/test_nominations.py": "a787ad1f9c9af630aa7b9490b5c693ab7bca59c1",
}


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"expected one assembly anchor: {old[:90]!r}")
    return text.replace(old, new, 1)


def call_keywords(text: str, name: str, additions) -> str:
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    inserts = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == name:
            suffix = additions(node)
            end = offsets[node.end_lineno - 1] + node.end_col_offset
            # These call sites contain ASCII before the closing parenthesis.
            if text[end - 1] != ")":
                raise ValueError("unexpected UTF-8 call offset")
            end -= 1
            start = offsets[node.lineno - 1] + node.col_offset
            body = text[start:end].rstrip()
            separator = " " if body.endswith((",", "(")) else ", "
            inserts.append((end, separator + suffix))
    if not inserts:
        raise ValueError(f"no call sites for {name}")
    for pos, suffix in sorted(inserts, reverse=True):
        text = text[:pos] + suffix + text[pos:]
    return text


def transform_cli(text: str) -> str:
    start = text.index("def query(\n")
    end = text.index("\n@app.command", start)
    query = text[start:end]
    query = once(query, "    citable_only: bool = typer.Option(", '''    nomination_scope: str | None = typer.Option(
        None, "--nomination-scope",
        help="Explicit caller scope alias for compact nominations; independent of stored index_id.",
    ),
    citable_only: bool = typer.Option(''')
    query = once(query, "    _configure_logging(verbose)", '''    if nomination_scope is not None and (
        not nomination_scope.strip() or not (as_json and envelope and nominations_flag)
    ):
        typer.echo("error: --nomination-scope requires compact nomination mode and a nonblank alias", err=True)
        raise typer.Exit(code=1)
    _configure_logging(verbose)''')
    query = call_keywords(query, "nominations_mod.project_nominations", lambda _n: "scope_index=nomination_scope,")
    text = text[:start] + query + text[end:]
    start = text.index("def expand_nomination(\n")
    end = text.index("\n@app.command", start)
    expansion = text[start:end]
    expansion = once(expansion, "    verbose: bool = typer.Option(False, \"--verbose\", \"-v\"),", '''    scope: str | None = typer.Option(
        None, "--scope", help="Original registered caller alias, when one was supplied at query time.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),''')
    expansion = once(expansion,
        "nominations_mod.resolve_expansion(conn, expansion_handle)",
        "nominations_mod.resolve_expansion(conn, expansion_handle, scope_index=scope)")
    text = text[:start] + expansion + text[end:]
    ast.parse(text)
    return text


def transform_tests(text: str) -> str:
    # Preserve test objectives and source preview assertions. Supply explicit
    # fixture index identity now required by the corrected expansion contract.
    text = once(text, '"index_id": None,', '"index_id": "test-index",')
    text = once(text, '    conn.commit()\n    return conn',
                '    conn.execute("UPDATE documents SET index_id = \'test-index\'")\n    conn.commit()\n    return conn')
    text = once(text, '    cli._ingest_directory(vault, db_path, embedder="minilm")',
                '    cli._ingest_scopes([cli.IngestScope(vault, index_id="test-index", trust_profile="durable_knowledge")], db_path, embedder="minilm")')
    def additions(node):
        doc = next(k.value for k in node.keywords if k.arg == "doc_id")
        path = 'body["path"]' if isinstance(doc, ast.Subscript) else '"d1.md"'
        return f'index_id="test-index", namespace=None, path={path},'
    text = call_keywords(text, "encode_expansion_handle", additions)
    text = once(text, '        "v": EXPANSION_VERSION,\n',
                '        "v": EXPANSION_VERSION,\n        "index_id": "test-index",\n        "namespace": None,\n        "path": "d1.md",\n')
    text = once(text, '.startswith("exp1:")', '.startswith("exp2:")')
    ast.parse(text)
    return text


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    if git(root, "rev-parse", "HEAD") != args.expected_head or git(root, "status", "--porcelain"):
        raise SystemExit("REFUSED: exact clean assembly checkout required")
    outputs = {}
    for rel, expected in BLOBS.items():
        path = root / rel
        if path.is_symlink():
            raise SystemExit("REFUSED: symlink input")
        raw = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if actual != expected:
            raise SystemExit(f"REFUSED: source blob changed: {rel}")
        text = raw.decode("utf-8")
        result = transform_cli(text) if rel.endswith("cli.py") else transform_tests(text)
        outputs[rel] = result.encode("utf-8")
    receipt = {"operation": "assembly_not_qualification", "input_head": args.expected_head,
               "written": args.write, "files": {p: hashlib.sha256(b).hexdigest() for p, b in outputs.items()}}
    if args.write:
        for rel, raw in outputs.items():
            (root / rel).write_bytes(raw)
    print(json.dumps(receipt, sort_keys=True, indent=2))

if __name__ == "__main__":
    main()
