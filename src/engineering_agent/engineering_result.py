"""Normalize vendor runner output for agent-facing engineering workflows.

The four software adapters historically returned a mixture of ``verification``
and ``vendor_validation`` fields.  This module adds one small, stable envelope
without discarding those fields, so an agent can consume CATIA, Ansys, Adams
and VI-grade results through the same shape.

The envelope also carries a bounded summary of the exact input/output and
metric contract used for the run.  The summary is descriptive evidence: it
never replaces the native vendor report or claims that a contract was
satisfied merely because it was present.
"""
from __future__ import annotations

from typing import Any, Iterable

from .engineering_failure import classify_failure
from .engineering_lifecycle import normalize_lifecycle


SCHEMA = "simloom-ai/engineering-result/v1"
ACCEPTANCE_STATES = {"not_requested", "passed", "failed", "unknown"}


def _records(value: Any) -> list[dict[str, Any]]:
    """Return bounded, project-relative artifact records from a manifest."""
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue
        record: dict[str, Any] = {"path": path}
        if type(item.get("bytes")) is int and item["bytes"] >= 0:
            record["bytes"] = item["bytes"]
        if isinstance(item.get("sha256"), str):
            record["sha256"] = item["sha256"]
        records.append(record)
    return records[:512]


def _acceptance(vendor_validation: Any) -> str:
    if not isinstance(vendor_validation, dict):
        return "not_requested" if vendor_validation in (None, "not_run") else "unknown"
    value = vendor_validation.get("engineering_acceptance")
    return value if value in ACCEPTANCE_STATES else "unknown"


