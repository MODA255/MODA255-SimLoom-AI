import json
import math
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.brep import get_brep_backend
from engineering_agent.desktop import LocalApplication, launch
from engineering_agent.queue import TaskQueue
from engineering_agent.viewer import artifact_listing, asset_view, mesh_payload, render_csv_svg
from engineering_agent.web import create_server
from engineering_agent.workspace import Workspace


@pytest.fixture
def service(tmp_path):
    queue = TaskQueue(tmp_path / "workspace.db")
    agent = EngineeringAgent({"tools": {}})
    server = create_server(agent, queue, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None, raw=None, headers=None):
        payload = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(base + path, data=payload, headers=headers or {"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            if response.headers.get_content_type() == "application/json":
                data = json.loads(data)
            return response.status, data
    yield call, queue, agent
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_project_wizard_offline_history_and_download(service, tmp_path):
    call, queue, _ = service
    status, project = call("/api/projects", {"name": "悬架 A", "template": "offline-vehicle",
                                          "parameters": {"wheelbase_mm": 3200, "force_n": 1000}})
    assert status == 201
    root = Path(project["project_dir"])
    assert root.parent == tmp_path / "projects"
    assert json.loads((root / "simloom.project.json").read_text())["model_spec"]["parameters"]["wheelbase_mm"] == 3200
    # Reopen a new DB connection, as a restarted launcher would.
    assert TaskQueue(queue.path).get_project(project["id"])["project_dir"] == str(root)
    status, result = call("/api/offline-run", {"project_id": project["id"]})
    assert status == 200 and result["status"] == "committed"
    assert result["validation_passed"]
    specification = json.loads(Path(result["artifacts"]["model_spec.json"]).read_text())
    assert specification["load_cases"][0]["force_n"] == 1000
    status, tasks = call("/api/tasks?project_id=" + project["id"])
    assert len(tasks) == 1 and tasks[0]["status"] == "completed"
    assert tasks[0]["result"]["task_id"] == result["task_id"]
    _, events = call("/api/tasks/" + result["task_id"] + "/events")
    assert [e["event"] for e in events] == ["queued", "running", "completed"]
    query = urllib.parse.urlencode({"root": str(root), "path": "artifacts/generated/offline/adams_motion.csv"})
    status, view = call("/api/asset-view?" + query)
    assert status == 200 and "polyline" in view["svg"] and "41 行" in view["svg"]
    status, content = call("/api/download?" + query)
    assert status == 200 and b"yaw_rate" in content
    query = urllib.parse.urlencode({"root": str(root), "path": "../../workspace.db"})
    assert call("/api/download?" + query)[0] == 400


def test_project_parameters_errors_and_duplicate_directory(service, tmp_path):
    call, _, _ = service
    for body in ({"name": ""}, {"name": "bad", "parameters": {"wheelbase_mm": -1}},
                 {"name": "bad", "template": "unknown"}, {"name": "bad", "parameters": []}):
        status, error = call("/api/projects", body)
        assert status == 400 and error["code"] == "INVALID_ARGUMENTS"
    root = tmp_path / "named-project"
    body = {"name": "original", "project_dir": str(root)}
    assert call("/api/projects", body)[0] == 201
    before = (root / "simloom.project.json").read_bytes()
    assert call("/api/projects", {**body, "name": "replacement"})[0] == 409
    assert (root / "simloom.project.json").read_bytes() == before
    assert not (root / ".engineering-agent/project.lock").exists()


def test_api_errors_do_not_disconnect_clients(service):
    call, _, _ = service
    assert call("/api/projects", raw=b"{")[0] == 400
    assert call("/api/projects", raw=b"[]")[0] == 400
    assert call("/api/projects", {"name": "cross-site"}, headers={"Content-Type": "application/json", "Origin": "https://example.com"})[0] == 403
    assert call("/api/offline-run", {"project_id": "missing"})[0] == 404
    assert call("/api/health")[1] == {"ok": True}


def test_offline_failure_is_recorded_and_retry_works(service, monkeypatch):
    call, queue, agent = service
    _, project = call("/api/projects", {"name": "failure-demo"})
    original = agent.run_offline_vehicle_chain
    def fail(*_args):
        raise RuntimeError("simulated kernel failure")
    monkeypatch.setattr(agent, "run_offline_vehicle_chain", fail)
    status, error = call("/api/offline-run", {"project_id": project["id"]})
    assert status == 500 and "kernel failure" in error["error"]
    assert queue.list()[0]["status"] == "failed"
    monkeypatch.setattr(agent, "run_offline_vehicle_chain", original)
    assert call("/api/offline-run", {"project_id": project["id"]})[0] == 200


def test_saved_rehearsal_executes_once(service):
    call, _, _ = service
    _, project = call("/api/projects", {"name": "排练"})
    _, task = call("/api/tasks", {"project_id": project["id"], "prompt": "用 CATIA 建模"})
    path = "/api/tasks/" + task["id"] + "/run"
    status, result = call(path, {})
    assert status == 200 and all(r["status"] == "planned" for r in result["results"])
    assert call(path, {})[0] == 409


def test_legacy_project_database_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT,created_at TEXT)")
        db.execute("INSERT INTO projects VALUES ('old','legacy','2026-11-05')")
    queue = TaskQueue(path)
    assert queue.get_project("old")["name"] == "legacy"
    new = Workspace(queue).create_project({"name": "new"})
    assert new["project_dir"] and len(queue.list_projects()) == 2


def test_mesh_camera_normalization_and_scalar_range():
    asset = {"geometry": {"vertices": [{"x": 9000, "y": 100, "z": 200}, {"x": 12000, "y": 100, "z": 200}, {"x": 9000, "y": 200, "z": 200}],
                          "faces": [[0, 1, 2]], "scalars": [100, 200, 300]}}
    data = mesh_payload(asset)
    assert all(math.sqrt(sum(v*v for v in p)) <= .800001 for p in data["points"])
    assert data["scalar_range"] == [100, 300]
    assert data["colors"][0] == [0, 1, 1] and data["colors"][-1] == [1, 1, 0]
    asset["geometry"]["scalars"] = [1]
    with pytest.raises(ValueError, match="每个顶点"):
        mesh_payload(asset)


def test_preview_preserves_indices_above_65535():
    vertices = [{"x": float(i), "y": i % 2, "z": 0} for i in range(65538)]
    data = mesh_payload({"geometry": {"vertices": vertices, "faces": [[65535, 65536, 65537]]}})
    assert data["indices"] == [65535, 65536, 65537]


def test_step_preview_contains_real_box_faces_when_kernel_available(tmp_path):
    backend = get_brep_backend()
    if backend.name != "opencascade":
        pytest.skip("optional OpenCascade binding unavailable")
    path = tmp_path / "box.step"
    backend.create_primitive("box", {"length": 2800, "width": 1600, "height": 120}, path)
    view = asset_view(tmp_path, "box.step")
    geometry = view["asset"]["geometry"]
    assert geometry["triangle_count"] == 12
    assert max(p["x"] for p in geometry["vertices"]) == pytest.approx(2800)
    assert "engineering-3d-viewer" in view["viewer"]
    assert "STEP 曲面网格" in view["preview_note"]


def test_step_preview_marks_unavailable_kernel(tmp_path, monkeypatch):
    from engineering_agent import viewer
    (tmp_path / "part.step").write_text("#1=CARTESIAN_POINT('',(0.,0.,0.));#2=CARTESIAN_POINT('',(2.,3.,4.));")
    monkeypatch.setattr(viewer, "get_brep_backend", lambda: SimpleNamespace(name="unavailable"))
    result = asset_view(tmp_path, "part.step")
    assert "仅显示 STEP 包围盒" in result["preview_note"]
    assert "engineering-3d-viewer" not in result["viewer"]


def test_result_preview_escapes_labels_and_uses_full_csv(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps({"<script>bad()</script>": 3}))
    assert "<script>bad" not in asset_view(tmp_path, "result.json")["svg"]
    csv = tmp_path / "motion.csv"
    csv.write_text("time_s,yaw\n" + "\n".join(f"{i},{i*10}" for i in range(50)))
    assert "50 行" in render_csv_svg(csv) and "490" in render_csv_svg(csv)


def test_asset_listing_excludes_internal_copies_and_symlinks(tmp_path):
    for name in ("artifacts/generated/model.obj", "artifacts/runs/one/stage/model.obj", ".git/config"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("data")
    assert [v["path"] for v in artifact_listing(tmp_path)] == ["artifacts/generated/model.obj"]


def test_local_launcher_server_lifecycle_and_user_data(tmp_path):
    with LocalApplication(EngineeringAgent({"tools": {}}), tmp_path / "User Data") as app:
        with urllib.request.urlopen(app.url + "/api/workspace") as response:
            assert json.load(response)["projects_root"] == str(tmp_path / "User Data/projects")
        assert (tmp_path / "User Data/workspace.db").is_file()
        thread = app.thread
    assert not thread.is_alive()


def test_native_launcher_passes_live_local_url_and_stops_server(tmp_path, monkeypatch):
    from engineering_agent import desktop
    called = []
    def create_window(title, url, **kwargs):
        with urllib.request.urlopen(url + "/api/health") as response:
            assert json.load(response)["ok"]
        called.append((title, kwargs["width"]))
    fake = SimpleNamespace(create_window=create_window, start=lambda **kw: called.append(kw["private_mode"]))
    monkeypatch.setattr(desktop.importlib, "import_module", lambda _: fake)
    assert launch(EngineeringAgent(), data_dir=tmp_path, mode="native") == 0
    assert called == [("SimLoom AI", 1440), False]
