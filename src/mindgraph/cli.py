import json
import logging
import os
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import typer

from mindgraph import daemon, db, embedders, idle_lifecycle, parser
from mindgraph import query as query_mod
from mindgraph.exceptions import EmbeddingError, IngestionError, MindgraphError
from mindgraph import intent as intent_mod
from mindgraph import routing as routing_mod

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


def _load_embedder(embedder: str | None = None):
    spec = embedders.resolve_embedder(embedder)
    logger.info("Loading embedding model (%s)...", spec.model_id)
    try:
        return embedders.load_sentence_embedder(spec)
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError(
            f"Failed to load embedding model {spec.model_id!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def _require_mcp_server():
    try:
        from mindgraph import mcp_server
    except ModuleNotFoundError as exc:
        raise MindgraphError(
            "MCP support is not installed. Install it with "
            "`pip install 'mindgraph[mcp]'` (or `mindgraph[full]`)."
        ) from exc
    return mcp_server


def _require_mcp_proxy():
    try:
        from mindgraph import mcp_proxy
    except ModuleNotFoundError as exc:
        raise MindgraphError(
            "MCP proxy support is not installed. Install it with "
            "`pip install 'mindgraph[mcp]'` (or `mindgraph[full]`)."
        ) from exc
    return mcp_proxy


def _encode_without_progress(embedder, texts):
    """Encode text while suppressing sentence-transformers progress output."""
    try:
        try:
            return embedder.encode(
                texts, convert_to_numpy=True, show_progress_bar=False
            )
        except TypeError:
            return embedder.encode(texts, convert_to_numpy=True)
    except Exception as exc:
        raise EmbeddingError(
            f"Embedding model failed to encode input: {type(exc).__name__}: {exc}"
        ) from exc


@dataclass(frozen=True)
class IngestScope:
    root: Path
    index_id: str | None = None
    trust_profile: str | None = None
    namespace: str | None = None
    source_root: Path | None = None
    display_prefix: str | None = None
    include_globs: tuple[str, ...] = field(default_factory=tuple)
    exclude_globs: tuple[str, ...] = field(default_factory=tuple)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def _markdown_files_for_scope(scope: IngestScope) -> list[Path]:
    all_md = sorted(scope.root.rglob("*.md"))
    selected: list[Path] = []
    for md_file in all_md:
        rel_path = md_file.relative_to(scope.root).as_posix()
        if scope.include_globs and not _matches_any(rel_path, scope.include_globs):
            continue
        if scope.exclude_globs and _matches_any(rel_path, scope.exclude_globs):
            continue
        selected.append(md_file)
    return selected


def _display_path(prefix: str | None, source_path: str) -> str:
    if not prefix:
        return source_path
    return f"{prefix.rstrip('/')}/{source_path}"


def _apply_scope_provenance(
    parsed: parser.ParsedDocument, scope: IngestScope
) -> parser.ParsedDocument:
    has_provenance = any(
        (
            scope.index_id,
            scope.trust_profile,
            scope.namespace,
            scope.source_root,
            scope.display_prefix,
        )
    )
    if not has_provenance:
        return parsed

    source_path = parsed.path
    namespace = scope.namespace or ""
    doc_id = parsed.id
    if scope.index_id and namespace:
        doc_id = parser.compute_scoped_doc_id(scope.index_id, namespace, source_path)
    display_path = _display_path(scope.display_prefix, source_path)
    source_root = scope.source_root or scope.root
    return parsed.model_copy(
        update={
            "id": doc_id,
            "path": display_path,
            "index_id": scope.index_id,
            "trust_profile": scope.trust_profile,
            "namespace": namespace or None,
            "source_root": str(source_root),
            "source_path": source_path,
            "display_path": display_path,
        }
    )


def _doc_id_for_scope_file(scope: IngestScope, source_path: str) -> str:
    namespace = scope.namespace or ""
    if scope.index_id and namespace:
        return parser.compute_scoped_doc_id(scope.index_id, namespace, source_path)
    return parser.compute_doc_id(source_path)


def _ingest_scopes(
    scopes: list[IngestScope],
    db_path: str,
    *,
    embedder: str | None = None,
    embed_template: str | None = None,
    lexical_only: bool = False,
) -> dict[str, int]:
    stats = {"total": 0, "ingested": 0, "skipped": 0, "pruned": 0, "failed": 0}
    scope_files: list[tuple[IngestScope, Path]] = []
    for scope in scopes:
        md_files = _markdown_files_for_scope(scope)
        scope_files.extend((scope, md_file) for md_file in md_files)
    md_files = [md_file for _, md_file in scope_files]
    stats["total"] = len(md_files)

    if not md_files:
        logger.warning(
            "No markdown files found in authoritative ingest scope(s); "
            "pruning stale indexed documents"
        )

    spec = embedders.resolve_embedder(embedder)
    template = embedders.resolve_embed_template(embed_template)
    conn = db.init_db(
        db_path,
        embedding_dims=spec.dimensions,
        semantic_enabled=not lexical_only,
    )
    model = None
    parsed_docs: list[tuple[Path, parser.ParsedDocument]] = []

    try:
        for scope, md_file in scope_files:
            relative_path = md_file.relative_to(scope.root).as_posix()
            try:
                body_bytes = md_file.read_bytes()
                parsed = parser.parse_document(relative_path, body_bytes)
                parsed_docs.append(
                    (md_file, _apply_scope_provenance(parsed, scope))
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
                edges = parser.extract_document_graph_edges(
                    parsed,
                    link_resolver=link_resolver,
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
                embeddings: list[list[float]] | None = None if lexical_only else []
                if chunks and not lexical_only:
                    if model is None:
                        model = _load_embedder(embedder)
                    encode_chunks = [
                        embedders.format_passage_text(
                            spec,
                            chunk,
                            template=template,
                            title=parsed.title,
                            domain=parsed.metadata.get("domain"),
                            doc_type=parsed.metadata.get("type"),
                        )
                        for chunk in chunks
                    ]
                    raw = _encode_without_progress(model, encode_chunks)
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

        # Prune documents whose source file no longer exists. The selected file
        # walk is authoritative for this ingest root, including files that failed
        # to parse in this run; a partial refresh should not delete still-present
        # rows just because one source is temporarily malformed.
        # Materialize the id list before deleting so we don't mutate a live
        # cursor. Inbound edges are left dangling by design (see delete_document).
        on_disk_ids = {
            _doc_id_for_scope_file(scope, md_file.relative_to(scope.root).as_posix())
            for scope, md_file in scope_files
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


def _ingest_directory(
    directory: Path,
    db_path: str,
    *,
    index_id: str | None = None,
    trust_profile: str | None = None,
    namespace: str | None = None,
    source_root: Path | None = None,
    display_prefix: str | None = None,
    include_globs: tuple[str, ...] | None = None,
    exclude_globs: tuple[str, ...] | None = None,
    embedder: str | None = None,
    embed_template: str | None = None,
    lexical_only: bool = False,
) -> dict[str, int]:
    return _ingest_scopes(
        [
            IngestScope(
                root=directory,
                index_id=index_id,
                trust_profile=trust_profile,
                namespace=namespace,
                source_root=source_root,
                display_prefix=display_prefix,
                include_globs=include_globs or (),
                exclude_globs=exclude_globs or (),
            )
        ],
        db_path,
        embedder=embedder,
        embed_template=embed_template,
        lexical_only=lexical_only,
    )


def _coerce_globs(value, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise IngestionError(f"manifest field {field_name!r} must be a list")
    globs: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise IngestionError(
                f"manifest field {field_name!r} must contain non-empty strings"
            )
        globs.append(item.strip())
    return tuple(globs)


def _resolve_manifest_path(value: str, manifest_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = manifest_dir / path
    return path.resolve()


def _load_ingest_manifest(manifest_path: Path) -> list[IngestScope]:
    try:
        payload = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as e:
        raise IngestionError(f"manifest is not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        raise IngestionError("manifest must be a JSON object")

    scopes_payload = payload.get("scopes")
    if not isinstance(scopes_payload, list) or not scopes_payload:
        raise IngestionError("manifest must include a non-empty 'scopes' list")

    manifest_dir = manifest_path.resolve().parent
    scopes: list[IngestScope] = []
    for idx, entry in enumerate(scopes_payload, start=1):
        if not isinstance(entry, dict):
            raise IngestionError(f"manifest scope #{idx} must be an object")
        root_value = entry.get("root") or entry.get("path")
        if not isinstance(root_value, str) or not root_value.strip():
            raise IngestionError(f"manifest scope #{idx} requires a root path")
        root = _resolve_manifest_path(root_value, manifest_dir)
        if not root.is_dir():
            raise IngestionError(f"manifest scope #{idx} root is not a directory: {root}")
        source_root_value = entry.get("source_root")
        source_root = (
            _resolve_manifest_path(source_root_value, manifest_dir)
            if isinstance(source_root_value, str) and source_root_value.strip()
            else root
        )
        scopes.append(
            IngestScope(
                root=root,
                index_id=entry.get("index_id") or payload.get("index_id"),
                trust_profile=entry.get("trust_profile")
                or payload.get("trust_profile"),
                namespace=entry.get("namespace"),
                source_root=source_root,
                display_prefix=entry.get("display_prefix"),
                include_globs=_coerce_globs(
                    entry.get("include") or payload.get("include"), "include"
                ),
                exclude_globs=_coerce_globs(
                    entry.get("exclude") or payload.get("exclude"), "exclude"
                ),
            )
        )
    return scopes


@app.command()
def init(
    db_path: str = typer.Option("mindgraph.sqlite", "--db", help="Path to SQLite DB."),
    embedder: str | None = typer.Option(
        None,
        "--embedder",
        help="Embedder key (minilm, bge-small, e5-small). Sets vec_chunks dimensions.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Initialize the MindGraph database."""
    _configure_logging(verbose)
    try:
        spec = embedders.resolve_embedder(embedder)
        db.init_db(db_path, embedding_dims=spec.dimensions).close()
        logger.info(
            "Initialized database at %s (embedding_dims=%d, embedder=%s)",
            db_path,
            spec.dimensions,
            spec.key,
        )
    except MindgraphError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)


@app.command("bootstrap-model")
def bootstrap_model(
    embedder: str | None = typer.Option(
        None,
        "--embedder",
        help="Embedder key (minilm, bge-small, e5-small) or MINDGRAPH_EMBEDDER env.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Explicitly acquire and cache a semantic embedding model."""
    _configure_logging(verbose)
    try:
        spec = embedders.resolve_embedder(embedder)
        logger.info("Acquiring embedding model (%s)...", spec.model_id)
        embedders.bootstrap_sentence_embedder(spec)
        typer.echo(
            f"Embedding model ready: {spec.model_id} ({spec.dimensions} dimensions)"
        )
    except MindgraphError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)


def _format_db_doctor_block(report: dict) -> str:
    status = "OK" if report.get("ok") else "FAIL"
    role = report.get("role") or "db"
    trust = report.get("trust_profile") or "-"
    lines = [
        f"## {role}  [{status}]",
        f"  path:           {report.get('path')}",
        f"  trust_profile:  {trust}",
        f"  exists:         {report.get('exists')}",
    ]
    if report.get("size_bytes") is not None:
        size = report["size_bytes"]
        if size >= 1_048_576:
            size_h = f"{size / 1_048_576:.1f} MiB"
        elif size >= 1024:
            size_h = f"{size / 1024:.1f} KiB"
        else:
            size_h = f"{size} B"
        lines.append(f"  size:           {size_h} ({size} bytes)")
    if report.get("mtime_iso"):
        lines.append(f"  mtime:          {report['mtime_iso']}")
    if report.get("embedding_dims") is not None:
        lines.append(f"  embedding_dims: {report['embedding_dims']}")
    if report.get("semantic_enabled") is not None:
        lines.append(f"  semantic_enabled: {report['semantic_enabled']}")
    missing = report.get("tables_missing") or []
    if missing:
        lines.append(f"  tables_missing: {', '.join(missing)}")
    else:
        lines.append("  tables_missing: (none)")
    counts = report.get("counts") or {}
    if counts:
        parts = [
            f"{k}={v}" for k, v in counts.items() if v is not None
        ]
        if parts:
            lines.append(f"  counts:         {', '.join(parts)}")
    for issue in report.get("issues") or []:
        lines.append(f"  issue:          {issue}")
    for warn in report.get("warnings") or []:
        lines.append(f"  warning:        {warn}")
    return "\n".join(lines)


@app.command("doctor")
@app.command("status")
def doctor(
    db_path: str | None = typer.Option(
        None,
        "--db",
        help="Inspect a single DB. Default: dual MainFrame indexes under ~/.mindgraph/.",
    ),
    workspace: Path | None = typer.Option(
        None,
        "--workspace",
        help="Also scan this directory for tiny stub mainframe*.sqlite files (default: cwd).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON diagnostics."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """First-contact diagnostics for MindGraph indexes (MH01).

    Reports authoritative DB paths, sizes, required tables (documents_fts,
    vec_chunks, …), row counts, and workspace stub traps — without loading the
    embedding model.
    """
    _configure_logging(verbose)
    reports: list[dict] = []
    if db_path:
        reports.append(db.inspect_database(db_path))
    else:
        for spec in db.default_dual_db_specs():
            report = db.inspect_database(
                spec["path"],
                role=spec["role"],
                trust_profile=spec["trust_profile"],
            )
            report["refresh_hint"] = spec.get("refresh_hint")
            reports.append(report)

    scan_root = workspace if workspace is not None else Path.cwd()
    stubs = db.find_workspace_stub_sqlite([scan_root])

    # Exit non-zero only when a checked index is unusable. Workspace stubs are
    # loud warnings (common on MainFrame checkouts) but not hard failures when
    # ~/.mindgraph indexes are healthy.
    dbs_ok = all(r.get("ok") for r in reports)
    payload = {
        "ok": dbs_ok,
        "databases": reports,
        "workspace_stubs": stubs,
        "hints": [
            "Authoritative DBs: ~/.mindgraph/mainframe.sqlite (durable_knowledge)",
            "Authoritative DBs: ~/.mindgraph/mainframe-projects.sqlite (project_status)",
            "Never query workspace-root mainframe*.sqlite stubs",
            "Refresh: bin/mindgraph-refresh && bin/mindgraph-refresh-projects",
        ],
    }

    if as_json:
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo("# MindGraph doctor")
        typer.echo("")
        for report in reports:
            typer.echo(_format_db_doctor_block(report))
            if report.get("refresh_hint") and not report.get("ok"):
                typer.echo(f"  refresh_hint:  {report['refresh_hint']}")
            typer.echo("")
        if stubs:
            typer.echo("## workspace stubs (do not query these)")
            for stub in stubs:
                typer.echo(f"  path: {stub['path']}  size={stub['size_bytes']} B")
                typer.echo(f"  warning: {stub['warning']}")
            typer.echo("")
        if dbs_ok:
            msg = "Overall: OK — dual indexes look query-ready."
            if stubs:
                msg += " (workspace stubs present — ignore them; use ~/.mindgraph paths)"
            typer.echo(msg)
        else:
            typer.echo("Overall: FAIL — fix database issues above before planning queries.")
            typer.echo("Hints:")
            for h in payload["hints"]:
                typer.echo(f"  - {h}")

    if not dbs_ok:
        raise typer.Exit(code=1)


@app.command()
def ingest(
    directory: Path = typer.Argument(
        ..., exists=True, file_okay=False, dir_okay=True, readable=True
    ),
    db_path: str = typer.Option("mindgraph.sqlite", "--db", help="Path to SQLite DB."),
    index_id: str | None = typer.Option(
        None, "--index-id", help="Optional lifecycle/index identifier."
    ),
    trust_profile: str | None = typer.Option(
        None, "--trust-profile", help="Optional trust profile for every document."
    ),
    namespace: str | None = typer.Option(
        None, "--namespace", help="Optional namespace for scoped document IDs."
    ),
    source_root: Path | None = typer.Option(
        None, "--source-root", help="Absolute source root stored as provenance."
    ),
    display_prefix: str | None = typer.Option(
        None, "--display-prefix", help="Path prefix shown to query clients."
    ),
    embedder: str | None = typer.Option(
        None,
        "--embedder",
        help="Embedder key (minilm, bge-small, e5-small) or MINDGRAPH_EMBEDDER env.",
    ),
    embed_template: str | None = typer.Option(
        None,
        "--embed-template",
        help="Optional passage/query template (none, mainframe).",
    ),
    lexical_only: bool = typer.Option(
        False,
        "--lexical-only",
        help="Build an FTS5/graph index without loading or storing semantic vectors.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Ingest a directory of markdown files."""
    _configure_logging(verbose)
    try:
        stats = _ingest_directory(
            directory,
            db_path,
            index_id=index_id,
            trust_profile=trust_profile,
            namespace=namespace,
            source_root=source_root,
            display_prefix=display_prefix,
            embedder=embedder,
            embed_template=embed_template,
            lexical_only=lexical_only,
        )
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


@app.command("ingest-many")
def ingest_many(
    manifest: Path = typer.Argument(
        ..., exists=True, file_okay=True, dir_okay=False, readable=True
    ),
    db_path: str = typer.Option("mindgraph.sqlite", "--db", help="Path to SQLite DB."),
    allow_failures: bool = typer.Option(
        False,
        "--allow-failures",
        help="Keep a partial index when some files fail to parse or ingest.",
    ),
    embedder: str | None = typer.Option(
        None,
        "--embedder",
        help="Embedder key (minilm, bge-small, e5-small) or MINDGRAPH_EMBEDDER env.",
    ),
    embed_template: str | None = typer.Option(
        None,
        "--embed-template",
        help="Optional passage/query template (none, mainframe).",
    ),
    lexical_only: bool = typer.Option(
        False,
        "--lexical-only",
        help="Build an FTS5/graph index without loading or storing semantic vectors.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Ingest multiple markdown roots from a JSON manifest as one index."""
    _configure_logging(verbose)
    try:
        scopes = _load_ingest_manifest(manifest)
        stats = _ingest_scopes(
            scopes,
            db_path,
            embedder=embedder,
            embed_template=embed_template,
            lexical_only=lexical_only,
        )
        logger.info(
            "Done. scopes=%d total=%d ingested=%d skipped=%d pruned=%d failed=%d",
            len(scopes),
            stats["total"],
            stats["ingested"],
            stats["skipped"],
            stats["pruned"],
            stats["failed"],
        )
        if stats["failed"] and not allow_failures:
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
    provenance_parts = [
        f"{label}={value}"
        for label, value in (
            ("index", result.index_id),
            ("trust", result.trust_profile),
            ("namespace", result.namespace),
        )
        if value
    ]
    provenance_line = (
        f"    provenance: {'  '.join(provenance_parts)}\n"
        if provenance_parts
        else ""
    )
    return (
        f"{header}\n"
        f"    path: {result.path}\n"
        f"    title: {result.title}\n"
        f"{meta_line}"
        f"{provenance_line}"
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
    lexical_only: bool = typer.Option(
        False,
        "--lexical-only",
        help="Use only FTS5 and graph data; do not load an embedding model.",
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
    associate: bool = typer.Option(
        False,
        "--associate",
        help="Append semantic doc-neighbor matches from fused seeds (ADR-034).",
    ),
    associate_top_k: int = typer.Option(
        query_mod.DEFAULT_ASSOCIATE_TOP_K,
        "--associate-top-k",
        help="Cap on appended associated results.",
    ),
    associate_seed_k: int = typer.Option(
        query_mod.DEFAULT_ASSOCIATE_SEED_K,
        "--associate-seed-k",
        help="How many fused rows seed association (default min(5, top-k)).",
    ),
    embedder: str | None = typer.Option(
        None,
        "--embedder",
        help="Embedder key (minilm, bge-small, e5-small) or MINDGRAPH_EMBEDDER env.",
    ),
    embed_template: str | None = typer.Option(
        None,
        "--embed-template",
        help="Optional query template (none, mainframe).",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of text."
    ),
    envelope: bool = typer.Option(
        False,
        "--envelope",
        help="With --json, emit intent metadata plus results instead of the legacy result list.",
    ),
    citable_only: bool = typer.Option(
        False,
        "--citable-only",
        help=(
            "Drop quarantined, retracted, and superseded documents from the "
            "legacy --json list. --envelope always separates them instead."
        ),
    ),
    no_intent: bool = typer.Option(
        False, "--no-intent", help="Skip intent graph resolution."
    ),
    intent_db: str = typer.Option(
        "~/.mindgraph/mainframe-intent.sqlite", "--intent-db", help="Path to intent graph DB for resolution."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run a fused lexical + semantic query against an ingested database.

    Pass --expand for graph BFS matches; --associate for semantic doc neighbors.
    Pass --lexical-only for the dependency-light FTS5/graph profile.
    Pass --envelope with --json to include intent graph resolution metadata.
    Plain --json preserves the legacy result-list contract for existing callers.
    Text output still shows intent resolution by default. Use --no-intent to skip.
    """
    _configure_logging(verbose)
    try:
        conn = db.get_db(db_path, read_only=True)
        # MH01: fail fast on stub/incomplete DBs instead of soft FTS degradation.
        db.validate_query_schema(conn, db_path)
    except MindgraphError as e:
        logger.error(str(e))
        raise typer.Exit(code=1)

    try:
        try:
            if lexical_only and associate:
                raise EmbeddingError(
                    "--associate requires semantic retrieval; remove --lexical-only"
                )
            effective_semantic_top_k = 0 if lexical_only else semantic_top_k
            template = embedders.resolve_embed_template(embed_template)
            spec = None
            model = None
            if effective_semantic_top_k > 0 or associate:
                spec = embedders.resolve_embedder(embedder)
                if db.get_semantic_enabled(conn) is False:
                    raise EmbeddingError(
                        "This database is lexical-only. Re-ingest it without "
                        "--lexical-only to enable semantic retrieval."
                    )
                model = _load_embedder(spec.key)
                formatted_question = embedders.format_query_text(
                    spec, question, template=template
                )
            else:
                formatted_question = question
            results = query_mod.run_query(
                conn,
                formatted_question,
                model,
                lexical_top_k=lexical_top_k,
                semantic_top_k=effective_semantic_top_k,
                final_top_k=final_top_k,
                expand=expand,
                expand_depth=expand_depth,
                expand_top_k=expand_top_k,
                associate=associate,
                associate_top_k=associate_top_k,
                associate_seed_k=associate_seed_k,
                embedder_spec=spec,
                embed_template=template,
            )
        except MindgraphError as e:
            logger.error(str(e))
            raise typer.Exit(code=1)

        resolution = None
        if not no_intent and (envelope or not as_json):
            intent_path = os.path.expanduser(intent_db)
            intent_conn = None
            if os.path.exists(intent_path):
                try:
                    intent_conn = intent_mod.open_intent_store(intent_path)
                except Exception as exc:
                    logger.warning("Failed to open intent DB: %s", exc)
            if intent_conn:
                try:
                    resolution = intent_mod.resolve_intent(
                        intent_conn,
                        formatted_question,
                        limits=intent_mod.TraversalLimits(max_depth=2, max_nodes=32),
                    )
                finally:
                    try:
                        intent_conn.close()
                    except Exception:
                        pass

        if as_json:
            if envelope:
                # Match MCP envelope shape so CLI and tool callers share one parser.
                if resolution is not None:
                    resolution_payload = resolution.as_transport_payload()
                    reason = "intent_resolved"
                    warnings = list(resolution.warnings)
                else:
                    intent_path = Path(os.path.expanduser(intent_db))
                    if intent_path.exists():
                        reason = "intent_resolution_skipped_or_failed"
                        warnings = ["intent_resolution_unavailable"]
                    else:
                        reason = "intent_store_missing"
                        warnings = []
                    resolution_payload = None
                out = {
                    "schema_version": routing_mod.SCHEMA_VERSION,
                    "intent_resolution": resolution_payload,
                    "routing": {
                        "mode": "single_database",
                        "selected_retrievers": ["cli-bound-db"],
                        "reason_codes": [reason],
                        "warnings": warnings,
                    },
                    **query_mod.citation_partition_payload(results),
                }
            else:
                emitted = results
                if citable_only:
                    emitted, _ = query_mod.partition_by_citation(results)
                out = [r.model_dump() for r in emitted]
            typer.echo(json.dumps(out, indent=2, default=str))
            return

        if resolution:
            typer.echo("=== Intent Resolution from graph ===")
            typer.echo(
                f"graph: {resolution.graph_id}@{resolution.graph_version}"
            )
            typer.echo(
                f"outcome: {resolution.outcome} "
                f"(method: {resolution.resolution_method})"
            )
            if resolution.matched_goal_ids:
                typer.echo(f"matched goals: {list(resolution.matched_goal_ids)}")
            if resolution.capability_hints:
                typer.echo(f"hints: {list(resolution.capability_hints)}")
            if resolution.warnings:
                typer.echo(f"warnings: {list(resolution.warnings)}")
            typer.echo("--- results below ---")
        elif not no_intent:
            typer.echo("(no intent DB or resolution; legacy results)")

        warning = query_mod.classify_query_scope(question)
        if warning is not None:
            typer.echo(_format_scope_warning(warning))

        if not results:
            typer.echo("(no candidate found)")
            return

        for idx, result in enumerate(results, start=1):
            typer.echo(_format_query_result_block(idx, result))
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


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
        conn = db.get_db(db_path, read_only=True)
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
    embedder: str | None = typer.Option(
        None,
        "--embedder",
        help="Embedder key (minilm, bge-small, e5-small) or MINDGRAPH_EMBEDDER env.",
    ),
    embed_template: str | None = typer.Option(
        None,
        "--embed-template",
        help="Optional query template (none, mainframe).",
    ),
    intent_db: str = typer.Option(
        "~/.mindgraph/mainframe-intent.sqlite",
        "--intent-db",
        help="Path to intent graph DB used for MCP query envelope metadata.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Start a stdio MCP server for one ingested MindGraph database."""
    _configure_logging(verbose)
    conn = None
    try:
        mcp_server = _require_mcp_server()
        conn = mcp_server.open_database(db_path)
        spec = embedders.resolve_embedder(embedder)
        template = embedders.resolve_embed_template(embed_template)
        model = _load_embedder(spec.key)
        server = mcp_server.create_server(
            conn,
            model,
            embedder_spec=spec,
            embed_template=template,
            intent_db_path=intent_db,
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


SCOPE_SPEC_HELP = (
    "Serve an arbitrary named scope: NAME=PATH or NAME:TRUST_PROFILE=PATH. "
    "Repeatable. Trust profile defaults to NAME. When any --scope is given it "
    "replaces the default knowledge/projects pair."
)


def parse_scope_specs(values: list[str] | None) -> dict[str, tuple[str, str]]:
    """Parse `NAME=PATH` / `NAME:TRUST=PATH` specs into {name: (path, trust)}.

    Split on the first `=` only, so database paths may contain any character.
    """
    scopes: dict[str, tuple[str, str]] = {}
    for raw in values or []:
        name_part, sep, db_path = raw.partition("=")
        if not sep or not name_part.strip() or not db_path.strip():
            raise MindgraphError(
                f"invalid --scope {raw!r}; expected NAME=PATH or NAME:TRUST_PROFILE=PATH"
            )
        name, _, trust = name_part.partition(":")
        name, trust = name.strip(), trust.strip()
        if not name:
            raise MindgraphError(f"invalid --scope {raw!r}; scope name is empty")
        if name in scopes:
            raise MindgraphError(f"duplicate --scope name: {name}")
        scopes[name] = (db_path.strip(), trust or name)
    return scopes


@app.command("serve-daemon")
def serve_daemon(
    knowledge_db: str = typer.Option("~/.mindgraph/mainframe.sqlite", "--knowledge-db"),
    projects_db: str = typer.Option("~/.mindgraph/mainframe-projects.sqlite", "--projects-db"),
    scope: list[str] = typer.Option(None, "--scope", help=SCOPE_SPEC_HELP),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    path: str = typer.Option("/mcp", "--path"),
    embedder: str | None = typer.Option(None, "--embedder"),
    idle_seconds: float | None = typer.Option(None, "--idle-seconds", min=60.0),
    lease_ttl: float = typer.Option(90.0, "--lease-ttl", min=30.0),
):
    """Run the explicit-scope shared MCP server in the foreground."""
    conns = []
    try:
        mcp_server = _require_mcp_server()
        requested = parse_scope_specs(scope) or {
            "knowledge": (knowledge_db, "durable_knowledge"),
            "projects": (projects_db, "project_status"),
        }
        scopes = {}
        for name, (db_path, trust_profile) in requested.items():
            conn = mcp_server.open_database_readonly(db_path)
            conns.append(conn)
            scopes[name] = (conn, trust_profile)
        spec = embedders.resolve_embedder(embedder)
        lifecycle = (
            idle_lifecycle.IdleLifecycle(idle_seconds, lease_ttl=lease_ttl)
            if idle_seconds is not None else None
        )
        server = mcp_server.create_shared_server(
            scopes,
            _load_embedder(spec.key), host=host, port=port, path=path,
            embedder_spec=spec, lifecycle=lifecycle,
        )
        if lifecycle:
            lifecycle.start()
        try:
            mcp_server.run_streamable_http(server)
        finally:
            if lifecycle:
                lifecycle.stop()
    except MindgraphError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    finally:
        for conn in conns:
            conn.close()


@app.command("mcp-proxy")
def mcp_proxy_command(
    url: str = typer.Option("http://127.0.0.1:8000/mcp", "--url"),
    lease: bool = typer.Option(False, "--lease"),
    auto_start: bool | None = typer.Option(None, "--auto-start/--no-auto-start"),
    state_dir: Path = typer.Option(Path("~/.mindgraph/run").expanduser(), "--state-dir"),
    idle_seconds: float = typer.Option(900.0, "--idle-seconds", min=60.0),
):
    """Proxy stdio MCP to an already-running shared daemon."""
    try:
        mcp_proxy = _require_mcp_proxy()
        activated_grace = daemon.idle_opt_in(state_dir)
        if auto_start is None:
            auto_start = activated_grace is not None
        if activated_grace is not None and idle_seconds == 900.0:
            idle_seconds = activated_grace
        if auto_start:
            health_url = daemon.health_url_for_mcp(url)
            daemon_host, daemon_port, daemon_path = daemon.daemon_endpoint_args(url)
            command = [
                sys.executable, "-m", "mindgraph.cli", "serve-daemon",
                "--idle-seconds", str(idle_seconds),
                "--host", daemon_host, "--port", str(daemon_port),
                "--path", daemon_path,
            ]
            result = daemon.start(state_dir, command, health_url=health_url)
            if result["status"] == "start_failed":
                raise MindgraphError(f"shared daemon failed to start: {result}")
        mcp_proxy.run_proxy_sync(url, lease=lease or auto_start)
    except Exception as exc:
        typer.echo(f"MCP proxy failed: {exc}", err=True)
        raise typer.Exit(1)


@app.command("daemon-start")
def daemon_start(
    state_dir: Path = typer.Option(Path("~/.mindgraph/run").expanduser(), "--state-dir"),
    knowledge_db: str = typer.Option("~/.mindgraph/mainframe.sqlite", "--knowledge-db"),
    projects_db: str = typer.Option("~/.mindgraph/mainframe-projects.sqlite", "--projects-db"),
    scope: list[str] = typer.Option(None, "--scope", help=SCOPE_SPEC_HELP),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    path: str = typer.Option("/mcp", "--path"),
    idle_seconds: float | None = typer.Option(None, "--idle-seconds", min=60.0),
):
    try:
        parse_scope_specs(scope)
    except MindgraphError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    command = [sys.executable, "-m", "mindgraph.cli", "serve-daemon",
               "--host", host, "--port", str(port), "--path", path]
    if scope:
        for spec in scope:
            command.extend(["--scope", spec])
    else:
        command.extend(["--knowledge-db", knowledge_db,
                        "--projects-db", projects_db])
    if idle_seconds is not None:
        command.extend(["--idle-seconds", str(idle_seconds)])
    health_endpoint = daemon.health_url(host, port)
    typer.echo(json.dumps(daemon.start(state_dir, command, health_url=health_endpoint)))


@app.command("daemon-status")
def daemon_status(
    state_dir: Path = typer.Option(Path("~/.mindgraph/run").expanduser(), "--state-dir"),
    url: str = typer.Option("http://127.0.0.1:8000/health", "--url"),
):
    typer.echo(json.dumps(daemon.status(state_dir, url)))


@app.command("daemon-health")
def daemon_health(url: str = typer.Option("http://127.0.0.1:8000/health", "--url")):
    result = daemon.health(url)
    typer.echo(json.dumps(result))
    if result.get("status") != "ok":
        raise typer.Exit(1)


@app.command("daemon-stop")
def daemon_stop(
    state_dir: Path = typer.Option(Path("~/.mindgraph/run").expanduser(), "--state-dir"),
    url: str = typer.Option("http://127.0.0.1:8000/health", "--url"),
):
    typer.echo(json.dumps(daemon.stop(state_dir, health_url=url)))


if __name__ == "__main__":
    app()
