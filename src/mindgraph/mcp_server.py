"""Stdio MCP transport for MindGraph.

Phase 5 is a transport wrap only. Query behavior stays in `mindgraph.query`.
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from starlette.responses import JSONResponse

from mindgraph import db
from mindgraph import query as query_mod
from mindgraph.embedders import EmbedTemplate, EmbedderSpec, format_query_text
from mindgraph import intent as intent_mod
from mindgraph import routing as routing_mod
from mindgraph.exceptions import MindgraphError
from mindgraph.models import QueryResult

REQUIRED_TABLES = {
    "documents",
    "documents_fts",
    "chunks",
    "vec_chunks",
    "edges",
}

logger = logging.getLogger("mindgraph")


class MCPServerStartupError(MindgraphError):
    """Raised when the MCP server cannot start cleanly."""


def _find_locked_operational_error(exc: BaseException) -> sqlite3.OperationalError | None:
    """Find a SQLite lock error even when a query layer wrapped it."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, sqlite3.OperationalError) and "locked" in str(
            current
        ).lower():
            return current
        current = current.__cause__ or current.__context__
    return None


def _run_with_lock_retry(operation, *, attempts: int = 5, base_delay: float = 0.5):
    """Retry transient SQLite lock errors, including wrapped query errors."""
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if _find_locked_operational_error(exc) is None or attempt + 1 >= attempts:
                raise
            time.sleep(base_delay * (attempt + 1))
    raise AssertionError("unreachable")


def open_database(db_path: str) -> sqlite3.Connection:
    """Open and validate the single database used by the MCP server."""
    if db_path != ":memory:" and not Path(db_path).exists():
        raise MCPServerStartupError(f"Database does not exist: {db_path}")

    try:
        conn = db.get_db(db_path, read_only=True)
        _validate_schema(conn, db_path)
        return conn
    except MindgraphError:
        raise
    except sqlite3.Error as e:
        raise MCPServerStartupError(f"Failed to open database at {db_path}: {e}") from e


def open_database_readonly(db_path: str) -> sqlite3.Connection:
    """Open and validate one index without allowing persistent writes."""
    expanded = Path(db_path).expanduser().resolve()
    if not expanded.exists():
        raise MCPServerStartupError(f"Database does not exist: {expanded}")
    try:
        conn = db.get_db(str(expanded), read_only=True)
        _validate_schema(conn, str(expanded))
        return conn
    except MindgraphError:
        raise
    except sqlite3.Error as exc:
        raise MCPServerStartupError(
            f"Failed to open database at {expanded}: {exc}"
        ) from exc


def _validate_schema(conn: sqlite3.Connection, db_path: str) -> None:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
    ).fetchall()
    existing = {row["name"] for row in rows}
    missing = sorted(REQUIRED_TABLES - existing)
    if missing:
        conn.close()
        raise MCPServerStartupError(
            f"Database at {db_path} is not a MindGraph database; "
            f"missing tables: {', '.join(missing)}"
        )


