"""Runner and HTTP contract tests. These do not exercise licensed vendor software."""
import hashlib
import json
import sys
import threading
import uuid
from pathlib import Path

import httpx
import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.adapters.base import AdapterContext
from engineering_agent.adapters.vendor import CatiaAdapter
from engineering_agent.models import PlanStep, ToolName
from engineering_agent.software_worker import run_job
from engineering_agent.windows_bridge import WindowsBridge, create_bridge_server
from engineering_agent.windows_control import WindowsAutomation
from engineering_agent.worker_protocol import SCHEMA, WorkerError, decode_files, pack_file, relative_path

RUNNER = '''import hashlib, json, sys, time
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
params = request["parameters"]
mode = params.get("fixture_mode", "ok")
if mode == "timeout": time.sleep(10)
if mode == "exit": sys.exit(17)
if mode == "missing": sys.exit(0)
inputs = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in request["inputs"]}
for name in request["outputs"]:
    target = Path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("" if mode == "empty" else json.dumps({"fixture": True, "action": request["action"], "inputs": inputs, "parameters": params}), encoding="utf-8")
print("fixture runner completed")
'''
COMMAND = [sys.executable, "{script}", "{request_file}"]
ACTIONS = {"catia": "create_or_update_model", "ansys": "mesh_and_solve",
           "adams": "build_and_run_multibody", "vi-grade": "run_vehicle_dynamics"}


@pytest.fixture
def project(tmp_path):
    project = tmp_path / "engineering project 中文"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "runner.py").write_text(RUNNER, encoding="utf-8")
    return project


def config(bridge_url=None, token=None):
    tools = {}
    for tool, action in ACTIONS.items():
        tools[tool] = {"actions": {action: {"script": "scripts/runner.py", "command": COMMAND}}}
        if bridge_url:
            tools[tool].update(bridge_url=bridge_url, bridge_token=token)
    return {"generate_scripts": False, "tools": tools}


def step(**params):
    return PlanStep("make", ToolName.CATIA, ACTIONS["catia"], "fixture task",
                    outputs=["artifacts/model.step"], parameters=params)


def payload(project, **params):
    return CatiaAdapter(config()["tools"]["catia"])._prepare(
        step(**params), AdapterContext(project), config()["tools"]["catia"]["actions"][ACTIONS["catia"]])


@pytest.fixture
def bridge(tmp_path):
    server = create_bridge_server({"root_dir": str(tmp_path / "remote worker"), "port": 0,
                                   "token": "contract-test-token", "commands": {
                                       f"{tool}.{action}": COMMAND for tool, action in ACTIONS.items()}})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", tmp_path / "remote worker"
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_local_script_binding_collects_outputs_and_receipts(project, tmp_path):
    adapter = CatiaAdapter(config()["tools"]["catia"])
    result = adapter.execute(step(load_n=1500), AdapterContext(project, False, workspace_dir=tmp_path / "staging"))
    assert result.status == "completed"
    output = Path(result.details["output_files"]["artifacts/model.step"])
    assert json.loads(output.read_text())["parameters"]["load_n"] == 1500
    assert result.details["output_manifest"][0]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result.details["vendor_validation"] == "not_run"
    assert not (project / "artifacts/model.step").exists()  # Publication is owned by the transaction.


@pytest.mark.parametrize(("mode", "code"), [("missing", "ARTIFACT_MISSING"), ("empty", "OUTPUT_EMPTY"),
                                            ("exit", "SOFTWARE_EXIT_FAILED"), ("timeout", "SOFTWARE_TIMEOUT")])
def test_zero_exit_is_insufficient_and_failures_keep_logs(project, tmp_path, mode, code):
    (project / "artifacts").mkdir()
    (project / "artifacts/model.step").write_text("old output must not count")
    adapter = CatiaAdapter(config()["tools"]["catia"])
    result = adapter.execute(step(fixture_mode=mode), AdapterContext(project, False, timeout_seconds=1,
                                                                   workspace_dir=tmp_path / "stage"))
    assert result.status == "failed" and not result.outputs
    assert result.details["error"]["code"] == code
    assert "stdout" in result.details and "stderr" in result.details
    assert (project / "artifacts/model.step").read_text() == "old output must not count"


