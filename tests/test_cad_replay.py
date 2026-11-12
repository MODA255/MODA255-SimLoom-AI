"""History browsing must render saved geometry and leave the project untouched."""
import json
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.cad_replay import history_view
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_server import MCPServer
from engineering_agent.transactions import RunTransaction


def source(root, name="history.dxf"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_dxf(path, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}],
              circles=[{"x": 5, "y": 5, "radius": 2}])
    return path


def edit(root, path, operation="cad.entity_move", **kwargs):
    return EngineeringAgent().execute_operation(operation, {
        "project_dir": str(root), "input_file": str(path), "output_file": str(path),
        "auto_repair": False, **({"entity_ids": ["e00001"], "delta": [4, 0, 0]}
                                if operation == "cad.entity_move" else {}), **kwargs})


def view(root, path, revision, side="after"):
    return EngineeringAgent().execute_operation("cad.history_view", {
        "project_dir": str(root), "input_file": str(path), "revision_id": revision, "side": side})


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_move_delete_and_redo_snapshots_preserve_current_files_and_cursor(tmp_path):
    path = source(tmp_path)
    moved = edit(tmp_path, path)["run_id"]
    deleted = edit(tmp_path, path, "cad.entity_delete", entity_ids=["e00002"])["run_id"]
    edit(tmp_path, path, "cad.undo")
    before_files = files(tmp_path)
    initial = view(tmp_path, path, moved, "before")
    after_move = view(tmp_path, path, moved)
    before_delete = view(tmp_path, path, deleted, "before")
    after_delete = view(tmp_path, path, deleted)
    assert initial["document"]["entities"][0]["start"]["x"] == 0
    assert after_move["document"]["entities"][0]["start"]["x"] == 4
    assert len(before_delete["document"]["entities"]) == 2
    assert len(after_delete["document"]["entities"]) == 1
    assert 'data-entity-id="e00002"' in before_delete["scene"]["svg"]
    assert 'data-entity-id="e00002"' not in after_delete["scene"]["svg"]
    assert initial["scene"]["svg"] != after_move["scene"]["svg"]
    for result in (initial, after_move, before_delete, after_delete):
        assert result["read_only"] is True
        assert result["history"]["cursor"] == 1
        assert result["history"]["can_redo"]
        assert result["scene"]["source_sha256"] == result["snapshot"]["sha256"]
        assert "path" not in result["snapshot"]
        assert "data-read-only='true'" in result["viewer"]
    assert files(tmp_path) == before_files


def test_created_file_before_snapshot_is_empty_even_after_undo(tmp_path):
    path = tmp_path / "created.dxf"
    EngineeringAgent().write_asset(str(path), "dxf", {"lines": [
        {"x1": 0, "y1": 0, "x2": 4, "y2": 0}]}, project_dir=tmp_path)
    revision = EngineeringAgent().execute_operation("cad.history", {
        "project_dir": str(tmp_path), "input_file": str(path)})["history"]["revisions"][0]["id"]
    edit(tmp_path, path, "cad.undo")
    before_files = files(tmp_path)
    result = view(tmp_path, path, revision, "before")
    assert result["document"] is None and not result["snapshot"]["exists"]
    assert result["scene"]["entity_count"] == 0
    assert view(tmp_path, path, revision)["scene"]["entity_count"] == 1
    assert files(tmp_path) == before_files


def test_external_current_edits_do_not_hide_verified_history(tmp_path):
    path = source(tmp_path)
    revision = edit(tmp_path, path)["run_id"]
    path.write_bytes(b"external editor replaced current drawing")
    Path(str(path) + ".cad.json").write_bytes(b"also replaced")
    before_files = files(tmp_path)
    assert view(tmp_path, path, revision)["document"]["entities"][0]["start"]["x"] == 4
    assert files(tmp_path) == before_files


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("suffix", ["", ".cad.json"])
def test_corrupt_or_unexpected_snapshot_is_rejected(tmp_path, side, suffix):
    path = source(tmp_path)
    revision = edit(tmp_path, path)["run_id"]
    snapshot = tmp_path / "artifacts/runs" / revision / ("backup" if side == "before" else "after") / (path.name + suffix)
    snapshot.write_bytes(b"damaged or unexpectedly present")
    before_files = files(tmp_path)
    with pytest.raises(RuntimeError, match="CAD_HISTORY_SNAPSHOT_CORRUPT"):
        view(tmp_path, path, revision, side)
    assert files(tmp_path) == before_files