def create_server(
    conn: sqlite3.Connection,
    embedder: query_mod.Embedder,
    *,
    embedder_spec: EmbedderSpec | None = None,
    embed_template: EmbedTemplate = "none",
    intent_db_path: str | os.PathLike[str] = "~/.mindgraph/mainframe-intent.sqlite",
    log_level: Literal["DEBUG", "INFO"] = "INFO",
) -> FastMCP:
    """Create a FastMCP server bound to one DB connection and one embedder."""
    server = FastMCP(
        "mindgraph",
        instructions=(
            "MindGraph retrieves candidate chunks from a local Markdown vault. "
            "It does not verify claims. Each result carries semantic_distance "
            "(lower is a closer match) and a weak_fit flag: when weak_fit is "
            "true, this index has no strong answer for the query and the chunk "
            "should be treated as a low-confidence nomination, not an answer. "
            "Rows may also carry query_scope_warning when the query appears to "
            "ask for inbox, live, or project-status state that belongs in a "
            "different lifecycle scope."
        ),
        log_level=log_level,
    )

    @server.tool(
        name="query",
        description=(
            "Run the MindGraph lexical plus semantic query path, optionally "
            "appending outbound graph expansion or association results. "
            "Default response is a JSON array of QueryResult nominations. "
            "With envelope=true, returns "
            "{schema_version, intent_resolution, routing, results} where "
            "routing is single-database metadata for this MCP-bound index "
            "(not multi-index federation)."
        ),
    )
    def query_tool(
        question: str,
        lexical_top_k: int = query_mod.DEFAULT_LEXICAL_TOP_K,
        semantic_top_k: int = query_mod.DEFAULT_SEMANTIC_TOP_K,
        final_top_k: int = query_mod.DEFAULT_FINAL_TOP_K,
        expand: bool = False,
        expand_depth: int = query_mod.DEFAULT_EXPAND_DEPTH,
        expand_top_k: int = query_mod.DEFAULT_EXPAND_TOP_K,
        associate: bool = False,
        associate_top_k: int = query_mod.DEFAULT_ASSOCIATE_TOP_K,
        associate_seed_k: int = query_mod.DEFAULT_ASSOCIATE_SEED_K,
        envelope: bool = False,
    ) -> CallToolResult:
        formatted_question = question
        if embedder_spec is not None:
            formatted_question = format_query_text(
                embedder_spec, question, template=embed_template
            )
        try:
            results = _run_with_lock_retry(
                lambda: query_mod.run_query(
                    conn,
                    formatted_question,
                    embedder,
                    lexical_top_k=lexical_top_k,
                    semantic_top_k=semantic_top_k,
                    final_top_k=final_top_k,
                    expand=expand,
                    expand_depth=expand_depth,
                    expand_top_k=expand_top_k,
                    associate=associate,
                    associate_top_k=associate_top_k,
                    associate_seed_k=associate_seed_k,
                    embedder_spec=embedder_spec,
                    embed_template=embed_template,
                )
            )
            if envelope:
                payload = _query_envelope(
                    formatted_question,
                    results,
                    intent_db_path=intent_db_path,
                )
                return _json_result(payload)
            return _json_result([result.model_dump() for result in results])
        except sqlite3.OperationalError as e:
            return _tool_error(f"Database error: {e}")
        except MindgraphError as e:
            return _tool_error(str(e))
        except Exception:
            logger.exception("unexpected MCP query tool failure")
            raise

    @server.tool(
        name="graph_neighbors",
        description=(
            "List outbound MindGraph edges for a document ID, preserving "
            "dangling targets as null target_path values."
        ),
    )
    def graph_neighbors_tool(doc_id: str) -> CallToolResult:
        def lookup_neighbors():
            _ensure_document_exists(conn, doc_id)
            return query_mod.list_neighbors(conn, doc_id)

        try:
            results = _run_with_lock_retry(lookup_neighbors)
            return _json_result([result.model_dump() for result in results])
        except sqlite3.OperationalError as e:
            return _tool_error(f"Database error: {e}")
        except MindgraphError as e:
            return _tool_error(str(e))
        except Exception:
            logger.exception("unexpected MCP graph_neighbors tool failure")
            raise

    return server


def run_stdio(server: FastMCP) -> None:
    """Run the server on stdio. Stdout is reserved for MCP protocol frames."""
    server.run("stdio")