def test_unbound_templates_do_not_launch_executable(project):
    result = CatiaAdapter({"executable": sys.executable}).execute(step(), AdapterContext(project, False))
    assert result.status == "unavailable"
    assert result.details["error"]["code"] == "ACTION_BINDING_REQUIRED"


@pytest.mark.parametrize("bad", ["../x", "/absolute", "C:/job", "folder\\x", "folder//x", "a/./b", ".",
                                  "CON.txt", "aux", "part.", "part ", "file:stream", "_logs/stdout.txt",
                                  "artifacts/RUNS/state.json", ".git/config"])
def test_paths_are_portable_and_cannot_write_control_files(bad):
    with pytest.raises(WorkerError):
        relative_path(bad)


def test_worker_rejects_replay_schema_mismatch_and_in_place_outputs(project, tmp_path):
    request = payload(project)
    commands = {"catia.create_or_update_model": COMMAND}
    first = run_job(tmp_path / "worker", request, commands)
    assert first["status"] == "completed"
    assert run_job(tmp_path / "worker", request, commands)["error"]["code"] == "JOB_ALREADY_EXISTS"
    request["schema_version"] = "simloom-ai/worker/v99"
    assert run_job(tmp_path / "worker", request, commands)["error"]["code"] == "WORKER_SCHEMA_UNSUPPORTED"
    request["schema_version"] = SCHEMA
    request["outputs"] = ["scripts/runner.py"]
    assert run_job(tmp_path / "worker", request, commands)["error"]["code"] == "DUPLICATE_ARTIFACT_PATH"


def test_manifest_hash_duplicate_case_and_symlink_checks(project, tmp_path):
    record = pack_file(project, "scripts/runner.py")
    altered = dict(record, sha256="0" * 64)
    with pytest.raises(WorkerError, match="scripts/runner.py"):
        decode_files([altered])
    with pytest.raises(WorkerError, match="DUPLICATE"):
        decode_files([record, dict(record, path="scripts/RUNNER.py")])
    (project / "scripts/link.py").symlink_to(project / "scripts/runner.py")
    with pytest.raises(WorkerError, match="scripts/link.py"):
        pack_file(project, "scripts/link.py")


@pytest.mark.parametrize("corruption", ["empty", "status", "request", "hash", "missing", "extra", "returncode"])
def test_adapter_never_accepts_incomplete_or_forged_success(project, tmp_path, monkeypatch, corruption):
    adapter = CatiaAdapter(config("http://worker")["tools"]["catia"])
    def post(request, timeout):
        body = run_job(tmp_path / "remote", request, {"catia.create_or_update_model": COMMAND})
        if corruption == "empty": return {}
        if corruption == "status": body.pop("status")
        if corruption == "request": body["request_id"] = uuid.uuid4().hex
        if corruption == "hash": body["outputs"][0]["sha256"] = "bad"
        if corruption == "missing": body["outputs"] = []
        if corruption == "extra": body["outputs"].append(dict(body["outputs"][0], path="unexpected.txt"))
        if corruption == "returncode": body["returncode"] = False
        return body
    monkeypatch.setattr(adapter, "_post", post)
    result = adapter.execute(step(), AdapterContext(project, False, workspace_dir=tmp_path / "stage"))
    assert result.status == "failed" and not result.outputs
    assert not (project / "artifacts/model.step").exists()


def test_http_bridge_transfers_script_input_and_result_without_shared_paths(project, bridge, tmp_path):
    url, worker = bridge
    (project / "geometry.step").write_text("input geometry")
    request_step = step()
    request_step.inputs = ["geometry.step"]
    adapter = CatiaAdapter(config(url, "contract-test-token")["tools"]["catia"])
    result = adapter.execute(request_step, AdapterContext(project, False, workspace_dir=tmp_path / "stage"))
    assert result.status == "completed", result.to_dict()
    output = json.loads(Path(result.details["output_files"]["artifacts/model.step"]).read_text())
    assert output["inputs"]["geometry.step"] == hashlib.sha256(b"input geometry").hexdigest()
    job = Path(result.details["job_dir"])
    assert job.is_relative_to(worker) and job != project
    assert (job / "scripts/runner.py").exists()
    assert adapter.doctor()["reachable"] is True
    assert adapter.doctor()["real_machine_verified"] is False