def test_revision_scope_and_discarded_redo_branch(tmp_path):
    path = source(tmp_path)
    other = source(tmp_path, "other.dxf")
    revision = edit(tmp_path, path)["run_id"]
    for target, identifier in ((other, revision), (path, "unknown")):
        with pytest.raises(ValueError, match="CAD_HISTORY_REVISION_NOT_FOUND"):
            view(tmp_path, target, identifier)
    edit(tmp_path, path, "cad.undo")
    edit(tmp_path, path, delta=[2, 0, 0])
    with pytest.raises(ValueError, match="CAD_HISTORY_REVISION_NOT_FOUND"):
        view(tmp_path, path, revision)


@pytest.mark.parametrize("target,revision,side,error", [
    ("history.dxf", "../escape", "after", "CAD_HISTORY_INVALID_REVISION"),
    ("history.dxf", "unknown", "invalid", "CAD_HISTORY_INVALID_SIDE"),
    ("../outside.dxf", "unknown", "after", "PATH"),
    ("history.json", "unknown", "after", "CAD_HISTORY_REQUIRES_DXF"),
])
def test_untrusted_arguments_fail_before_reading(tmp_path, target, revision, side, error):
    with pytest.raises(ValueError, match=error):
        history_view(tmp_path, target, revision, side)


def test_snapshot_paths_ignore_metadata_and_reject_symlinks(tmp_path):
    path = source(tmp_path)
    revision = edit(tmp_path, path)["run_id"]
    history_file = next((tmp_path / ".engineering-agent/cad-history").glob("*.json"))
    data = json.loads(history_file.read_text())
    for entry in data["revisions"][0]["files"]:
        entry["snapshot"] = "../../outside.dxf"
    history_file.write_text(json.dumps(data))
    assert view(tmp_path, path, revision)["scene"]["entity_count"] == 2
    snapshot = tmp_path / "artifacts/runs" / revision / "after" / path.name
    saved = tmp_path / "saved.dxf"
    saved.write_bytes(snapshot.read_bytes())
    snapshot.unlink()
    try:
        snapshot.symlink_to(saved)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="TRANSACTION_SYMLINK_PATH"):
        view(tmp_path, path, revision)


def test_snapshot_read_requires_project_lock(tmp_path):
    path = source(tmp_path)
    revision = edit(tmp_path, path)["run_id"]
    with RunTransaction.read_lock(tmp_path):
        with pytest.raises(RuntimeError, match="PROJECT_LOCKED"):
            view(tmp_path, path, revision)


def test_relative_input_without_project_dir_is_resolved_once(tmp_path, monkeypatch):
    path = source(tmp_path, "nested/history.dxf")
    revision = edit(path.parent, path)["run_id"]
    monkeypatch.chdir(tmp_path)
    result = EngineeringAgent().execute_operation("cad.history_view", {
        "input_file": "nested/history.dxf", "revision_id": revision})
    assert result["document"]["entities"][0]["start"]["x"] == 4


def test_batch_history_records_actual_changes_per_step(tmp_path):
    path = source(tmp_path)
    result = edit(tmp_path, path, "cad.batch_apply", operations=[
        {"operation": "cad.entity_move", "entity_ids": ["e00001"], "delta": [4, 0, 0]},
        {"operation": "cad.entity_delete", "entity_ids": ["e00002"]},
        {"operation": "cad.entity_create", "entity": {"type": "CIRCLE", "center": {"x": 20, "y": 3}, "radius": 1}},
    ])
    document = CadDocument.open(path)
    entry = document.metadata["operation_history"][-1]
    steps = entry["steps"]
    assert entry["run_id"] == result["run_id"]
    assert steps[0]["changed_ids"] == ["e00001"]
    assert steps[1]["removed_ids"] == ["e00002"]
    assert steps[2]["added_ids"] == [document.entities[-1]["id"]]
    assert len(result["history"]["revisions"]) == 1


def test_mcp_safe_profile_can_read_history(tmp_path):
    path = source(tmp_path)
    revision = edit(tmp_path, path)["run_id"]
    server = MCPServer(EngineeringAgent(), profile="safe")
    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "operation.cad.history_view", "arguments": {
            "input_file": str(path), "project_dir": str(tmp_path), "revision_id": revision}}})
    assert "error" not in result
    content = json.loads(result["result"]["content"][0]["text"])
    assert content["read_only"] is True
    assert content["document"]["entities"][0]["start"]["x"] == 4