def create_shared_server(
    scopes: dict[str, tuple[sqlite3.Connection, str]],
    embedder: query_mod.Embedder,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp",
    embedder_spec: EmbedderSpec | None = None,
    embed_template: EmbedTemplate = "none",
) -> FastMCP:
    """Create a loopback shared server with one explicit scope per call."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise MCPServerStartupError("shared MCP daemon host must be loopback")
    if not path.startswith("/"):
        raise MCPServerStartupError("MCP path must start with '/'")
    server = FastMCP(
        "mindgraph-shared",
        instructions="Select one lifecycle scope. Results are nominations, not verified claims.",
        host=host,
        port=port,
        streamable_http_path=path,
    )

    def selected(scope: str) -> tuple[sqlite3.Connection, str]:
        try:
            return scopes[scope]
        except KeyError as exc:
            raise query_mod.QueryError(
                f"unknown scope: {scope}; choose one of: {', '.join(sorted(scopes))}"
            ) from exc

    @server.tool(name="query")
    def scoped_query(question: str, scope: str, final_top_k: int = query_mod.DEFAULT_FINAL_TOP_K) -> CallToolResult:
        try:
            conn, trust_profile = selected(scope)
            formatted = (
                format_query_text(embedder_spec, question, template=embed_template)
                if embedder_spec is not None else question
            )
            rows = _run_with_lock_retry(
                lambda: query_mod.run_query(conn, formatted, embedder, final_top_k=final_top_k)
            )
            return _json_result({"scope": scope, "trust_profile": trust_profile,
                                 "results": [row.model_dump() for row in rows]})
        except sqlite3.OperationalError as exc:
            return _tool_error(f"Database error: {exc}")
        except MindgraphError as exc:
            return _tool_error(str(exc))

    @server.tool(name="graph_neighbors")
    def scoped_neighbors(doc_id: str, scope: str) -> CallToolResult:
        try:
            conn, trust_profile = selected(scope)
            def lookup():
                _ensure_document_exists(conn, doc_id)
                return query_mod.list_neighbors(conn, doc_id)
            rows = _run_with_lock_retry(lookup)
            return _json_result({"scope": scope, "trust_profile": trust_profile,
                                 "results": [row.model_dump() for row in rows]})
        except sqlite3.OperationalError as exc:
            return _tool_error(f"Database error: {exc}")
        except MindgraphError as exc:
            return _tool_error(str(exc))

    @server.custom_route("/health", methods=["GET"])
    async def health(_request):
        return JSONResponse({"status": "ok", "scopes": [
            {"scope": name, "trust_profile": trust}
            for name, (_conn, trust) in sorted(scopes.items())
        ]})
    return server


def run_streamable_http(server: FastMCP) -> None:
    server.run("streamable-http")


def _ensure_document_exists(conn: sqlite3.Connection, doc_id: str) -> None:
    try:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE id = ? LIMIT 1", (doc_id,)
        ).fetchone()
    except sqlite3.Error as e:
        raise query_mod.QueryError(f"document lookup failed: {e}") from e
    if row is None:
        raise query_mod.QueryError(f"unknown doc_id: {doc_id}")


def _intent_resolution_payload(
    resolution: intent_mod.IntentResolution,
) -> dict:
    payload = resolution.model_dump()
    payload["method"] = payload.pop("resolution_method")
    payload["matched_goals"] = payload.pop("matched_goal_ids")
    payload["prerequisite_goals"] = payload.pop("prerequisite_goal_ids")
    payload["path"] = payload.pop("intent_path")
    return payload


def _resolve_intent_payload(
    question: str, intent_db_path: str | os.PathLike[str]
) -> tuple[dict | None, str, tuple[str, ...]]:
    expanded = Path(os.path.expanduser(os.fspath(intent_db_path)))
    if not expanded.exists():
        return None, "intent_store_missing", ()

    intent_conn = None
    try:
        intent_conn = intent_mod.open_intent_store(expanded)
        resolution = intent_mod.resolve_intent(
            intent_conn,
            question,
            limits=intent_mod.TraversalLimits(max_depth=2, max_nodes=32),
        )
        return (
            _intent_resolution_payload(resolution),
            "intent_resolved",
            tuple(resolution.warnings),
        )
    except Exception as exc:  # noqa: BLE001 - MCP envelope reports resolution failures.
        logger.warning("MCP intent resolution failed: %s", exc)
        return (
            {
                "outcome": "fallback",
                "method": "none",
                "refusal_reason": "intent_resolution_failed",
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
            "intent_resolution_failed",
            ("intent_resolution_failed",),
        )
    finally:
        if intent_conn is not None:
            intent_conn.close()


def _query_envelope(
    question: str,
    results: list[QueryResult],
    *,
    intent_db_path: str | os.PathLike[str],
) -> dict:
    intent_payload, reason, warnings = _resolve_intent_payload(question, intent_db_path)
    return {
        "schema_version": routing_mod.SCHEMA_VERSION,
        "intent_resolution": intent_payload,
        "routing": {
            "mode": "single_database",
            "selected_retrievers": ["mcp-bound-db"],
            "reason_codes": [reason],
            "warnings": list(warnings),
        },
        "results": [result.model_dump() for result in results],
    }


def _json_result(payload) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(type="text", text=json.dumps(payload, indent=2)),
        ],
        isError=False,
    )


def _tool_error(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        isError=True,
    )