def test_http_bridge_chunks_large_input_without_shared_paths(project, bridge, tmp_path):
    url, worker = bridge
    large = project / "large.step"
    large.write_bytes((b"STEP-DATA-" * ((33 * 1024 * 1024 + 7) // 10))[:33 * 1024 * 1024 + 7])
    request_step = step()
    request_step.inputs = ["large.step"]
    adapter = CatiaAdapter(config(url, "contract-test-token")["tools"]["catia"])
    result = adapter.execute(request_step, AdapterContext(project, False, timeout_seconds=30,
                                                          workspace_dir=tmp_path / "stage"))
    assert result.status == "completed", result.to_dict()
    output = json.loads(Path(result.details["output_files"]["artifacts/model.step"]).read_text())
    assert output["inputs"]["large.step"] == hashlib.sha256(large.read_bytes()).hexdigest()
    transfer_root = worker / "control" / "transfers"
    assert any(path.is_dir() for path in transfer_root.iterdir())


def test_http_bridge_downloads_large_output_in_chunks(project, bridge, tmp_path):
    url, _worker = bridge
    (project / "scripts" / "large_runner.py").write_text(
        "import json, sys\nfrom pathlib import Path\nrequest=json.loads(Path(sys.argv[1]).read_text())\ntarget=Path(request['outputs'][0]); target.parent.mkdir(parents=True, exist_ok=True)\ntarget.write_bytes((b'RESULT-DATA-' * ((33 * 1024 * 1024 + 23) // 12))[:33 * 1024 * 1024 + 11])\n",
        encoding="utf-8")
    cfg = config(url, "contract-test-token")
    cfg["tools"]["catia"]["actions"][ACTIONS["catia"]]["script"] = "scripts/large_runner.py"
    adapter = CatiaAdapter(cfg["tools"]["catia"])
    result = adapter.execute(step(), AdapterContext(project, False, timeout_seconds=60,
                                                     workspace_dir=tmp_path / "stage"))
    assert result.status == "completed", result.to_dict()
    output = Path(result.details["output_files"]["artifacts/model.step"])
    assert output.stat().st_size == 33 * 1024 * 1024 + 11
    assert result.details["output_manifest"][0]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_http_authentication_and_schema_errors_are_explicit(project, bridge):
    url, _ = bridge
    assert httpx.get(url + "/health").status_code == 401
    assert httpx.post(url, json=payload(project)).status_code == 401
    body = httpx.post(url, json={"operation": "health", "schema_version": "old"},
                      headers={"X-Bridge-Token": "contract-test-token"}).json()
    assert body["status"] == "failed" and body["error"]["code"] == "WORKER_SCHEMA_UNSUPPORTED"
    assert CatiaAdapter(config(url, "incorrect")["tools"]["catia"]).doctor()["available"] is False


def test_windows_bridge_capabilities_are_discoverable_without_claiming_real_vendor_validation(bridge):
    url, _ = bridge
    response = httpx.get(url + "/capabilities", headers={"X-Bridge-Token": "contract-test-token"})
    assert response.status_code == 200
    body = response.json()
    assert body["capabilities_schema"] == "simloom-ai/worker-capabilities/v1"
    assert body["tools"]["catia"]["actions"] == ["create_or_update_model"]
    assert body["tools"]["ansys"]["actions"] == ["mesh_and_solve"]
    assert body["real_machine_verified"] is False
    assert httpx.get(url + "/capabilities").status_code == 401


def test_remote_one_software_is_usable_from_non_windows_controller(project, bridge, monkeypatch):
    url, _ = bridge
    cfg = config(url, "contract-test-token")
    cfg["tools"] = {"catia": cfg["tools"]["catia"]}
    monkeypatch.setattr("engineering_agent.windows_control.platform.system", lambda: "Darwin")
    matrix = EngineeringAgent(cfg).capabilities()
    modes = {item["id"]: item for item in matrix["modes"]}
    assert modes["execute"]["ready"] and modes["connected"]["ready"]
    assert not matrix["components"]["windows"]["available"]
    assert not matrix["tools"]["ansys"]["available"]


def test_full_chain_publishes_verified_results_in_one_transaction(project, bridge):
    url, _ = bridge
    agent = EngineeringAgent(config(url, "contract-test-token"))
    report = agent.run("CATIA建模，Ansys应力分析，Adams动力学，VI-grade操稳", project_dir=str(project), parameters={"scenario": "fixture-contract-test"}, dry_run=False)
    assert report.status == "committed", report.to_dict()
    assert len(report.results) == 4 and all(r.status == "completed" for r in report.results)
    for result in report.results:
        for name in result.outputs:
            assert (project / name).is_file()
            assert name in report.generated_files
    assert report.artifact_manifest
    for record in report.artifact_manifest:
        assert hashlib.sha256((project / record["path"]).read_bytes()).hexdigest() == record["sha256"]
    ansys = json.loads((project / "artifacts/ansys/report.json").read_text())
    assert ansys["inputs"]["artifacts/model.step"] == hashlib.sha256((project / "artifacts/model.step").read_bytes()).hexdigest()


def test_preflight_reports_action_readiness_before_launch(project):
    agent = EngineeringAgent(config())
    result = agent.preflight("CATIA建模，Ansys应力分析，Adams动力学，VI-grade操稳",
                             project_dir=str(project), parameters={"scenario": "fixture"})
    assert result["ready"] is True
    assert result["step_count"] == 4
    assert all(item["ready"] for item in result["steps"])
    assert all(item["contract"]["outputs"] for item in result["steps"])


def test_preflight_identifies_unbound_vendor_action(project):
    agent = EngineeringAgent({"generate_scripts": False, "tools": {"catia": {"actions": {}}}})
    result = agent.preflight("CATIA建模", project_dir=str(project))
    assert result["ready"] is False
    assert result["steps"][0]["issues"][0]["code"] == "ACTION_BINDING_REQUIRED"


def test_failed_downstream_does_not_publish_first_step_or_replace_previous_outputs(project, monkeypatch):
    agent = EngineeringAgent(config())
    plan = agent.plan("CATIA建模，Ansys应力分析")
    plan.steps[1].parameters["fixture_mode"] = "missing"
    monkeypatch.setattr(agent, "plan", lambda *a, **kw: plan)
    (project / "artifacts").mkdir()
    original = project / "artifacts/model.step"
    original.write_text("existing model")
    report = agent.run("fixture", project_dir=str(project), dry_run=False)
    assert report.status == "rolled_back"
    assert report.results[0].status == "completed"
    assert report.results[1].status == "failed"
    assert original.read_text() == "existing model"
    assert not (project / "artifacts/ansys/report.json").exists()
    assert not report.generated_files


def test_direct_operation_also_uses_transaction(project):
    agent = EngineeringAgent(config())
    result = agent.execute_operation("catia.create_model", {"project_dir": str(project), "dry_run": False,
                                                            "outputs": ["artifacts/part.step"]})
    assert result["status"] == "completed"
    assert result["details"]["transaction_status"] == "committed"
    assert (project / "artifacts/part.step").is_file()


def test_no_guessed_com_and_no_path_string_installation_evidence(monkeypatch):
    monkeypatch.setattr("engineering_agent.windows_control.platform.system", lambda: "Windows")
    diagnostics = WindowsAutomation().validate_backends({"ansys": {"executable": "C:/missing/fake.exe"}})
    assert all(not item["prog_id"] for item in diagnostics["tools"])
    assert not diagnostics["tools"][1]["executable_found"]
    assert diagnostics["ready_count"] == 0


def test_health_is_read_only_and_command_strings_are_not_accepted(tmp_path):
    bridge = WindowsBridge({"root_dir": str(tmp_path), "commands": {"catia.create_or_update_model": "cmd /c script"}})
    assert not bridge.health()["command_checks"]["catia.create_or_update_model"]["executable_found"]
    assert not (tmp_path / "jobs").exists()


def test_mcp_passes_engineering_parameters_to_configured_software(project):
    from engineering_agent.mcp_server import MCPServer
    server = MCPServer(EngineeringAgent(config()), profile="full")
    listed = {item["name"]: item for item in server._tools()}
    assert "parameters" in listed["engineering_run"]["inputSchema"]["properties"]
    report = server.call("engineering_run", {"prompt": "VI-grade", "project_dir": str(project), "dry_run": False,
                                             "parameters": {"scenario": "fixture-only", "speed_kph": 60}})
    assert report["status"] == "committed"
    data = json.loads((project / "artifacts/vi-grade/metrics.json").read_text())
    assert data["parameters"]["speed_kph"] == 60 and data["parameters"]["scenario"] == "fixture-only"


def test_cli_forwards_parameters_and_reports_invalid_plan_failure(project, capsys):
    from engineering_agent.cli import main
    cfg = project / "config.json"
    cfg.write_text(json.dumps(config()))
    args = ["--config", str(cfg), "--project-dir", str(project), "run", "VI-grade", "--execute"]
    assert main(args + ["--parameters", '{"scenario":"fixture-only"}']) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "committed"
    assert main(args) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "failed" and report["results"] == []


def test_required_input_missing_prevents_launch(project, tmp_path):
    request_step = step()
    request_step.inputs = ["not-there.step"]
    result = CatiaAdapter(config()["tools"]["catia"]).execute(request_step, AdapterContext(project, False,
                                                                      workspace_dir=tmp_path / "stage"))
    assert result.details["error"]["code"] == "ARTIFACT_MISSING"
    assert not (tmp_path / "stage").exists()


def test_environment_token_and_invalid_http_request(project, bridge, monkeypatch):
    url, _ = bridge
    cfg = config(url)["tools"]["catia"]
    cfg["bridge_token_env"] = "SIMLOOM_TEST_WORKER_TOKEN"
    monkeypatch.setenv("SIMLOOM_TEST_WORKER_TOKEN", "contract-test-token")
    adapter = CatiaAdapter(cfg)
    assert adapter.doctor()["reachable"]
    monkeypatch.delenv("SIMLOOM_TEST_WORKER_TOKEN")
    assert not adapter.doctor()["reachable"]
    body = httpx.post(url, content=b"{}", headers={"X-Bridge-Token": "contract-test-token"}).json()
    assert body["error"]["code"] == "WORKER_SCHEMA_UNSUPPORTED"


def test_transfer_limit_is_enforced_before_execution(project, tmp_path, monkeypatch):
    import engineering_agent.worker_protocol as protocol
    monkeypatch.setattr(protocol, "MAX_BYTES", 4)
    result = CatiaAdapter(config()["tools"]["catia"]).execute(step(), AdapterContext(project, False,
                                                                    workspace_dir=tmp_path / "stage"))
    assert result.details["error"]["code"] == "ARTIFACT_TOO_LARGE"
    assert not (tmp_path / "stage").exists()


def test_queue_preserves_software_parameters_and_executes(project, tmp_path):
    from engineering_agent.queue import TaskQueue
    from engineering_agent.tasks import TaskRunner, input_snapshot
    queue = TaskQueue(tmp_path / "queue.db")
    snapshot = input_snapshot(overrides={"parameters": {"scenario": "queue-fixture"}})
    task_id = queue.submit("VI-grade", str(project), inputs=snapshot)
    result = TaskRunner(EngineeringAgent(config()), queue).run(task_id, execute=True)
    assert result["status"] == "committed"
    data = json.loads((project / "artifacts/vi-grade/metrics.json").read_text())
    assert data["parameters"]["scenario"] == "queue-fixture"
    assert queue.get(task_id)["input"]["parameters"] == {"scenario": "queue-fixture"}


def test_software_operation_schema_exposes_execution_and_artifact_contract():
    from engineering_agent.operations import OperationRegistry
    schema = OperationRegistry().get("catia.create_model").schema()
    assert {"project_dir", "dry_run", "inputs", "outputs"} <= schema["parameters_schema"].keys()
    assert "details" in schema["output_schema"] and "status" in schema["output_schema"]


def test_validate_cli_accepts_engineering_parameters(project, capsys):
    from engineering_agent.cli import main
    cfg = project / "config.json"
    cfg.write_text(json.dumps(config()))
    assert main(["--config", str(cfg), "validate", "VI-grade", "--parameters", '{"scenario":"fixture"}']) == 0
    assert json.loads(capsys.readouterr().out)["valid"]


def test_llm_plan_preserves_explicit_engineering_parameters():
    from engineering_agent.llm_planner import AutonomousPlanner
    from engineering_agent.models import AgentRequest
    request = AgentRequest("VI-grade", parameters={"scenario": "user-scenario", "speed_kph": 60})
    plan = AutonomousPlanner()._validate(request, {"steps": [{"id": "v", "tool": "vi-grade",
        "action": "run_vehicle_dynamics", "parameters": {"speed_kph": 999}}]})
    assert plan.steps[0].parameters == request.parameters
