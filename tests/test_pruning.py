import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
sys.path.append(str(scripts_dir))

import pruning_pass


def test_compute_pagerank_simple():
    # Chain: A -> B -> C
    nodes = ["A", "B", "C"]
    edges = [("A", "B"), ("B", "C")]
    
    pr = pruning_pass.compute_pagerank(nodes, edges, damping=0.85, max_iter=100)
    
    assert pr["C"] > pr["B"]
    assert pr["B"] > pr["A"]
    assert abs(sum(pr.values()) - 1.0) < 1e-5


def test_compute_pagerank_cycle():
    # Cycle: A -> B -> A
    nodes = ["A", "B"]
    edges = [("A", "B"), ("B", "A")]
    
    pr = pruning_pass.compute_pagerank(nodes, edges, damping=0.85)
    assert abs(pr["A"] - pr["B"]) < 1e-5
    assert abs(pr["A"] - 0.5) < 1e-5


def test_get_age_days():
    dt = datetime.now(timezone.utc) - timedelta(days=10)
    iso_str = dt.isoformat()
    
    age = pruning_pass.get_age_days(iso_str)
    assert abs(age - 10.0) < 0.1


@patch("pruning_pass.load_graph")
@patch("shutil.move")
@patch("pathlib.Path.exists")
def test_run_pruning_dry_run(mock_exists, mock_move, mock_load):
    now = datetime.now(timezone.utc)
    old_date = (now - timedelta(days=100)).isoformat()
    new_date = (now - timedelta(days=10)).isoformat()
    
    nodes = [
        {
            "id": "1",
            "title": "Old Raw Stub",
            "path": "10_knowledge/domain/old_raw.md",
            "source_path": "domain/old_raw.md",
            "source_root": "/workspace/10_knowledge",
            "created_at": old_date,
            "metadata_json": '{"type": "raw"}',
        },
        {
            "id": "2",
            "title": "New Raw Stub",
            "path": "10_knowledge/domain/new_raw.md",
            "source_path": "domain/new_raw.md",
            "source_root": "/workspace/10_knowledge",
            "created_at": new_date,
            "metadata_json": '{"type": "raw"}',
        },
        {
            "id": "3",
            "title": "Old Synthesis Note",
            "path": "10_knowledge/domain/old_note.md",
            "source_path": "domain/old_note.md",
            "source_root": "/workspace/10_knowledge",
            "created_at": old_date,
            "metadata_json": '{"type": "note"}',
        }
    ]
    edges = []
    
    mock_load.return_value = (nodes, edges)
    mock_exists.return_value = True
    
    # Use pr_threshold_factor = 2.0 so node 1 (PageRank 1/3) is below threshold (2.0 * 1/3 = 2/3)
    with patch("builtins.print") as mock_print:
        pruning_pass.run_pruning(
            db_path=Path("dummy.sqlite"),
            workspace_root=Path("/workspace"),
            archive_root=Path("/workspace/90_archive"),
            age_threshold_days=30.0,
            pr_threshold_factor=2.0,
            damping=0.85,
            exclude_types={"note", "project", "intent"},
            apply=False
        )
        
        mock_move.assert_not_called()


@patch("pruning_pass.load_graph")
@patch("shutil.move")
@patch("pathlib.Path.exists")
@patch("pathlib.Path.mkdir")
def test_run_pruning_apply(mock_mkdir, mock_exists, mock_move, mock_load):
    now = datetime.now(timezone.utc)
    old_date = (now - timedelta(days=100)).isoformat()
    
    nodes = [
        {
            "id": "1",
            "title": "Old Raw Stub",
            "path": "10_knowledge/domain/old_raw.md",
            "source_path": "domain/old_raw.md",
            "source_root": "/workspace/10_knowledge",
            "created_at": old_date,
            "metadata_json": '{"type": "raw"}',
        }
    ]
    edges = []
    
    mock_load.return_value = (nodes, edges)
    mock_exists.return_value = True
    
    # Use pr_threshold_factor = 2.0 so PageRank of 1.0 is below threshold of 2.0
    pruning_pass.run_pruning(
        db_path=Path("dummy.sqlite"),
        workspace_root=Path("/workspace"),
        archive_root=Path("/workspace/90_archive"),
        age_threshold_days=30.0,
        pr_threshold_factor=2.0,
        damping=0.85,
        exclude_types={"note", "project", "intent"},
        apply=True
    )
    
    mock_move.assert_called_once_with(
        str(Path("/workspace/10_knowledge/domain/old_raw.md")),
        str(Path("/workspace/90_archive/10_knowledge/domain/old_raw.md"))
    )
