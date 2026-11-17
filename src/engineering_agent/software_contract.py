"""Versioned contract shared by preflight, worker execution and recovery.

The contract contains only project-relative names and declarative expectations.
Dynamic input hashes remain in the worker/evidence contract.  Keeping the
operation definition separate lets preflight and execution compare the same
action without leaking local absolute paths or pretending that a command has
already run.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA = "simloom-ai/software-operation-contract/v1"
STAGES = ["preflight", "execute", "collect", "validate", "recover"]


def _paths(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str) and value][:512]


def _validation_summary(validation: Any) -> dict[str, Any] | None:
    if not isinstance(validation, dict):
        return None
    expectations = validation.get("expectations")
    if not isinstance(expectations, dict):
        expectations = validation
    result: dict[str, Any] = {
        "schema_version": validation.get("schema_version") if isinstance(validation.get("schema_version"), str) else None,
        "kind": validation.get("kind") if isinstance(validation.get("kind"), str) else None,
        "report": validation.get("report") if isinstance(validation.get("report"), str) else None,
        "required_checks": [value for value in validation.get("required_checks", []) if isinstance(value, str)][:256]
        if isinstance(validation.get("required_checks"), list) else [],
        "required_metrics": [value for value in expectations.get("required_metrics", []) if isinstance(value, str)][:256]
        if isinstance(expectations.get("required_metrics"), list) else [],
        "metric_units": {str(key): value for key, value in list((expectations.get("metric_units") or {}).items())[:256]
                         if isinstance(key, str) and isinstance(value, str)}
        if isinstance(expectations.get("metric_units"), dict) else {},
        "metric_rules": [value for value in expectations.get("metric_rules", [])[:256] if isinstance(value, dict)]
        if isinstance(expectations.get("metric_rules"), list) else [],
    }
    return result


def build_operation_contract(*, tool: str, action: str, script: str | None,
                             inputs: list[str], outputs: list[str],
                             validation: dict[str, Any] | None,
                             mode: str = "local", bridge: bool = False,
                             workflow: bool = False) -> dict[str, Any]:
    """Build a stable operation definition and its digest."""
    definition: dict[str, Any] = {
        "schema_version": SCHEMA,
        "tool": str(tool),
        "action": str(action),
        "execution": {"mode": mode if mode in {"local", "bridge"} else "local",
                       "script": script if isinstance(script, str) else None},
        "artifacts": {"inputs": _paths(inputs), "outputs": _paths(outputs)},
        "validation": _validation_summary(validation),
        "lifecycle": {"stages": list(STAGES)},
        "recovery": {"request_id": True, "reconnect": bool(bridge), "workflow": bool(workflow)},
    }
    encoded = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return definition | {"definition_digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


def validate_operation_contract(contract: Any, *, tool: str, action: str,
                                 script: str, inputs: list[str], outputs: list[str]) -> dict[str, Any]:
    """Validate the immutable operation-definition portion of a request."""
    if not isinstance(contract, dict) or contract.get("schema_version") != SCHEMA:
        raise ValueError("SOFTWARE_OPERATION_CONTRACT_UNSUPPORTED")
    if contract.get("tool") != tool or contract.get("action") != action:
        raise ValueError("SOFTWARE_OPERATION_CONTRACT_MISMATCH")
    execution = contract.get("execution") or {}
    artifacts = contract.get("artifacts") or {}
    if execution.get("script") != script or artifacts.get("inputs") != _paths(inputs) or artifacts.get("outputs") != _paths(outputs):
        raise ValueError("SOFTWARE_OPERATION_CONTRACT_MISMATCH")
    expected = build_operation_contract(tool=tool, action=action, script=script,
                                        inputs=inputs, outputs=outputs,
                                        validation=None, mode=execution.get("mode", "local"),
                                        bridge=bool((contract.get("recovery") or {}).get("reconnect")),
                                        workflow=bool((contract.get("recovery") or {}).get("workflow")))
    # The validation section is intentionally omitted from the digest check;
    # worker evidence adds input hashes after preflight. Compare the stable
    # structural fields and the caller-provided digest separately.
    for key in ("schema_version", "tool", "action", "execution", "artifacts", "lifecycle", "recovery"):
        if contract.get(key) != expected.get(key):
            raise ValueError("SOFTWARE_OPERATION_CONTRACT_MISMATCH")
    digest_source = build_operation_contract(
        tool=tool, action=action, script=script, inputs=inputs, outputs=outputs,
        validation=contract.get("validation"), mode=execution.get("mode", "local"),
        bridge=bool((contract.get("recovery") or {}).get("reconnect")),
        workflow=bool((contract.get("recovery") or {}).get("workflow")))
    if contract.get("definition_digest") != digest_source.get("definition_digest"):
        raise ValueError("SOFTWARE_OPERATION_CONTRACT_DIGEST_MISMATCH")
    return contract

