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
                "metric_evidence": [{"name": "equivalent_stress", "source": {"path": "results.json", "format": "json", "value_selector": "metrics.equivalent_stress"}}],
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
    assert result["metric_evidence"][0]["source"]["value_selector"] == "metrics.equivalent_stress"
    assert result["artifacts"]["inputs"][0]["path"] == "model.mechdat"
    assert result["artifacts"]["outputs"][0]["sha256"] == "b" * 64
    assert result["engineering_context_validation"]["checked"] is False


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
    assert envelope["properties"]["metric_evidence"]["type"] == "array"
    assert envelope["properties"]["contract"]["properties"]["metric_rules"]["type"] == "array"
    assert envelope["properties"]["real_machine_verified"]["type"] == "boolean"
    assert envelope["properties"]["lifecycle"]["properties"]["stages"]["properties"]["recover"]["properties"]["status"]["enum"][-1] == "deferred"
    assert envelope["properties"]["failure"]["properties"]["category"]["enum"] == [
        "configuration", "input_contract", "transport", "execution", "collection",
        "validation", "recovery", "concurrency", "publication", "unknown"]
    assert envelope["properties"]["operation_contract"]["properties"]["definition_digest"]["type"] == "string"
    assert envelope["properties"]["engineering_context"]["properties"]["schema_version"]["const"] == "simloom-ai/engineering-context/v1"
    assert envelope["properties"]["engineering_context_validation"]["properties"]["valid"]["type"] == "boolean"
    assert envelope["properties"]["operation_contract"]["properties"]["engineering_context"]["type"] == ["object", "null"]
    assert envelope["properties"]["failure_evidence"]["properties"]["schema_version"]["const"] == "simloom-ai/failure-evidence/v1"
    assert envelope["properties"]["failure_evidence"]["properties"]["logs"]["type"] == "object"


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


def test_metric_evidence_is_bounded_and_rejects_nonfinite_values():
    result = normalize_engineering_result(
        tool="adams", action="build_and_run_multibody", status="completed",
        details={"vendor_validation": {"metric_evidence": [
            {"name": "peak", "value": float("nan"), "unit": "m/s^2",
             "source": {"path": "x" * 1000, "value_selector": "metrics.peak", "row_count": 99_999_999}},
            {"name": "valid", "value": 2.5, "unit": "m/s^2",
             "source": {"path": "result.json", "format": "json", "value_selector": "metrics.valid"}},
        ]}})
    assert "value" not in result["metric_evidence"][0]
    assert len(result["metric_evidence"][0]["source"]["path"]) == 256
    assert result["metric_evidence"][0]["source"]["row_count"] == 10_000_000


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


def test_failure_classification_distinguishes_validation_from_recovery():
    validation = normalize_engineering_result(
        tool="ansys", action="solve_configured_model", status="failed",
        details={"error": {"code": "VENDOR_REPORT_INVALID", "message": "bad report"}},
    )
    assert validation["failure"]["category"] == "validation"
    assert validation["failure"]["retryable"] is False
    assert validation["failure"]["recommended_action"] == "review_engineering_result"

    recovery = normalize_engineering_result(
        tool="ansys", action="solve_configured_model", status="failed",
        details={"error": {"code": "REMOTE_JOB_PENDING", "message": "observe later"},
                 "remote_job": {"requires_reconnect": True}},
        recoverable=True,
    )
    assert recovery["failure"]["category"] == "recovery"
    assert recovery["failure"]["retryable"] is True
    assert recovery["failure"]["requires_human"] is False


