from __future__ import annotations

from engineering_agent.engineering_result import SCHEMA, normalize_engineering_result
from engineering_agent.operations import OperationRegistry


def test_normalized_result_separates_execution_from_engineering_acceptance():
    result = normalize_engineering_result(
        tool="ansys", action="solve_configured_model", status="completed",
        message="completed", outputs=["results.json"], details={
            "request_id": "abc123",
            "verification": "artifact_and_vendor_evidence",
            "inputs": [{"path": "model.mechdat", "bytes": 10, "sha256": "a" * 64}],
            "output_manifest": [{"path": "results.json", "bytes": 20, "sha256": "b" * 64}],
            "vendor_validation": {
                "engineering_acceptance": "failed",
                "metrics": {"equivalent_stress": {"value": 125.0, "unit": "MPa"}},
                "metric_verdicts": [{"name": "equivalent_stress", "passed": False}],
                "observations": {"solution_status": "Done"},
                "real_machine_verified": False,
                "scope": "file_identity_and_reported_observations",
            },
        })
    assert result["schema_version"] == SCHEMA
    assert result["status"] == "completed"
    assert result["engineering_acceptance"] == "failed"
    assert result["verification"] == "artifact_and_vendor_evidence"
    assert result["metrics"]["equivalent_stress"]["unit"] == "MPa"
    assert result["artifacts"]["inputs"][0]["path"] == "model.mechdat"
    assert result["artifacts"]["outputs"][0]["sha256"] == "b" * 64


def test_missing_vendor_contract_is_explicitly_not_requested():
    result = normalize_engineering_result(
        tool="catia", action="create_model", status="planned",
        message="dry-run", outputs=["model.CATPart"], details={"verification": "not_run"})
    assert result["engineering_acceptance"] == "not_requested"
    assert result["real_machine_verified"] is False
    assert result["artifacts"]["outputs"] == [{"path": "model.CATPart"}]


def test_failed_vendor_validation_does_not_claim_engineering_failure():
    result = normalize_engineering_result(
        tool="adams", action="build_and_run_multibody", status="failed",
        message="invalid report", details={"verification": "artifact_integrity", "vendor_validation": "failed"})
    assert result["engineering_acceptance"] == "unknown"
    assert result["verification"] == "artifact_integrity"


def test_vendor_operation_schema_advertises_normalized_result():
    schema = OperationRegistry().get("ansys.solve_configured_model").schema()
    details = schema["output_schema"]["details"]
    assert "engineering_result" in details["properties"]
    envelope = details["properties"]["engineering_result"]
    assert envelope["properties"]["contract"]["properties"]["metric_rules"]["type"] == "array"
    assert envelope["properties"]["real_machine_verified"]["type"] == "boolean"
    assert envelope["properties"]["lifecycle"]["properties"]["stages"]["properties"]["recover"]["properties"]["status"]["enum"][-1] == "deferred"


def test_normalized_result_exposes_the_input_output_and_metric_contract():
    result = normalize_engineering_result(
        tool="ansys", action="solve_configured_model", status="failed",
        message="runner stopped", outputs=["result.json", "report.json"],
        details={
            "inputs": [{"path": "model.mechdat", "bytes": 12, "sha256": "a" * 64}],
            "error": {"code": "SOFTWARE_TIMEOUT", "message": "runner stopped"},
        },
        contract={
            "schema_version": "simloom-ai/vendor-evidence/v2",
            "kind": "mechanical_solve",
            "report": "report.json",
            "input_hashes": {"model.mechdat": "a" * 64},
            "expectations": {
                "required_metrics": ["equivalent_stress"],
                "metric_units": {"equivalent_stress": "MPa"},
                "metric_rules": [{"name": "equivalent_stress", "operator": "<=", "target": 80, "unit": "MPa"}],
            },
        },
    )
    assert result["contract"] == {
        "schema_version": "simloom-ai/vendor-evidence/v2",
        "kind": "mechanical_solve",
        "report": "report.json",
        "inputs": ["model.mechdat"],
        "outputs": ["result.json", "report.json"],
        "required_checks": [],
        "required_metrics": ["equivalent_stress"],
        "metric_units": {"equivalent_stress": "MPa"},
        "metric_rules": [{"name": "equivalent_stress", "operator": "<=", "target": 80, "unit": "MPa"}],
    }
    assert result["error"]["code"] == "SOFTWARE_TIMEOUT"


def test_lifecycle_marks_completed_validation_separately_from_execution():
    result = normalize_engineering_result(
        tool="ansys", action="solve_configured_model", status="completed",
        details={"vendor_validation": {"engineering_acceptance": "failed", "metric_verdicts": []}},
        contract={"schema_version": "simloom-ai/vendor-evidence/v2"},
    )
    lifecycle = result["lifecycle"]
    assert lifecycle["schema_version"] == "simloom-ai/engineering-lifecycle/v1"
    assert lifecycle["current_stage"] == "completed"
    assert all(lifecycle["stages"][stage]["status"] == "passed"
               for stage in ("preflight", "execute", "collect", "validate"))
    assert lifecycle["stages"]["recover"]["status"] == "not_run"


def test_lifecycle_does_not_claim_unknown_failure_was_validated():
    result = normalize_engineering_result(
        tool="catia", action="create_model", status="failed",
        details={"error": {"code": "SOFTWARE_TIMEOUT", "message": "lost"},
                 "remote_job": {"requires_reconnect": True}},
    )
    lifecycle = result["lifecycle"]
    assert lifecycle["current_stage"] == "recover"
    assert lifecycle["stages"]["execute"]["status"] == "not_run"
    assert lifecycle["stages"]["validate"]["status"] == "not_run"
    assert lifecycle["stages"]["recover"]["status"] == "deferred"
    assert lifecycle["recoverable"] is True
    assert lifecycle["next_action"] == "resume"

