"""File preservation tests use synthetic native bytes, not licensed vendor models."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.formats import write_dxf, write_step
from engineering_agent.ingest import ArtifactReader
from engineering_agent.mcp_server import MCPServer
from engineering_agent.transactions import RunTransaction
from engineering_agent.versioning import VersionStore


def fixtures(root):
    values = {
        "native/part.CATPart": b"opaque CATIA fixture\x00\xff\xfe",
        "native/solve.rst": b"opaque Ansys fixture\x00\xff",
        "native/motion.adm": b"opaque Adams fixture\r\n",
        "native/vehicle.sim": b"opaque VI-grade fixture\x00",
        "results/curve.csv": ("t,force\n" + "".join(f"{i},{i * 2}\n" for i in range(60))).encode(),
        "results/report.json": b'{"label":"original","values":[1,2,3],"passed":false}',
    }
    for name, content in values.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    write_step(root / "part.step", [(0, 0, 0), (4, 5, 6)])
    values["part.step"] = (root / "part.step").read_bytes()
    write_dxf(root / "part.dxf", lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    EngineeringAgent().execute_operation("cad.entity_move", {"project_dir": str(root), "input_file": "part.dxf",
        "output_file": "part.dxf", "entity_ids": ["e00001"], "delta": [2, 0, 0]})
    values["part.dxf"] = (root / "part.dxf").read_bytes()
    return values


def test_original_files_survive_source_deletion_and_export_with_sidecars(tmp_path):
    originals = fixtures(tmp_path)
    originals["part.dxf.cad.json"] = (tmp_path / "part.dxf.cad.json").read_bytes()
    store = VersionStore(tmp_path)
    saved = store.ingest_and_create("raw files", [p for p in originals if not p.endswith(".cad.json")])
    assert saved["schema_version"] == 2 and saved["storage"] == "original_files"
    for name in originals:
        (tmp_path / name).unlink()
    for entry in saved["files"]:
        if entry["exists"]:
            with VersionStore(tmp_path).open_file(saved["id"], entry["path"]) as (stream, checked):
                content = stream.read()
                assert content == originals[entry["path"]]
                assert hashlib.sha256(content).hexdigest() == checked["sha256"]
    csv_index = saved["asset_paths"].index("results/curve.csv")
    assert "60 行" in store.preview(saved["id"], csv_index)["svg"]
    cad_index = saved["asset_paths"].index("part.dxf")
    preview = store.preview(saved["id"], cad_index)
    assert preview["asset"]["cad_document"]["entities"][0]["start"]["x"] == 2
    assert "data-read-only='true'" in preview["viewer"]
    exported = EngineeringAgent().execute_operation("project.version_export", {
        "project_dir": str(tmp_path), "version_id": saved["id"], "output_dir": "recovered/old"})
    assert exported["validation"]["valid"]
    for name, content in originals.items():
        assert (tmp_path / "recovered/old" / name).read_bytes() == content
        assert not (tmp_path / name).exists()


def test_nested_duplicate_names_remain_distinct_and_semantic_versions_still_work(tmp_path):
    for folder in ("first", "second"):
        (tmp_path / folder).mkdir(); (tmp_path / folder / "model.json").write_text(json.dumps({"label": folder}))
    store = VersionStore(tmp_path)
    saved = store.ingest_and_create("pair", ["first/model.json", "second/model.json"])
    assert len(saved["files"]) == 2
    legacy = store.create("legacy", [ArtifactReader().read(tmp_path / "first/model.json")])
    assert store.load(legacy["id"])["label"] == "legacy"
    with pytest.raises(ValueError, match="VERSION_RAW_FILES_UNAVAILABLE"):
        store.export(legacy["id"], "legacy-export")
    with pytest.raises(ValueError, match="VERSION_RAW_FILES_UNAVAILABLE"):
        with store.open_file(legacy["id"], "first/model.json"):
            pass


@pytest.mark.parametrize("action", ["changed", "deleted", "sidecar_added"])
def test_source_change_during_capture_aborts_whole_version(tmp_path, monkeypatch, action):
    import engineering_agent.versioning as versioning
    path = tmp_path / "part.dxf"
    write_dxf(path, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    copy = versioning.shutil.copyfile
    def raced(source, destination):
        result = copy(source, destination)
        if action == "changed": path.write_bytes(path.read_bytes() + b"\n")
        elif action == "deleted": path.unlink()
        else: Path(str(path) + ".cad.json").write_text("{}")
        return result
    monkeypatch.setattr(versioning.shutil, "copyfile", raced)
    store = VersionStore(tmp_path)
    with pytest.raises(RuntimeError, match="VERSION_SOURCE_CONFLICT"):
        store.ingest_and_create("raced", [path])
    assert store.list() == [] and store.timeline() == []
    assert not (tmp_path / ".engineering-agent/project.lock").exists()


def test_snapshot_validation_failure_rolls_back_manifest_files_and_timeline(tmp_path, monkeypatch):
    path = tmp_path / "data.json"; path.write_text('{"value": 1}')
    store = VersionStore(tmp_path)
    old = store.ingest_and_create("first", [path])
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    def reject(*args):
        raise RuntimeError("readback failed")
    monkeypatch.setattr(store, "_verify_files", reject)
    with pytest.raises(RuntimeError, match="readback failed"):
        store.ingest_and_create("second", [path], old["id"])
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("change", ["primary", "sidecar", "unexpected_sidecar"])
def test_corrupt_snapshot_refuses_preview_download_or_export(tmp_path, change):
    values = fixtures(tmp_path)
    store = VersionStore(tmp_path); saved = store.ingest_and_create("copy", list(values))
    relative = {"primary": "part.dxf", "sidecar": "part.dxf.cad.json", "unexpected_sidecar": "part.step.brep.json"}[change]
    bad = store.root / saved["id"] / "files" / relative
    bad.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="VERSION_SNAPSHOT_CORRUPT"):
        store.export(saved["id"], "recovered")
    assert not (tmp_path / "recovered").exists()
    target = "part.step" if change == "unexpected_sidecar" else "part.dxf"
    with pytest.raises(RuntimeError, match="VERSION_SNAPSHOT_CORRUPT"):
        store.preview(saved["id"], saved["asset_paths"].index(target))
    if change != "unexpected_sidecar":
        with pytest.raises(RuntimeError, match="VERSION_SNAPSHOT_CORRUPT"):
            with store.open_file(saved["id"], relative): pass


def test_export_rejects_existing_destination_and_external_creation_race(tmp_path, monkeypatch):
    path = tmp_path / "data.json"; path.write_text('{"metric": 1}')
    store = VersionStore(tmp_path); saved = store.ingest_and_create("first", [path])
    (tmp_path / "existing").mkdir(); (tmp_path / "existing/data.json").write_text("keep me")
    with pytest.raises(FileExistsError, match="VERSION_EXPORT_DESTINATION_EXISTS"):
        store.export(saved["id"], "existing")
    commit = RunTransaction.commit
    def raced(self, generated, destination_root=None, **kwargs):
        Path(destination_root).mkdir(parents=True)
        (Path(destination_root) / "data.json").write_text("external file")
        return commit(self, generated, destination_root, **kwargs)
    monkeypatch.setattr(RunTransaction, "commit", raced)
    with pytest.raises(RuntimeError, match="TRANSACTION_CONFLICT"):
        store.export(saved["id"], "raced")
    assert (tmp_path / "raced/data.json").read_text() == "external file"
    assert path.read_text() == '{"metric": 1}'


def test_partial_export_failure_restores_entire_file_group(tmp_path, monkeypatch):
    for name in ("one.json", "two.json"): (tmp_path / name).write_text('{"value": 1}')
    store = VersionStore(tmp_path); saved = store.ingest_and_create("pair", ["one.json", "two.json"])
    original = RunTransaction._install
    def failed(self, entry, target, side):
        if side == "after" and target.name == "two.json" and "exported" in target.parts:
            raise OSError("simulated disk failure")
        return original(self, entry, target, side)
    monkeypatch.setattr(RunTransaction, "_install", failed)
    with pytest.raises(OSError, match="disk failure"): store.export(saved["id"], "exported")
    assert not list((tmp_path / "exported").rglob("*.json"))


def test_process_crash_recovers_snapshot_group(tmp_path):
    (tmp_path / "data.json").write_text('{"value": 1}')
    result = subprocess.run([sys.executable, "-c", '''
import os,sys
from pathlib import Path
from engineering_agent.transactions import RunTransaction
from engineering_agent.versioning import VersionStore
original=RunTransaction._install
def crash(self,entry,target,side):
    original(self,entry,target,side)
    if side=="after": os._exit(23)
RunTransaction._install=crash
VersionStore(sys.argv[1]).ingest_and_create("crash",["data.json"])
''', str(tmp_path)], capture_output=True, timeout=25)
    assert result.returncode == 23, result.stderr
    store = VersionStore(tmp_path)
    assert store.list() == [] and store.timeline() == []
    assert not list(store.root.rglob("*.json"))
    assert (tmp_path / "data.json").read_text() == '{"value": 1}'


@pytest.mark.parametrize("relative", ["../escape", ".engineering-agent/file", "artifacts/runs/file"])
def test_source_and_export_paths_are_confined(tmp_path, relative):
    store = VersionStore(tmp_path)
    with pytest.raises(ValueError): store.ingest_and_create("escape", [relative])
    with pytest.raises(ValueError): store.export("unused", relative)
    with pytest.raises(ValueError, match="VERSION_INVALID_ID"): store.load("../outside")


def test_symlink_archive_rejected_but_current_source_replacement_does_not_break_history(tmp_path):
    source = tmp_path / "data.json"; source.write_text('{"value": 1}')
    store = VersionStore(tmp_path); saved = store.ingest_and_create("first", [source])
    target = tmp_path / "other.json"; target.write_text('{"value": 9}')
    source.unlink()
    try: source.symlink_to(target)
    except OSError: pytest.skip("symlink creation unavailable")
    assert store.preview(saved["id"])["asset"]["metrics"]["value"] == 1
    with pytest.raises(ValueError, match="SYMLINK"): store.ingest_and_create("linked", [source])
    archived = store.root / saved["id"] / "files/data.json"
    archived.unlink(); archived.symlink_to(target)
    with pytest.raises(ValueError, match="SYMLINK"): store.export(saved["id"], "out")


def test_mcp_and_cli_use_verified_export(tmp_path):
    path = tmp_path / "part.CATPart"; path.write_bytes(b"synthetic opaque fixture\0\xff")
    saved = VersionStore(tmp_path).ingest_and_create("native", [path])
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "operation.project.version_export", "arguments": {"project_dir": str(tmp_path),
        "version_id": saved["id"], "output_dir": "mcp-copy"}}}
    denied = MCPServer(EngineeringAgent(), profile="safe").handle(call)
    assert denied["result"]["isError"] is True
    assert denied["result"]["structuredContent"]["error"]["code"] == "INVALID_ARGUMENTS"
    assert "result" in MCPServer(EngineeringAgent(), profile="full").handle(call)
    result = subprocess.run([sys.executable, "-m", "engineering_agent.cli", "version-export", saved["id"],
                             "cli-copy", "--root", str(tmp_path)], capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["validation"]["valid"]
    assert (tmp_path / "cli-copy/part.CATPart").read_bytes() == path.read_bytes()


def test_cli_version_keeps_working_directory_relative_input_compatibility(tmp_path):
    root = tmp_path / "project"; root.mkdir(); (root / "input.json").write_text('{"value": 1}')
    result = subprocess.run([sys.executable, "-m", "engineering_agent.cli", "version", "initial", "project/input.json",
                             "--root", "project"], cwd=tmp_path, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["asset_paths"] == ["input.json"]


@pytest.mark.parametrize("content", [b'{"unfinished":', b'{"value": NaN}'])
def test_unparseable_results_keep_raw_evidence_and_honest_preview(tmp_path, content):
    path = tmp_path / "failed-result.json"; path.write_bytes(content)
    store = VersionStore(tmp_path); saved = store.ingest_and_create("failed solve evidence", [path])
    assert saved["objects"][0]["metadata"]["parse_status"] == "unavailable"
    json.dumps(saved, allow_nan=False)
    assert store.preview(saved["id"])["preview_unavailable"]
    assert "原始文件可下载或导出" in store.preview(saved["id"])["svg"]
    exported = store.export(saved["id"], "evidence-copy")
    assert exported["validation"]["valid"]
    assert (tmp_path / "evidence-copy" / path.name).read_bytes() == content


def test_render_failure_does_not_discard_original_files(tmp_path, monkeypatch):
    import engineering_agent.viewer as viewer
    path = tmp_path / "result.json"; path.write_text('{"value": 3}')
    store = VersionStore(tmp_path); saved = store.ingest_and_create("original", [path])
    def unavailable(*args, **kwargs): raise ValueError("fixture renderer unavailable <unsupported>")
    monkeypatch.setattr(viewer, "_asset_view", unavailable)
    result = store.preview(saved["id"])
    assert result["preview_unavailable"]
    assert "&lt;unsupported&gt;" in result["svg"]
    with store.open_file(saved["id"], path.name) as (stream, _): assert stream.read() == path.read_bytes()


@pytest.mark.parametrize("name", ["engineering_run", "engineering_generate", "engineering_offline_run", "engineering_version",
    "operation.cad.entity_move", "operation.catia.inspect_model", "operation.project.version_export"])
def test_safe_profile_rejects_direct_mutation_calls_not_just_discovery(tmp_path, name):
    server = MCPServer(EngineeringAgent(), profile="safe")
    assert name not in {tool["name"] for tool in server._tools()}
    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": name, "arguments": {"project_dir": str(tmp_path)}}})
    assert result["result"]["isError"] is True
    assert "MCP_PROFILE_READ_ONLY" in result["result"]["structuredContent"]["error"]["message"]
    assert list(tmp_path.iterdir()) == []
