import json
import sys
from pathlib import Path

import pytest

from engineering_agent.adapters.base import AdapterContext
from engineering_agent.adapters.vendor import CatiaAdapter
from engineering_agent.models import PlanStep, ToolName
from engineering_agent.software_worker import run_job
from engineering_agent.vendor_validation import VendorValidationError, validate_report
from engineering_agent.worker_protocol import SCHEMA


RUNNER = r'''
import json, sys
from pathlib import Path
request = json.loads(Path(sys.argv[1]).read_text())
for name in request["outputs"]:
    Path(name).parent.mkdir(parents=True, exist_ok=True)
    if name.endswith("report.json"):
        report = {"schema_version":"simloom-ai/vendor-validation/v1", "request_id":request["request_id"],
                  "tool":request["tool"], "action":request["action"], "passed":True,
                  "checks":[{"name":"native_readback", "passed":True}, {"name":"source_unchanged", "passed":True}],
                  "native_readback":{"length":{"value":12.5,"unit":"mm"}}}
        Path(name).write_text(json.dumps(report))
    else:
        Path(name).write_text("native result")
'''
COMMAND = [sys.executable, "{script}", "{request_file}"]


def test_report_requires_exact_operation_and_all_checks():
    report = {"schema_version": "simloom-ai/vendor-validation/v1", "request_id": "a" * 32,
              "tool": "catia", "action": "fixture_report", "passed": True,
              "checks": [{"name": "native_readback", "passed": True}]}
    assert validate_report(report, request_id="a" * 32, tool="catia", action="fixture_report",
                           required_checks=["native_readback"])["passed"]
    with pytest.raises(VendorValidationError, match="VENDOR_CHECK_MISSING"):
        validate_report(report, request_id="a" * 32, tool="catia", action="fixture_report",
                        required_checks=["source_unchanged"])
    report["passed"] = False
    with pytest.raises(VendorValidationError, match="VENDOR_REPORT_FAILED"):
        validate_report(report, request_id="a" * 32, tool="catia", action="fixture_report")


def test_worker_and_controller_both_validate_native_report(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "scripts").mkdir()
    (project / "scripts/runner.py").write_text(RUNNER)
    output_names = ["artifacts/model.step", "artifacts/vendor-report.json"]
    step = PlanStep("vendor", ToolName.CATIA, "fixture_report", "update a native model",
                    outputs=output_names, parameters={"input_model": "inputs/model.CATPart"})
    (project / "inputs").mkdir()
    (project / "inputs/model.CATPart").write_text("fixture native model")
    cfg = {"actions": {"fixture_report": {"script": "scripts/runner.py", "command": COMMAND,
                                                "validation": {"report": "artifacts/vendor-report.json",
                                                                "required_checks": ["native_readback", "source_unchanged"]}}}}
    result = CatiaAdapter(cfg).execute(step, AdapterContext(project, False, workspace_dir=tmp_path / "stage"))
    assert result.status == "completed"
    assert result.details["verification"] == "artifact_and_report_schema"
    assert result.details["vendor_validation"]["native_readback"]["length"]["unit"] == "mm"


def test_worker_rejects_report_when_validation_contract_is_present(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "scripts").mkdir()
    bad = project / "scripts/runner.py"
    bad.write_text("from pathlib import Path; import json,sys; r=json.loads(Path(sys.argv[1]).read_text()); Path(r['outputs'][0]).parent.mkdir(parents=True,exist_ok=True); Path(r['outputs'][0]).write_text('{}')")
    payload = {"schema_version": SCHEMA, "request_id": "b" * 32, "tool": "catia", "action": "fixture_report",
               "script": "scripts/runner.py", "parameters": {}, "inputs": [],
               "outputs": ["artifacts/vendor-report.json"], "validation": {"report": "artifacts/vendor-report.json", "required_checks": []},
               "files": [{"path": "scripts/runner.py", "bytes": bad.stat().st_size,
                          "sha256": __import__('hashlib').sha256(bad.read_bytes()).hexdigest(),
                          "content_base64": __import__('base64').b64encode(bad.read_bytes()).decode()}]}
    body = run_job(tmp_path / "worker", payload, {"catia.fixture_report": COMMAND})
    assert body["status"] == "failed"
    assert body["error"]["code"] in {"VENDOR_REPORT_SCHEMA_UNSUPPORTED", "VENDOR_CHECKS_MISSING", "VENDOR_REPORT_INVALID"}
