import json
import logging
import os
from pathlib import Path

import typer

from mindgraph import db, mcp_server, parser
from mindgraph import query as query_mod
from mindgraph.exceptions import MindgraphError

app = typer.Typer(
    name="mindgraph",
    help="A Graph-Augmented Personal Knowledge Engine",
    add_completion=False,
)

logger = logging.getLogger("mindgraph")


_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "huggingface_hub",
    "huggingface_hub.utils._http",
    "sentence_transformers",
    "sentence_transformers.base.model",
    "transformers",
)


def _configure_logging(verbose: bool) -> None:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for noisy_logger in _NOISY_LOGGERS:
        logging.getLogger(noisy_logger).setLevel(
            logging.WARNING if verbose else logging.ERROR
        )


def _load_embedder():
    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model (all-MiniLM-L6-v2)...")
    return SentenceTransformer("all-MiniLM-L6-v2")


def _encode_without_progress(embedder, texts):
    """Encode text while suppressing sentence-transformers progress output."""
    try:
        return embedder.encode(
            texts, convert_to_numpy=True, show_progress_bar=False
        )
    except TypeError:
        return embedder.encode(texts, convert_to_numpy=True)


def _ingest_directory(directory: Path, db_path: str) -> dict[str, int]:
    stats = {"total": 0, "ingested": 0, "skipped": 0, "pruned": 0, "failed": 0}
    md_files = sorted(directory.rglob("*.md"))
    stats["total"] = len(md_files)

    if not md_files:
        logger.warning("No markdown files found under %s", directory)
        return stats

    conn = db.get_db(db_path)
    model = None
    parsed_docs: list[tuple[Path, parser.ParsedDocument]] = []

    try:
        for md_file in md_files:
            relative_path = str(md_file.relative_to(directory))
            try:
                body_bytes = md_file.read_bytes()
                parsed_docs.append(
                    (md_file, parser.parse_document(relative_path, body_bytes))
                )
            except MindgraphError as e:
                logger.error("failed: %s — %s", relative_path, e)
                stats["failed"] += 1
            except Exception:
                logger.exception("unexpected failure: %s", relative_path)
                stats["failed"] += 1

        link_resolver = parser.LinkResolver.from_documents(
            parsed for _, parsed in parsed_docs
        )

        for md_file, parsed in parsed_docs:
            relative_path = parsed.path
            try:
                edges = parser.extract_graph_edges(
                    parsed.truth_text,
                    parsed.id,
                    link_resolver=link_resolver,
                    source_path=parsed.path,
                )

                existing_hash = db.get_document_hash(conn, parsed.id)
                if existing_hash == parsed.content_hash:
                    logger.debug("skipped (unchanged): %s", relative_path)
                    # Re-resolve edges (a newly-added note can resolve a target
                    # that was previously dangling), but only write when the
                    # resolved edge set actually differs from what is stored.
                    # This keeps the common no-op refresh read-only instead of
                    # running a DELETE+INSERT transaction per unchanged document.
                    new_edge_keys = {
                        (e.target_id, e.relationship_type) for e in edges
                    }
                    if new_edge_keys != db.get_outbound_edge_keys(conn, parsed.id):
                        with conn:
                            db.replace_edges(conn, parsed.id, edges)
                    stats["skipped"] += 1
                    continue

                chunks = parser.chunk_truth(parsed.truth_text)
                embeddings: list[list[float]] = []
                if chunks:
                    if model is None:
                        model = _load_embedder()
                    raw = _encode_without_progress(model, chunks)
                    embeddings = [row.tolist() for row in raw]

                with conn:
                    db.upsert_document(conn, parsed)
                    db.insert_chunks_and_embeddings(
                        conn, parsed.id, chunks, embeddings
                    )
                    db.insert_edges(conn, edges)

                logger.info(
                    "ingested: %s (%d chunks, %d edges)",
                    relative_path,
                    len(chunks),
                    len(edges),
                )
                stats["ingested"] += 1
            except MindgraphError as e:
                logger.error("failed: %s — %s", relative_path, e)
                stats["failed"] += 1
            except Exception:
                logger.exception("unexpected failure: %s", relative_path)
                stats["failed"] += 1

        # Prune documents whose source file no longer exists. The scope walk
        # above (md_files) is authoritative for this ingest root, so any DB
        # document not backed by a current file is a deleted/renamed orphan.
        # Materialize the id list before deleting so we don't mutate a live
        # cursor. Inbound edges are left dangling by design (see delete_document).
        on_disk_ids = {
            parser.compute_doc_id(str(md_file.relative_to(directory)))
            for md_file in md_files
        }
        db_ids = [row["id"] for row in conn.execute("SELECT id FROM documents")]
        orphans = [doc_id for doc_id in db_ids if doc_id not in on_disk_ids]
        if orphans:
            with conn:
                for doc_id in orphans:
                    db.delete_document(conn, doc_id)
                    stats["pruned"] += 1
            logger.info("pruned %d orphaned document(s)", stats["pruned"])
    finally:
        conn.close()

    return stats


