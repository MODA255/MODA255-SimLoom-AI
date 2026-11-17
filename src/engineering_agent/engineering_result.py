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

import math
from typing import Any, Iterable

from .engineering_failure import classify_failure
from .engineering_lifecycle import normalize_lifecycle


SCHEMA = "simloom-ai/engineering-result/v1"
CONTEXT_SCHEMA = "simloom-ai/engineering-context/v1"
ACCEPTANCE_STATES = {"not_requested", "passed", "failed", "unknown"}


def _bounded_text(value: Any, limit: int = 256) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    """Copy vendor context without allowing unbounded or executable payloads."""
    if depth > 3:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:1024]
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1) for item in value[:128]]
    if isinstance(value, dict):
        return {str(key)[:128]: _bounded_value(item, depth=depth + 1)
                for key, item in list(value.items())[:128]
                if isinstance(key, (str, int, float))}
    return str(value)[:256]


def _context_records(value: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records: list[dict[str, Any]] = []
    for item in value[:256]:
        if not isinstance(item, dict):
            continue
        record: dict[str, Any] = {}
        for field in fields:
            if field not in item:
                continue
            if field in {"id", "name", "kind", "parent_id", "reference", "path", "role", "status", "unit", "version", "build", "license_status"}:
                text = _bounded_text(item[field])
                if text is not None:
                    record[field] = text
            else:
                record[field] = _bounded_value(item[field])
        if record:
            records.append(record)
    return records


def _engineering_context(details: dict[str, Any], vendor_validation: Any,
                         *, tool: str | None = None) -> dict[str, Any] | None:
    """Normalize explicit native software context into one agent-facing shape.

    The controller never infers a CATIA tree, Ansys coordinate system, Adams
    load case or VI-grade object.  It only copies fields explicitly exported by
    a runner or validator, with bounded sizes and stable field names.
    """
    raw = details.get("engineering_context")
    if not isinstance(raw, dict) and isinstance(vendor_validation, dict):
        raw = vendor_validation.get("engineering_context")
        if not isinstance(raw, dict):
            observations = vendor_validation.get("observations")
            if isinstance(observations, dict):
                raw = observations.get("engineering_context")
    if not isinstance(raw, dict):
        raw = {}
        source = details if isinstance(details, dict) else {}
        vendor = vendor_validation if isinstance(vendor_validation, dict) else {}
        vendor_observations = vendor.get("observations") if isinstance(vendor.get("observations"), dict) else {}
        for key in ("software_version", "software_build", "software_edition", "license_status",
                    "native_objects", "coordinate_systems", "load_cases", "model_references", "source"):
            if key in source:
                raw[key] = source[key]
            elif key in vendor:
                raw[key] = vendor[key]
            elif key in vendor_observations:
                raw[key] = vendor_observations[key]
        if not raw:
            return None
    else:
        raw = dict(raw)
        for key in ("software_version", "software_build", "software_edition", "license_status"):
            if key not in raw and key in details:
                raw[key] = details[key]
    software = raw.get("software") if isinstance(raw.get("software"), dict) else {}
    if tool and "name" not in software:
        software = dict(software, name=tool)
    for source_key, target_key in (("software_version", "version"), ("software_build", "build"),
                                   ("software_edition", "edition")):
        if target_key not in software and isinstance(raw.get(source_key), str):
            software[target_key] = raw[source_key]
    if "license_status" not in software and isinstance(raw.get("license_status"), str):
        software["license_status"] = raw["license_status"]
    context: dict[str, Any] = {
        "schema_version": CONTEXT_SCHEMA,
        "software": {key: _bounded_text(software.get(key)) for key in
                      ("name", "version", "build", "edition", "license_status")
                      if _bounded_text(software.get(key)) is not None},
        "native_objects": _context_records(raw.get("native_objects"),
                                             ("id", "kind", "name", "parent_id", "reference", "path", "status", "properties")),
        "coordinate_systems": _context_records(raw.get("coordinate_systems"),
                                                 ("id", "name", "kind", "reference", "origin", "axes", "unit", "properties")),
        "load_cases": _context_records(raw.get("load_cases"),
                                         ("id", "name", "kind", "status", "model_reference", "coordinate_system", "loads", "constraints", "parameters")),
        "model_references": _context_records(raw.get("model_references"),
                                              ("id", "name", "kind", "path", "reference", "role", "version", "properties")),
    }
    if isinstance(raw.get("source"), str):
        context["source"] = raw["source"][:256]
    if not context["software"] and not any(context[key] for key in
                                             ("native_objects", "coordinate_systems", "load_cases", "model_references")):
        return None
    return context


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
    context = _engineering_context(details, vendor_validation, tool=tool)
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
        "engineering_context": context,
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
