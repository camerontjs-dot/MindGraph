import json as jsonlib
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mcp.shared.memory import create_connected_server_and_client_session

from mindgraph import cli, db, parser
from mindgraph import mcp_server
from mindgraph.intent import compile_intent_corpus
from mindgraph.query import list_neighbors, run_query
from tests.test_query import KeywordEmbedder


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _doc_id(rel_path: str) -> str:
    return parser.compute_doc_id(rel_path)


@pytest.fixture
def mcp_embedder():
    return KeywordEmbedder(
        {
            "feedback": 0,
            "loops": 0,
            "reinforcing": 1,
            "balancing": 2,
            "archetype": 3,
            "compilers": 4,
        }
    )


@pytest.fixture
def mcp_db(tmp_path, monkeypatch, mcp_embedder):
    monkeypatch.setattr(cli, "_load_embedder", lambda *_a, **_k: mcp_embedder)

    notes = tmp_path / "vault"
    notes.mkdir()
    (notes / "feedback-loops.md").write_text(
        "Feedback loops explain circular system behavior. "
        "See [[reinforcing-loops]] (illustrates) and "
        "[[balancing-loops]] (illustrates).\n"
    )
    (notes / "reinforcing-loops.md").write_text(
        "Reinforcing loops amplify change over repeated cycles. "
        "Related to [[systems-archetypes]] (related).\n"
    )
    (notes / "balancing-loops.md").write_text(
        "Balancing loops counter drift and push a system toward a setpoint. "
        "Related to [[systems-archetypes]] (related).\n"
    )
    (notes / "systems-archetypes.md").write_text(
        "A systems archetype is a recurring feedback structure. "
        "This note cites [[missing-model]] (cites).\n"
    )
    (notes / "unrelated.md").write_text(
        "Compilers transform source code into executable forms.\n"
    )

    db_path = str(tmp_path / "test.sqlite")
    db.init_db(db_path).close()
    cli._ingest_directory(notes, db_path)
    return db_path


@pytest.fixture
def mcp_runtime(mcp_db, mcp_embedder):
    conn = mcp_server.open_database(mcp_db)
    server = mcp_server.create_server(conn, mcp_embedder)
    try:
        yield server, conn
    finally:
        conn.close()


@pytest.fixture
def mcp_intent_db(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "intent_graph_cases.yaml"
    catalog = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    source = tmp_path / "intent-source"
    source.mkdir()
    (source / "mainframe-core.yaml").write_text(
        yaml.safe_dump(catalog["documents"]["phase1"], sort_keys=False),
        encoding="utf-8",
    )
    destination = tmp_path / "intent.sqlite"
    compile_intent_corpus(source, destination)
    return destination


@pytest.fixture
def mcp_runtime_with_intent(mcp_db, mcp_embedder, mcp_intent_db):
    conn = mcp_server.open_database(mcp_db)
    server = mcp_server.create_server(
        conn,
        mcp_embedder,
        intent_db_path=str(mcp_intent_db),
    )
    try:
        yield server, conn
    finally:
        conn.close()


def _tool_json(result):
    assert result.isError is False
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return jsonlib.loads(result.content[0].text)


@pytest.mark.anyio
async def test_server_start_happy_path_lists_tools(mcp_runtime):
    server, _conn = mcp_runtime

    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_tools()

    tool_names = {tool.name for tool in result.tools}
    assert {"query", "graph_neighbors"} <= tool_names


def test_server_start_missing_db_is_clean_error(tmp_path, mcp_embedder):
    missing = tmp_path / "missing.sqlite"

    with pytest.raises(mcp_server.MCPServerStartupError) as exc:
        mcp_server.open_database(str(missing))

    assert "does not exist" in str(exc.value)
    assert not missing.exists()


@pytest.mark.anyio
async def test_query_tool_shape_matches_cli_json_surface(mcp_runtime, mcp_embedder):
    server, conn = mcp_runtime

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "query",
            {
                "question": "feedback loops",
                "final_top_k": 3,
            },
        )

    tool_rows = _tool_json(result)
    expected = [
        row.model_dump()
        for row in run_query(
            conn,
            "feedback loops",
            mcp_embedder,
            final_top_k=3,
        )
    ]
    assert tool_rows == expected