def _contract_summary(contract: Any, input_artifacts: list[dict[str, Any]],
                      output_artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Project a validation contract into a bounded, agent-readable summary."""
    if not isinstance(contract, dict):
        contract = {}
    expectations = contract.get("expectations")
    if not isinstance(expectations, dict):
        expectations = {}
    input_hashes = contract.get("input_hashes")
    if not isinstance(input_hashes, dict):
        input_hashes = {}
    input_paths = [item["path"] for item in input_artifacts]
    if input_hashes:
        input_paths = [key for key in input_hashes if isinstance(key, str) and key][:512]
    output_paths = [item["path"] for item in output_artifacts]
    required_metrics = expectations.get("required_metrics", contract.get("required_metrics", []))
    if not isinstance(required_metrics, list):
        required_metrics = []
    required_metrics = [item for item in required_metrics if isinstance(item, str) and item][:256]
    metric_units = expectations.get("metric_units", contract.get("metric_units", {}))
    if not isinstance(metric_units, dict):
        metric_units = {}
    metric_units = {str(key): value for key, value in list(metric_units.items())[:256]
                    if isinstance(key, str) and isinstance(value, str)}
    metric_rules = expectations.get("metric_rules", contract.get("metric_rules", []))
    if not isinstance(metric_rules, list):
        metric_rules = []
    metric_rules = [item for item in metric_rules[:256] if isinstance(item, dict)]
    required_checks = contract.get("required_checks", [])
    if not isinstance(required_checks, list):
        required_checks = []
    required_checks = [item for item in required_checks if isinstance(item, str) and item][:256]
    report = contract.get("report")
    return {
        "schema_version": contract.get("schema_version") if isinstance(contract.get("schema_version"), str) else None,
        "kind": contract.get("kind") if isinstance(contract.get("kind"), str) else None,
        "report": report if isinstance(report, str) else None,
        "inputs": input_paths,
        "outputs": output_paths,
        "required_checks": required_checks,
        "required_metrics": required_metrics,
        "metric_units": metric_units,
        "metric_rules": metric_rules,
    }


def normalize_engineering_result(*, tool: str, action: str, status: str,
                                 message: str = "", outputs: Iterable[str] = (),
                                 details: dict[str, Any] | None = None,
                                 contract: dict[str, Any] | None = None,
                                 phase: str | None = None,
                                 lifecycle: dict[str, Any] | None = None,
                                 recoverable: bool = False,
                                 operation_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the common result envelope from an adapter/worker response.

    The function intentionally copies only bounded structured fields.  It does
    not infer engineering validity from a process exit code or from artifact
    hashes; those remain separate evidence fields. ``contract`` is included as
    a descriptive snapshot so a planner can compare attempts without parsing
    vendor-specific payloads. ``phase``/``lifecycle`` records which controller
    stage was reached; uncertain stages remain explicitly unknown or unstarted.
    """
    details = details if isinstance(details, dict) else {}
    contract = contract if isinstance(contract, dict) else details.get("validation")
    vendor_validation = details.get("vendor_validation")
    if vendor_validation == "failed":
        acceptance = "unknown"
    else:
        acceptance = _acceptance(vendor_validation)
    verification = details.get("verification", "not_run")
    if not isinstance(verification, str) or not verification:
        verification = "not_run"
    metrics: dict[str, Any] = {}
    metric_verdicts: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    scope = None
    real_machine_verified = False
    if isinstance(vendor_validation, dict):
        raw_metrics = vendor_validation.get("metrics")
        if isinstance(raw_metrics, dict):
            metrics = {str(key): value for key, value in list(raw_metrics.items())[:256]}
        raw_verdicts = vendor_validation.get("metric_verdicts")
        if isinstance(raw_verdicts, list):
            metric_verdicts = [item for item in raw_verdicts[:256] if isinstance(item, dict)]
        raw_observations = vendor_validation.get("observations")
        if isinstance(raw_observations, dict):
            observations = raw_observations
        scope = vendor_validation.get("scope")
        real_machine_verified = vendor_validation.get("real_machine_verified") is True
    error = details.get("error")
    if not isinstance(error, dict):
        error = None
    error_code = error.get("code") if isinstance(error, dict) and isinstance(error.get("code"), str) else None
    effective_recoverable = bool(recoverable or (details.get("remote_job") or {}).get("requires_reconnect"))
    lifecycle_value = normalize_lifecycle(
        lifecycle if isinstance(lifecycle, dict) else details.get("lifecycle"),
        phase=(phase or ("recover" if effective_recoverable else None)),
        status=str(status),
        validation_requested=contract is not None or isinstance(vendor_validation, dict),
        error_code=error_code,
        message=message,
        recoverable=effective_recoverable,
    )
    input_artifacts = _records(details.get("inputs"))
    output_artifacts = _records(details.get("output_manifest"))
    if not output_artifacts:
        # A dry-run or an early failure may only have declared output names.
        output_artifacts = [{"path": item} for item in outputs if isinstance(item, str) and item][:512]
    result: dict[str, Any] = {
        "schema_version": SCHEMA,
        "tool": str(tool),
        "action": str(action),
        "status": str(status),
        "message": str(message or ""),
        "verification": verification,
        "engineering_acceptance": acceptance,
        "real_machine_verified": real_machine_verified,
        "metrics": metrics,
        "metric_verdicts": metric_verdicts,
        "observations": observations,
        "artifacts": {"inputs": input_artifacts, "outputs": output_artifacts},
        "contract": _contract_summary(contract, input_artifacts, output_artifacts),
        "operation_contract": (operation_contract if isinstance(operation_contract, dict)
                               else details.get("operation_contract")),
        "lifecycle": lifecycle_value,
        "evidence": {
            "request_id": details.get("request_id") if isinstance(details.get("request_id"), str) else None,
            "scope": scope if isinstance(scope, str) else None,
            "verification": verification,
        },
    }
    if error is not None:
        result["error"] = error
        result["failure"] = classify_failure(
            error_code,
            status=str(status),
            lifecycle=lifecycle_value,
            message=error.get("message") if isinstance(error.get("message"), str) else message,
        )
    return result