@app.command()
def init(
    db_path: str = typer.Option("mindgraph.sqlite", "--db", help="Path to SQLite DB."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Initialize the MindGraph database."""
    _configure_logging(verbose)
    try:
        db.init_db(db_path).close()
        logger.info("Initialized database at %s", db_path)
    except MindgraphError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)


@app.command()
def ingest(
    directory: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, readable=True
    ),
    db_path: str = typer.Option("mindgraph.sqlite", "--db", help="Path to SQLite DB."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Ingest a directory of markdown files."""
    _configure_logging(verbose)
    try:
        stats = _ingest_directory(directory, db_path)
        logger.info(
            "Done. total=%d ingested=%d skipped=%d pruned=%d failed=%d",
            stats["total"],
            stats["ingested"],
            stats["skipped"],
            stats["pruned"],
            stats["failed"],
        )
        if stats["failed"]:
            raise typer.Exit(code=1)
    except MindgraphError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)


def _format_query_result_block(idx: int, result) -> str:
    lex = result.lexical_rank if result.lexical_rank is not None else "-"
    sem = result.semantic_rank if result.semantic_rank is not None else "-"
    header = (
        f"#{idx}  signal={result.signal}  rrf_score={result.rrf_score:.6f}  "
        f"lex_rank={lex}  sem_rank={sem}"
    )
    if result.expansion_depth > 0:
        header = f"{header}  depth={result.expansion_depth}"
    if result.semantic_distance is not None:
        header = f"{header}  dist={result.semantic_distance:.4f}"
    if result.weak_fit:
        header = f"{header}  [weak-fit]"
    excerpt = result.chunk_text.strip().replace("\n", " ")
    if len(excerpt) > 280:
        excerpt = excerpt[:277] + "..."
    meta_parts = [
        f"{label}={value}"
        for label, value in (
            ("type", result.doc_type),
            ("domain", result.domain),
            ("status", result.status),
        )
        if value
    ]
    meta_line = f"    meta: {'  '.join(meta_parts)}\n" if meta_parts else ""
    return (
        f"{header}\n"
        f"    path: {result.path}\n"
        f"    title: {result.title}\n"
        f"{meta_line}"
        f"    chunk_index: {result.chunk_index}\n"
        f"    excerpt: {excerpt}"
    )


def _format_scope_warning(warning) -> str:
    return (
        "scope warning: "
        f"{warning.message} "
        f"recommended_trust_profile={warning.recommended_trust_profile}"
    )


def _format_neighbor_block(idx: int, neighbor) -> str:
    rel = neighbor.relationship_type or "(no relationship)"
    target_path = neighbor.target_path or "(dangling)"
    return (
        f"#{idx}  -> {neighbor.target_id}  rel={rel}\n"
        f"    target_path: {target_path}"
    )


@app.command()
def query(
    question: str = typer.Argument(..., help="The free-text query."),
    db_path: str = typer.Option("mindgraph.sqlite", "--db", help="Path to SQLite DB."),
    lexical_top_k: int = typer.Option(
        query_mod.DEFAULT_LEXICAL_TOP_K,
        "--lexical-top-k",
        help="Top-k for the FTS5 ranking before fusion.",
    ),
    semantic_top_k: int = typer.Option(
        query_mod.DEFAULT_SEMANTIC_TOP_K,
        "--semantic-top-k",
        help="Top-k for the vec_chunks ranking before fusion.",
    ),
    final_top_k: int = typer.Option(
        query_mod.DEFAULT_FINAL_TOP_K,
        "--top-k",
        help="Top-k for the fused output.",
    ),
    expand: bool = typer.Option(
        False,
        "--expand",
        help="Walk outbound graph edges from Phase 2 results and append expanded matches.",
    ),
    expand_depth: int = typer.Option(
        query_mod.DEFAULT_EXPAND_DEPTH,
        "--depth",
        min=1,
        max=3,
        help="Walk depth when --expand is set. Default 1, hard cap 3.",
    ),
    expand_top_k: int = typer.Option(
        query_mod.DEFAULT_EXPAND_TOP_K,
        "--expand-top-k",
        help="Cap on the number of appended expanded results.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of text."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run a fused lexical + semantic query against an ingested database.

    Pass --expand to also append outbound-graph-walk matches with signal=expanded.
    """
    _configure_logging(verbose)
    try:
        conn = db.get_db(db_path)
    except MindgraphError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)
    try:
        embedder = _load_embedder()
        results = query_mod.run_query(
            conn,
            question,
            embedder,
            lexical_top_k=lexical_top_k,
            semantic_top_k=semantic_top_k,
            final_top_k=final_top_k,
            expand=expand,
            expand_depth=expand_depth,
            expand_top_k=expand_top_k,
        )
    except MindgraphError as e:
        logger.error(str(e))
        conn.close()
        raise typer.Exit(code=1)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if as_json:
        typer.echo(json.dumps([r.model_dump() for r in results], indent=2))
        return

    warning = query_mod.classify_query_scope(question)
    if warning is not None:
        typer.echo(_format_scope_warning(warning))

    if not results:
        typer.echo("(no candidate found)")
        return

    for idx, result in enumerate(results, start=1):
        typer.echo(_format_query_result_block(idx, result))


@app.command()
def neighbors(
    doc_id: str = typer.Argument(..., help="The source document ID."),
    db_path: str = typer.Option("mindgraph.sqlite", "--db", help="Path to SQLite DB."),
    as_json: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of text."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """List outbound edges from a document. Preserves dangling edges."""
    _configure_logging(verbose)
    try:
        conn = db.get_db(db_path)
    except MindgraphError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)
    try:
        results = query_mod.list_neighbors(conn, doc_id)
    except MindgraphError as e:
        logger.error(str(e))
        conn.close()
        raise typer.Exit(code=1)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if as_json:
        typer.echo(json.dumps([n.model_dump() for n in results], indent=2))
        return

    if not results:
        typer.echo("(no outbound edges)")
        return

    for idx, neighbor in enumerate(results, start=1):
        typer.echo(_format_neighbor_block(idx, neighbor))


@app.command("serve-mcp")
def serve_mcp(
    db_path: str = typer.Option("mindgraph.sqlite", "--db", help="Path to SQLite DB."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Start a stdio MCP server for one ingested MindGraph database."""
    _configure_logging(verbose)
    conn = None
    try:
        conn = mcp_server.open_database(db_path)
        embedder = _load_embedder()
        server = mcp_server.create_server(
            conn,
            embedder,
            log_level="DEBUG" if verbose else "INFO",
        )
        mcp_server.run_stdio(server)
    except MindgraphError as e:
        logger.error(str(e))
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    app()
