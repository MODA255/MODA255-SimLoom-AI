"""Normalize vendor runner output for agent-facing engineering workflows.

The four software adapters historically returned a mixture of ``verification``
and ``vendor_validation`` fields.  This module adds one small, stable envelope
without discarding those fields, so an agent can consume CATIA, Ansys, Adams
and VI-grade results through the same shape.
"""
from __future__ import annotations

from typing import Any, Iterable


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


def normalize_engineering_result(*, tool: str, action: str, status: str,
                                 message: str = "", outputs: Iterable[str] = (),
                                 details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the common result envelope from an adapter/worker response.

    The function intentionally copies only bounded structured fields.  It does
    not infer engineering validity from a process exit code or from artifact
    hashes; those remain separate evidence fields.
    """
    details = details if isinstance(details, dict) else {}
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
        "evidence": {
            "request_id": details.get("request_id") if isinstance(details.get("request_id"), str) else None,
            "scope": scope if isinstance(scope, str) else None,
            "verification": verification,
        },
    }
    if error is not None:
        result["error"] = error
    return result