def test_failed_result_exposes_bounded_process_and_native_failure_evidence():
    result = normalize_engineering_result(
        tool="ansys", action="solve_configured_model", status="failed",
        phase="execute", details={
            "returncode": -15,
            "requires_review": True,
            "stdout": "stdout-prefix\n" + ("x" * 5000) + " token=secret-value",
            "stderr": "solver stopped password=another-secret",
            "remote_job": {"request_id": "job-123", "status": "lost", "requires_reconnect": True,
                           "api_key": "do-not-copy"},
            "vendor_validation": {
                "failure_evidence": {
                    "native_code": "ANSYS_SOLVER_ABORTED",
                    "message": "native failure token=native-secret",
                },
            },
            "error": {"code": "SOFTWARE_TIMEOUT", "message": "runner stopped"},
        })

    evidence = result["failure_evidence"]
    assert evidence["schema_version"] == "simloom-ai/failure-evidence/v1"
    assert evidence["phase"] == "execute"
    assert evidence["error_code"] == "SOFTWARE_TIMEOUT"
    assert evidence["category"] == "execution"
    assert evidence["recommended_action"] == "inspect_software_session"
    assert evidence["process"] == {"returncode": -15, "requires_review": True}
    assert len(evidence["logs"]["stdout_tail"]) <= 4096
    assert "stdout-prefix" not in evidence["logs"]["stdout_tail"]
    assert "secret-value" not in evidence["logs"]["stdout_tail"]
    assert "another-secret" not in evidence["logs"]["stderr_tail"]
    assert evidence["logs"]["paths"] == {"stdout": "_logs/stdout.txt", "stderr": "_logs/stderr.txt"}
    assert evidence["remote_job"] == {"request_id": "job-123", "status": "lost", "requires_reconnect": True}
    assert evidence["native"]["native_code"] == "ANSYS_SOLVER_ABORTED"
    assert "native-secret" not in evidence["native"]["message"]
    assert evidence["sources"] == ["worker_process", "remote_job", "vendor_evidence"]


def test_failure_evidence_is_absent_for_successful_result():
    result = normalize_engineering_result(
        tool="catia", action="create_model", status="completed",
        details={"returncode": 0, "stdout": "completed", "stderr": ""})
    assert "failure_evidence" not in result


def test_normalized_result_carries_bounded_native_engineering_context():
    result = normalize_engineering_result(
        tool="ansys", action="solve_configured_model", status="completed",
        details={
            "software_version": "2026 R1",
            "license_status": "licensed",
            "engineering_context": {
                "source": "mechanical_export",
                "native_objects": [{"id": "analysis-1", "kind": "analysis", "name": "Static Structural"}],
                "coordinate_systems": [{"id": "cs-1", "kind": "cartesian", "origin": [0, 0, 0]}],
                "load_cases": [{"id": "lc-1", "name": "Gravity", "model_reference": "analysis-1"}],
                "model_references": [{"id": "mesh-1", "kind": "mesh", "path": "mesh.cdb"}],
            },
        })
    context = result["engineering_context"]
    assert context["schema_version"] == "simloom-ai/engineering-context/v1"
    assert context["software"] == {"name": "ansys", "version": "2026 R1", "license_status": "licensed"}
    assert context["native_objects"][0]["id"] == "analysis-1"
    assert context["coordinate_systems"][0]["origin"] == [0, 0, 0]
    assert context["load_cases"][0]["model_reference"] == "analysis-1"
    assert context["model_references"][0]["path"] == "mesh.cdb"
    assert result["engineering_context_validation"]["valid"] is True


def test_normalized_result_preserves_invalid_context_as_explicit_diagnostic():
    result = normalize_engineering_result(
        tool="ansys", action="solve_configured_model", status="completed",
        details={"engineering_context": {
            "native_objects": [{"id": "analysis"}],
            "load_cases": [{"id": "lc", "model_reference": "missing"}],
        }})
    assert result["engineering_context"] is not None
    assert result["engineering_context_validation"]["valid"] is False
    assert result["engineering_context_validation"]["errors"][0]["code"] == "CONTEXT_MODEL_REFERENCE_NOT_FOUND"


def test_context_can_be_read_from_legacy_vendor_validation_fields():
    result = normalize_engineering_result(
        tool="catia", action="update_parameters", status="completed",
        details={"vendor_validation": {
            "engineering_context": {
                "software": {"version": "V5-6R2024"},
                "native_objects": [{"id": "PartBody", "kind": "body"}],
            }}})
    assert result["engineering_context"]["software"]["name"] == "catia"
    assert result["engineering_context"]["software"]["version"] == "V5-6R2024"


def test_context_can_be_read_from_vendor_observations():
    result = normalize_engineering_result(
        tool="adams", action="build_and_run_multibody", status="completed",
        details={"vendor_validation": {
            "engineering_acceptance": "passed",
            "observations": {"engineering_context": {
                "software_version": "2025.1",
                "load_cases": [{"id": "maneuver-1", "kind": "handling"}],
            }}}})
    context = result["engineering_context"]
    assert context["software"] == {"name": "adams", "version": "2025.1"}
    assert context["load_cases"][0]["id"] == "maneuver-1"

