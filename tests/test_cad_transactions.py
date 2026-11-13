"""File-level evidence for crash recovery, contention and cross-session undo."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf
from engineering_agent.transactions import RunTransaction


def source(root):
    path = root / "图纸 test.dxf"
    write_dxf(path, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    return path


def edit(root, path, delta=1):
    return EngineeringAgent().execute_operation("cad.entity_move", {
        "project_dir": str(root), "input_file": path.name, "output_file": path.name,
        "entity_ids": ["e00001"], "delta": [delta, 0, 0], "auto_repair": False})


def pair(path):
    sidecar = Path(str(path) + ".cad.json")
    return (path.read_bytes() if path.exists() else None, sidecar.read_bytes() if sidecar.exists() else None)


def invoke(root, code, *args):
    return subprocess.run([sys.executable, "-c", code, str(root), *args], capture_output=True, text=True, timeout=25, cwd=root)


def cli(root, operation, path):
    result = subprocess.run([sys.executable, "-m", "engineering_agent.cli", "--project-dir", str(root),
                             "cad-" + operation, path.name], capture_output=True, text=True, timeout=25, cwd=root)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_cross_process_undo_redo_restores_exact_dxf_and_metadata(tmp_path):
    path = source(tmp_path)
    original = pair(path)
    first = edit(tmp_path, path, 2)
    first_bytes = pair(path)
    second = edit(tmp_path, path, 3)
    second_bytes = pair(path)
    undo = cli(tmp_path, "undo", path)
    assert undo["restored_revision"] == second["run_id"]
    assert pair(path) == first_bytes
    undo = cli(tmp_path, "undo", path)
    assert undo["restored_revision"] == first["run_id"]
    assert pair(path) == original
    assert not undo["history"]["can_undo"]
    assert cli(tmp_path, "history", path)["history"]["can_redo"]
    cli(tmp_path, "redo", path)
    assert pair(path) == first_bytes
    cli(tmp_path, "redo", path)
    assert pair(path) == second_bytes
    assert not cli(tmp_path, "history", path)["history"]["can_redo"]


def test_artifact_creation_is_undoable_and_redoable(tmp_path):
    path = tmp_path / "new.dxf"
    EngineeringAgent().write_asset(str(path), "dxf", {"lines": [{"x1": 0, "y1": 0, "x2": 4, "y2": 0}]}, project_dir=tmp_path)
    saved = pair(path)
    result = cli(tmp_path, "undo", path)
    assert result["document"] is None
    assert pair(path) == (None, None)
    cli(tmp_path, "redo", path)
    assert pair(path) == saved


def test_undo_repair_restores_original_invalid_geometry(tmp_path):
    path = tmp_path / "broken.dxf"
    write_dxf(path, lines=[{"x1": 1, "y1": 1, "x2": 1, "y2": 1}])
    original = pair(path)
    EngineeringAgent().execute_operation("cad.validate_and_repair", {
        "project_dir": str(tmp_path), "input_file": str(path), "output_file": str(path)})
    result = cli(tmp_path, "undo", path)
    assert pair(path) == original
    assert result["persisted_validation"]["valid"] is False


@pytest.mark.parametrize("which", ["dxf", "sidecar"])
def test_undo_rejects_external_edits_without_changing_any_file(tmp_path, which):
    path = source(tmp_path)
    edit(tmp_path, path)
    target = path if which == "dxf" else Path(str(path) + ".cad.json")
    target.write_bytes(target.read_bytes() + b"\n ")
    external = pair(path)
    agent = EngineeringAgent()
    with pytest.raises(RuntimeError, match="CAD_HISTORY_CONFLICT"):
        agent.execute_operation("cad.undo", {"project_dir": str(tmp_path), "input_file": str(path)})
    assert pair(path) == external
    assert not (tmp_path / ".engineering-agent/project.lock").exists()


def test_new_edit_after_undo_discards_redo_branch(tmp_path):
    path = source(tmp_path)
    edit(tmp_path, path, 2)
    edit(tmp_path, path, 3)
    cli(tmp_path, "undo", path)
    result = edit(tmp_path, path, 7)
    assert len(result["history"]["revisions"]) == 2
    assert not result["history"]["can_redo"]
    assert CadDocument.open(path).entities[0]["start"]["x"] == 9
    with pytest.raises(ValueError, match="CAD_NOTHING_TO_REDO"):
        EngineeringAgent().execute_operation("cad.redo", {"project_dir": str(tmp_path), "input_file": str(path)})


def test_corrupt_snapshot_cannot_partially_restore_drawing(tmp_path):
    path = source(tmp_path)
    edit(tmp_path, path, 1)
    result = edit(tmp_path, path, 2)
    before = pair(path)
    backup = tmp_path / "artifacts/runs" / result["run_id"] / "backup" / (path.name + ".cad.json")
    backup.write_bytes(b"damaged")
    with pytest.raises(RuntimeError, match="CAD_HISTORY_SNAPSHOT_CORRUPT"):
        EngineeringAgent().execute_operation("cad.undo", {"project_dir": str(tmp_path), "input_file": str(path)})
    assert pair(path) == before


@pytest.mark.parametrize("raises", [False, True])
def test_disk_validation_failure_restores_drawing_metadata_and_history(tmp_path, monkeypatch, raises):
    path = source(tmp_path)
    edit(tmp_path, path)
    before = pair(path)
    history_file = next((tmp_path / ".engineering-agent/cad-history").glob("*.json"))
    before_history = history_file.read_bytes()
    agent = EngineeringAgent()
    def reject(*args, **kwargs):
        assert (tmp_path / ".engineering-agent/project.lock").exists()
        if raises:
            raise OSError("readback failed")
        return {"valid": False, "errors": [{"code": "READBACK_LOSS"}]}
    monkeypatch.setattr(agent.cad, "_validate_persisted", reject)
    with pytest.raises((ValueError, OSError)):
        agent.execute_operation("cad.entity_move", {"project_dir": str(tmp_path), "input_file": str(path),
            "output_file": str(path), "entity_ids": ["e00001"], "delta": [8, 0, 0]})
    assert pair(path) == before
    assert history_file.read_bytes() == before_history
    assert not (tmp_path / ".engineering-agent/project.lock").exists()


def test_lock_covers_document_read_and_persisted_validation(tmp_path, monkeypatch):
    path = source(tmp_path)
    agent = EngineeringAgent()
    original_open = agent.cad.open
    observed = []
    def checked_open(*args, **kwargs):
        assert (tmp_path / ".engineering-agent/project.lock").exists()
        observed.append(str(args[0]))
        return original_open(*args, **kwargs)
    monkeypatch.setattr(agent.cad, "open", checked_open)
    original_validate = agent.cad._validate_persisted
    def checked_validate(*args, **kwargs):
        rival = invoke(tmp_path, '''
import sys
from engineering_agent.transactions import RunTransaction
try:
    RunTransaction(sys.argv[1]).begin({"competing": True})
except RuntimeError as exc:
    assert str(exc) == "PROJECT_LOCKED"
else:
    raise AssertionError("writer entered before validation ended")
''')
        assert rival.returncode == 0, rival.stderr
        return original_validate(*args, **kwargs)
    monkeypatch.setattr(agent.cad, "_validate_persisted", checked_validate)
    agent.execute_operation("cad.entity_move", {"project_dir": str(tmp_path), "input_file": str(path),
        "output_file": str(path), "entity_ids": ["e00001"], "delta": [1, 0, 0]})
    assert len(observed) >= 2


def test_failure_during_second_file_replacement_restores_first(tmp_path, monkeypatch):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("old a"); b.write_text("old b")
    transaction = RunTransaction(tmp_path)
    transaction.begin({"test": "replace failure"})
    sa, sb = transaction.stage_dir / "a", transaction.stage_dir / "b"
    sa.write_text("new a"); sb.write_text("new b")
    original = transaction._install
    def fail_second(entry, target, side):
        if target == b and side == "after":
            raise OSError("disk failed")
        return original(entry, target, side)
    monkeypatch.setattr(transaction, "_install", fail_second)
    with pytest.raises(OSError, match="disk failed"):
        transaction.commit({"a.txt": str(sa), "b.txt": str(sb)}, destination_root=tmp_path)
    assert (a.read_text(), b.read_text()) == ("old a", "old b")
    assert transaction.state == "failed"


@pytest.mark.parametrize("phase", ["first_file", "validation", "rollback", "after_commit"])
def test_process_crash_recovers_entire_group_before_next_transaction(tmp_path, phase):
    (tmp_path / "a.txt").write_text("old a")
    result = invoke(tmp_path, '''
import os,sys
from pathlib import Path
from engineering_agent.transactions import RunTransaction
root=Path(sys.argv[1]); phase=sys.argv[2]
t=RunTransaction(root); t.begin({"crash":phase})
a=t.stage_dir/"a"; b=t.stage_dir/"b"
a.write_text("new a"); b.write_text("new b")
install=t._install
count=0
def interrupted(entry,target,side):
    global count
    install(entry,target,side)
    count+=1
    if phase=="first_file" and count==1: os._exit(23)
    if phase=="rollback" and side=="before": os._exit(23)
t._install=interrupted
def validate(files):
    if phase=="validation": os._exit(23)
    if phase=="rollback": raise ValueError("reject")
t.commit({"a.txt":str(a),"b.txt":str(b)},destination_root=root,validate=validate)
os._exit(23)
''', phase)
    assert result.returncode == 23, result.stderr
    with RunTransaction(tmp_path) as transaction:
        transaction.begin({"after crash": True})
        if phase == "after_commit":
            assert (tmp_path / "a.txt").read_text() == "new a"
            assert (tmp_path / "b.txt").read_text() == "new b"
        else:
            assert (tmp_path / "a.txt").read_text() == "old a"
            assert not (tmp_path / "b.txt").exists()
            states = [json.loads(p.read_text())["state"] for p in (tmp_path / "artifacts/runs").glob("*/state.json")]
            assert "recovered" in states
        transaction.rollback("inspection complete")


def test_live_lock_does_not_expire_during_long_job(tmp_path):
    with RunTransaction(tmp_path) as transaction:
        transaction.begin({"long job": True})
        old = time.time() - 7200
        os.utime(transaction.lock_path, (old, old))
        with pytest.raises(RuntimeError, match="PROJECT_LOCKED"):
            RunTransaction(tmp_path).begin({"rival": True})
        with pytest.raises(RuntimeError, match="PROJECT_LOCKED"):
            RunTransaction.recover_stale(tmp_path, 0)
        assert transaction.lock_path.exists()
        transaction.rollback("finished")


@pytest.mark.parametrize("relative", ["../outside.txt", "/outside.txt", ".engineering-agent/project.guard"])
def test_commit_rejects_unsafe_targets(tmp_path, relative):
    transaction = RunTransaction(tmp_path)
    transaction.begin({"invalid target": True})
    staged = transaction.stage_dir / "data"; staged.write_text("data")
    with pytest.raises(ValueError):
        transaction.commit({relative: str(staged)}, destination_root=tmp_path)
    assert not transaction.lock_path.exists()


def test_late_rollback_does_not_overwrite_newer_commit(tmp_path):
    path = tmp_path / "a.txt"; path.write_text("original")
    first = RunTransaction(tmp_path); first.begin({})
    staged = first.stage_dir / "a"; staged.write_text("first")
    first.commit({"a.txt": str(staged)}, destination_root=tmp_path)
    second = RunTransaction(tmp_path); second.begin({})
    staged = second.stage_dir / "a"; staged.write_text("second")
    second.commit({"a.txt": str(staged)}, destination_root=tmp_path)
    with pytest.raises(RuntimeError, match="TRANSACTION_CONFLICT"):
        first.rollback("too late")
    assert path.read_text() == "second"


def test_interrupted_validation_restores_and_releases_lock(tmp_path):
    path = tmp_path / "a.txt"; path.write_text("original")
    transaction = RunTransaction(tmp_path); transaction.begin({})
    staged = transaction.stage_dir / "a"; staged.write_text("changed")
    def interrupt(_files):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        transaction.commit({"a.txt": str(staged)}, destination_root=tmp_path, validate=interrupt)
    assert path.read_text() == "original"
    with RunTransaction.read_lock(tmp_path):
        assert path.read_text() == "original"


def test_cad_crash_recovers_history_and_drawing_together(tmp_path):
    path = source(tmp_path)
    original = pair(path)
    result = invoke(tmp_path, '''
import os,sys
from pathlib import Path
from engineering_agent import EngineeringAgent
from engineering_agent.transactions import RunTransaction
root=Path(sys.argv[1]); path=root/sys.argv[2]
install=RunTransaction._install
def crash(self,entry,target,side):
    install(self,entry,target,side)
    if target.suffix==".dxf": os._exit(24)
RunTransaction._install=crash
EngineeringAgent().execute_operation("cad.entity_move",{"project_dir":str(root),"input_file":str(path),"output_file":str(path),"entity_ids":["e00001"],"delta":[5,0,0]})
''', path.name)
    assert result.returncode == 24, result.stderr
    history = cli(tmp_path, "history", path)["history"]
    assert pair(path) == original
    assert history["revisions"] == []
    assert not history["can_undo"]
    edit(tmp_path, path, 2)
    assert cli(tmp_path, "history", path)["history"]["can_undo"]


def test_recovery_conflict_preserves_external_bytes_and_blocks_new_write(tmp_path):
    path = tmp_path / "a.txt"; path.write_text("original")
    result = invoke(tmp_path, '''
import os,sys
from pathlib import Path
from engineering_agent.transactions import RunTransaction
root=Path(sys.argv[1]); t=RunTransaction(root); t.begin({})
p=t.stage_dir/"a";p.write_text("changed")
t.commit({"a.txt":str(p)},destination_root=root,validate=lambda files:os._exit(25))
''')
    assert result.returncode == 25, result.stderr
    path.write_text("external edit after crash")
    with pytest.raises(RuntimeError, match="TRANSACTION_RECOVERY_CONFLICT"):
        RunTransaction(tmp_path).begin({"new writer": True})
    assert path.read_text() == "external edit after crash"
    assert not (tmp_path / ".engineering-agent/project.lock").exists()


def test_mcp_undo_and_conflict_are_exposed(tmp_path):
    from engineering_agent.mcp_server import MCPServer
    path = source(tmp_path)
    original = pair(path)
    edit(tmp_path, path, 2)
    server = MCPServer(EngineeringAgent(), profile="full")
    def call(name):
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "operation.cad." + name, "arguments": {"input_file": str(path), "project_dir": str(tmp_path)}}})
    result = call("undo")
    assert "error" not in result
    assert pair(path) == original
    assert "error" not in call("redo")
    path.write_bytes(path.read_bytes() + b"\n")
    conflict = call("undo")
    assert conflict["result"]["isError"] is True
    assert conflict["result"]["structuredContent"]["error"]["code"] == "CONFLICT"


def test_asset_preview_shares_the_writer_lock(tmp_path, monkeypatch):
    from engineering_agent.viewer import asset_view
    path = source(tmp_path)
    with RunTransaction(tmp_path) as transaction:
        transaction.begin({})
        with pytest.raises(RuntimeError, match="PROJECT_LOCKED"):
            asset_view(tmp_path, path.name)
        transaction.rollback("finished")
    assert asset_view(tmp_path, path.name)["cad_history"]["cursor"] == 0
