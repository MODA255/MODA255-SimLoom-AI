import json
import sys
import threading
from pathlib import Path

from engineering_agent.adapters.base import AdapterContext
from engineering_agent.adapters.vendor import CatiaAdapter
from engineering_agent.models import AgentRequest, ExecutionPlan, PlanStep, ToolName
from engineering_agent.software_preflight import preflight_plan
from engineering_agent.software_worker import run_job
from engineering_agent.worker_protocol import SCHEMA
from engineering_agent.windows_bridge import create_bridge_server


RUNNER = "from pathlib import Path\nimport json, sys\nrequest=json.loads(Path(sys.argv[1]).read_text())\nfor name in request['outputs']:\n p=Path(name); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps({'ok': True}))\n"
COMMAND = [sys.executable, "{script}", "{request_file}"]
ACTION = "create_or_update_model"


def _project(tmp_path):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "runner.py").write_text(RUNNER, encoding="utf-8")
    return root


def _config(requirements):
    return {"generate_scripts": False, "tools": {"catia": {"actions": {
        ACTION: {"script": "scripts/runner.py", "command": COMMAND,
                 "capability_requirements": requirements}
    }}}}


def _step():
    return PlanStep("model", ToolName.CATIA, ACTION, "create a model", outputs=["artifacts/model.json"])


def test_preflight_publishes_requirements_and_digest(tmp_path):
    root = _project(tmp_path)
    adapter = CatiaAdapter(_config({"adapter_capabilities": ["step-export"]})["tools"]["catia"])
    result = preflight_plan({ToolName.CATIA: adapter}, ExecutionPlan(AgentRequest("test", str(root), {}), [_step()], []), root)
    step = result["steps"][0]
    assert step["requirements"] == {"declared": {"adapter_capabilities": ["step-export"]}, "status": "ready"}
    assert step["operation_contract"]["requirements"] == {"adapter_capabilities": ["step-export"]}
    assert step["operation_contract"]["definition_digest"]


def test_missing_adapter_capability_blocks_preflight_and_execution(tmp_path):
    root = _project(tmp_path)
    adapter = CatiaAdapter(_config({"adapter_capabilities": ["unknown-vendor-feature"]})["tools"]["catia"])
    result = preflight_plan({ToolName.CATIA: adapter}, ExecutionPlan(AgentRequest("test", str(root), {}), [_step()], []), root)
    assert result["ready"] is False
    assert result["steps"][0]["issues"][0]["code"] == "ADAPTER_CAPABILITY_MISSING"
    executed = adapter.execute(_step(), AdapterContext(root, dry_run=False, workspace_dir=tmp_path / "stage"))
    assert executed.status == "failed"
    assert executed.details["error"]["code"] == "ADAPTER_CAPABILITY_MISSING"
    assert not (root / "artifacts/model.json").exists()


def test_local_worker_requirement_requires_bridge(tmp_path):
    root = _project(tmp_path)
    adapter = CatiaAdapter(_config({"worker_features": ["durable_jobs"]})["tools"]["catia"])
    result = adapter.execute(_step(), AdapterContext(root, dry_run=False, workspace_dir=tmp_path / "stage"))
    assert result.status == "failed"
    assert result.details["error"]["code"] == "WORKER_FEATURE_REQUIRES_BRIDGE"


def test_doctor_marks_missing_action_capability_as_not_execution_ready(tmp_path):
    root = _project(tmp_path)
    config = _config({"adapter_capabilities": ["unknown-vendor-feature"]})
    adapter = CatiaAdapter(config["tools"]["catia"])
    doctor = adapter.doctor(probe=False)
    assert doctor["actions"] == [ACTION]
    assert doctor["ready_actions"] == []
    assert doctor["execution_ready"] is False
    assert doctor["action_requirements"][ACTION]["status"] == "missing"


def test_doctor_marks_remote_worker_requirement_as_not_probed_without_probe(tmp_path):
    root = _project(tmp_path)
    config = _config({"worker_features": ["durable_jobs"]})
    server = create_bridge_server({"root_dir": str(tmp_path / "worker"), "port": 0,
                                   "token": "token", "commands": {f"catia.{ACTION}": COMMAND}})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config["tools"]["catia"].update(bridge_url=f"http://127.0.0.1:{server.server_port}", bridge_token="token")
        adapter = CatiaAdapter(config["tools"]["catia"])
        doctor = adapter.doctor(probe=False)
        assert doctor["action_requirements"][ACTION]["status"] == "not_probed"
        assert doctor["execution_ready"] is False
        probed = adapter.doctor(probe=True)
        assert probed["action_requirements"][ACTION]["status"] == "ready"
        assert probed["execution_ready"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_remote_worker_requirement_is_checked_before_job_launch(tmp_path):
    root = _project(tmp_path)
    server = create_bridge_server({"root_dir": str(tmp_path / "worker"), "port": 0,
                                   "token": "token", "commands": {f"catia.{ACTION}": COMMAND}})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _config({"worker_features": ["artifact_streaming"]})
        config["tools"]["catia"].update(bridge_url=f"http://127.0.0.1:{server.server_port}", bridge_token="token")
        adapter = CatiaAdapter(config["tools"]["catia"])
        result = adapter.execute(_step(), AdapterContext(root, dry_run=False, workspace_dir=tmp_path / "stage"))
        assert result.status == "failed"
        assert result.details["error"]["code"] == "WORKER_FEATURE_MISSING"
        assert not list((tmp_path / "worker" / "control" / "jobs").glob("*/_simloom_request.json"))
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_worker_rejects_requirement_drift_before_runner(tmp_path):
    root = _project(tmp_path)
    config = _config({"adapter_capabilities": ["step-export"]})
    adapter = CatiaAdapter(config["tools"]["catia"])
    binding = config["tools"]["catia"]["actions"][ACTION]
    prepared = adapter._prepare(_step(), AdapterContext(root), binding)
    assert prepared["operation_contract"]["requirements"] == {"adapter_capabilities": ["step-export"]}
    prepared["capability_requirements"] = {"adapter_capabilities": ["parametric-geometry"]}
    result = run_job(tmp_path / "worker", prepared, {f"catia.{ACTION}": COMMAND})
    assert result["status"] == "failed"
    assert result["error"]["code"] == "SOFTWARE_OPERATION_CONTRACT_MISMATCH"
