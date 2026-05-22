"""Stdio MCP transport for MindGraph.

Phase 5 is a transport wrap only. Query behavior stays in `mindgraph.query`.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from mindgraph import db
from mindgraph import query as query_mod
from mindgraph.exceptions import MindgraphError

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


def open_database(db_path: str) -> sqlite3.Connection:
    """Open and validate the single database used by the MCP server."""
    if db_path != ":memory:" and not Path(db_path).exists():
        raise MCPServerStartupError(f"Database does not exist: {db_path}")

    try:
        conn = db.get_db(db_path)
        _validate_schema(conn, db_path)
        return conn
    except MindgraphError:
        raise
    except sqlite3.Error as e:
        raise MCPServerStartupError(f"Failed to open database at {db_path}: {e}") from e


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
    log_level: Literal["DEBUG", "INFO"] = "INFO",
) -> FastMCP:
    """Create a FastMCP server bound to one DB connection and one embedder."""
    server = FastMCP(
        "mindgraph",
        instructions=(
            "MindGraph retrieves candidate chunks from a local Markdown vault. "
            "It does not verify claims."
        ),
        log_level=log_level,
    )

    @server.tool(
        name="query",
        description=(
            "Run the MindGraph lexical plus semantic query path, optionally "
            "appending outbound graph expansion results."
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
    ) -> CallToolResult:
        try:
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
            return _tool_error(str(e))
        except Exception:
            logger.exception("unexpected MCP query tool failure")
            raise
        return _json_result([result.model_dump() for result in results])

    @server.tool(
        name="graph_neighbors",
        description=(
            "List outbound MindGraph edges for a document ID, preserving "
            "dangling targets as null target_path values."
        ),
    )
    def graph_neighbors_tool(doc_id: str) -> CallToolResult:
        try:
            _ensure_document_exists(conn, doc_id)
            results = query_mod.list_neighbors(conn, doc_id)
        except MindgraphError as e:
            return _tool_error(str(e))
        except Exception:
            logger.exception("unexpected MCP graph_neighbors tool failure")
            raise
        return _json_result([result.model_dump() for result in results])

    return server


def run_stdio(server: FastMCP) -> None:
    """Run the server on stdio. Stdout is reserved for MCP protocol frames."""
    server.run("stdio")


def _ensure_document_exists(conn: sqlite3.Connection, doc_id: str) -> None:
    try:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE id = ? LIMIT 1", (doc_id,)
        ).fetchone()
    except sqlite3.Error as e:
        raise query_mod.QueryError(f"document lookup failed: {e}") from e
    if row is None:
        raise query_mod.QueryError(f"unknown doc_id: {doc_id}")


def _json_result(payload: list[dict]) -> CallToolResult:
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