@pytest.mark.anyio
async def test_query_tool_envelope_includes_intent_and_routing_metadata(
    mcp_runtime_with_intent,
):
    server, _conn = mcp_runtime_with_intent

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "query",
            {
                "question": "Run the route contract checks.",
                "final_top_k": 3,
                "envelope": True,
            },
        )

    payload = _tool_json(result)
    assert set(payload) == {"schema_version", "intent_resolution", "routing", "results"}
    assert payload["schema_version"] == "1"
    assert payload["intent_resolution"]["graph_id"] == "mainframe.core"
    assert payload["intent_resolution"]["graph_version"] == "2026-06-29.1"
    assert payload["intent_resolution"]["outcome"] == "resolved"
    assert payload["intent_resolution"]["method"] == "alias"
    assert payload["intent_resolution"]["matched_goals"] == [
        "goal.route-contract-evaluation"
    ]
    assert payload["routing"]["mode"] == "single_database"
    assert payload["routing"]["selected_retrievers"] == ["mcp-bound-db"]
    assert payload["routing"]["reason_codes"] == ["intent_resolved"]
    assert isinstance(payload["results"], list)


@pytest.mark.anyio
async def test_graph_neighbors_tool_shape_matches_cli_json_surface(mcp_runtime):
    server, conn = mcp_runtime
    doc_id = _doc_id("feedback-loops.md")

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("graph_neighbors", {"doc_id": doc_id})

    tool_rows = _tool_json(result)
    expected = [row.model_dump() for row in list_neighbors(conn, doc_id)]
    assert tool_rows == expected


@pytest.mark.anyio
async def test_query_tool_routes_expand_parameters(mcp_runtime):
    server, _conn = mcp_runtime

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "query",
            {
                "question": "feedback loops",
                "final_top_k": 1,
                "expand": True,
                "expand_depth": 2,
                "expand_top_k": 3,
            },
        )

    rows = _tool_json(result)
    by_path = {row["path"]: row for row in rows}
    assert by_path["feedback-loops.md"]["expansion_depth"] == 0
    assert by_path["reinforcing-loops.md"]["signal"] == "expanded"
    assert by_path["reinforcing-loops.md"]["expansion_depth"] == 1
    assert by_path["balancing-loops.md"]["signal"] == "expanded"
    assert by_path["balancing-loops.md"]["expansion_depth"] == 1
    assert by_path["systems-archetypes.md"]["signal"] == "expanded"
    assert by_path["systems-archetypes.md"]["expansion_depth"] == 2


@pytest.mark.anyio
async def test_query_tool_emits_scope_warning(mcp_runtime):
    server, _conn = mcp_runtime

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "query",
            {
                "question": "current feedback status this week",
                "final_top_k": 3,
            },
        )

    rows = _tool_json(result)
    assert rows
    assert rows[0]["query_scope_warning"]["intent"] == "live_state"
    assert (
        rows[0]["query_scope_warning"]["recommended_trust_profile"]
        == "time_bound_live_state"
    )


@pytest.mark.anyio
async def test_graph_neighbors_preserves_dangling_target(mcp_runtime):
    server, _conn = mcp_runtime

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "graph_neighbors",
            {"doc_id": _doc_id("systems-archetypes.md")},
        )

    rows = _tool_json(result)
    assert len(rows) == 1
    assert rows[0]["target_id"] == _doc_id("missing-model.md")
    assert rows[0]["target_path"] is None


@pytest.mark.anyio
async def test_graph_neighbors_unknown_doc_id_is_clean_tool_error(mcp_runtime):
    server, _conn = mcp_runtime

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "graph_neighbors",
            {"doc_id": "not-a-real-doc"},
        )

    assert result.isError is True
    assert len(result.content) == 1
    assert "unknown doc_id" in result.content[0].text
    assert "Traceback" not in result.content[0].text


def test_serve_mcp_help_is_registered():
    runner = CliRunner()

    result = runner.invoke(cli.app, ["serve-mcp", "--help"])

    assert result.exit_code == 0
    assert "-db" in result.stdout


def test_serve_mcp_missing_db_exits_cleanly(tmp_path):
    runner = CliRunner()
    missing = tmp_path / "missing.sqlite"

    result = runner.invoke(cli.app, ["serve-mcp", "--db", str(missing)])

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert not Path(missing).exists()
