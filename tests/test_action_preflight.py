import sys
import json
import threading
import urllib.request

from engineering_agent import EngineeringAgent
from engineering_agent.cli import _parser
from engineering_agent.queue import TaskQueue
from engineering_agent.web import create_server


RUNNER = "from pathlib import Path\nimport json, sys\nrequest=json.loads(Path(sys.argv[1]).read_text())\nfor name in request['outputs']:\n p=Path(name); p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{}')\n"


def _project(tmp_path):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "runner.py").write_text(RUNNER, encoding="utf-8")
    return root


def test_action_preflight_reuses_adapter_contract_and_digest(tmp_path):
    root = _project(tmp_path)
    config = {"generate_scripts": False, "tools": {"catia": {"actions": {
        "create_or_update_model": {
            "script": "scripts/runner.py",
            "command": [sys.executable, "{script}", "{request_file}"],
            "outputs": ["artifacts/model.json"],
        }
    }}}}
    agent = EngineeringAgent(config)
    result = agent.preflight_action("catia", "create_or_update_model", project_dir=root)
    assert result["schema_version"] == "simloom-ai/action-preflight/v1"
    assert result["step"]["ready"] is True
    assert result["step"]["contract"]["outputs"] == ["artifacts/model.json"]
    assert result["step"]["operation_contract"]["definition_digest"]
    direct = agent.execute_operation("project.action_preflight", {
        "tool": "catia", "action": "create_or_update_model", "project_dir": str(root)})
    assert direct["step"]["operation_contract"]["definition_digest"] == result["step"]["operation_contract"]["definition_digest"]


def test_action_preflight_reports_typed_parameter_contract_errors(tmp_path):
    root = _project(tmp_path)
    config = {"generate_scripts": False, "tools": {"catia": {"actions": {
        "update_parameters": {
            "script": "scripts/runner.py",
            "command": [sys.executable, "{script}", "{request_file}"],
            "validation": {"kind": "catia_parameters"},
        }
    }}}}
    result = EngineeringAgent(config).preflight_action("catia", "update_parameters", project_dir=root)
    assert result["step"]["ready"] is False
    assert any(issue["code"].startswith("VENDOR_") for issue in result["step"]["issues"])
    assert result["step"]["next_actions"][0]["action"] == "inspect_diagnostics"


def test_action_preflight_web_endpoint_returns_same_contract(tmp_path):
    root = _project(tmp_path)
    config = {"generate_scripts": False, "tools": {"catia": {"actions": {
        "create_or_update_model": {
            "script": "scripts/runner.py",
            "command": [sys.executable, "{script}", "{request_file}"],
            "outputs": ["artifacts/model.json"],
        }
    }}}}
    queue = TaskQueue(tmp_path / "workspace.db")
    server = create_server(EngineeringAgent(config), queue, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = json.dumps({"project_dir": str(root), "tool": "catia",
                           "action": "create_or_update_model"}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/action-preflight",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            result = json.loads(response.read())
        assert result["step"]["ready"] is True
        assert result["step"]["operation_contract"]["definition_digest"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_action_preflight_cli_arguments_are_discoverable():
    args = _parser().parse_args([
        "--project-dir", "demo", "action-preflight", "ansys", "solve_configured_model",
        "--parameters", "{}", "--probe",
    ])
    assert args.command == "action-preflight"
    assert args.tool == "ansys"
    assert args.action == "solve_configured_model"
    assert args.probe is True

