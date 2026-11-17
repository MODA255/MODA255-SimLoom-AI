"""Bounded, evidence-driven repair proposals for vendor workflows.

The agent may choose only strategies explicitly declared by the engineer in an
adapter binding. It never loosens an engineering requirement or invents a
vendor command. A repair creates a child workflow with new output paths and
keeps the failed parent immutable.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from .models import ExecutionPlan, PlanStep
from .worker_protocol import WorkerError

SCHEMA = "simloom-ai/repair/v1"
COMPARISON_SCHEMA = "simloom-ai/repair-comparison/v1"
_RESERVED = {
    "input_model", "native_output", "step_output", "db_file", "result_output",
    "command_file", "scenario_file", "metrics_output", "report_output",
    "required_metrics", "metric_units", "metric_rules", "analysis_name",
    "validation", "vendor_options", "inputs", "outputs", "source_prompt",
}


@dataclass(frozen=True)
class RepairCandidate:
    strategy_id: str
    step_id: str
    reason: str
    parameter_patch: dict[str, Any]
    evidence: dict[str, Any]
    max_attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA, "strategy_id": self.strategy_id,
                "step_id": self.step_id, "reason": self.reason,
                "parameter_patch": copy.deepcopy(self.parameter_patch),
                "evidence": copy.deepcopy(self.evidence), "max_attempts": self.max_attempts}


def _envelope(evidence: dict[str, Any] | None) -> dict[str, Any]:
    """Return the normalized evidence payload used for comparison.

    Older adapters stored the result under ``vendor_validation`` while newer
    workers expose ``engineering_result``.  Repair decisions must compare the
    same fields regardless of which producer wrote the workflow state.
    """
    if not isinstance(evidence, dict):
        return {}
    values = [(key, evidence.get(key)) for key in ("engineering_result", "vendor_validation")]
    # A legacy validator may be added beside an older ``engineering_result``
    # envelope whose acceptance is still ``not_requested``.  Prefer the
    # payload with actual independent metric evidence in that case.
    for _key, value in values:
        if isinstance(value, dict) and (
                isinstance(value.get("metric_verdicts"), list) and value.get("metric_verdicts")
                or value.get("engineering_acceptance") in {"passed", "failed"}):
            return value
    for _key, value in values:
        if isinstance(value, dict):
            return value
    return {}


def _metric_verdicts(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    payload = _envelope(evidence)
    raw = payload.get("metric_verdicts")
    if not isinstance(raw, list):
        return []
    return [item for item in raw[:256] if isinstance(item, dict) and isinstance(item.get("name"), str)]


def _acceptance(evidence: dict[str, Any] | None) -> str:
    value = _envelope(evidence).get("engineering_acceptance")
    return value if value in {"passed", "failed", "unknown", "not_requested"} else "unknown"


def _state_evidence(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect one bounded engineering evidence record for each plan step."""
    result: dict[str, dict[str, Any]] = {}
    report = state.get("report")
    if not isinstance(report, dict):
        return result
    for item in report.get("results", []):
        if not isinstance(item, dict) or not isinstance(item.get("step_id"), str):
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        result[item["step_id"]] = {
            "status": item.get("status"),
            "engineering_result": details.get("engineering_result"),
            "vendor_validation": details.get("vendor_validation"),
            "verification": details.get("verification"),
            "message": item.get("message"),
        }
    return result


def _overall_acceptance(state: dict[str, Any]) -> str:
    values = [_acceptance(item) for item in _state_evidence(state).values()]
    if not values:
        return "unknown"
    if "failed" in values:
        return "failed"
    if all(value in {"passed", "not_requested"} for value in values) and "passed" in values:
        return "passed"
    if "unknown" in values:
        return "unknown"
    return "not_requested"


def _metric_direction(operator: Any) -> str | None:
    if operator in {"<", "<="}:
        return "lower"
    if operator in {">", ">="}:
        return "higher"
    if operator in {"=", "=="}:
        return "closer"
    return None


def _metric_detail(parent: dict[str, Any] | None, child: dict[str, Any] | None) -> dict[str, Any]:
    """Compare one metric without treating a missing value as a pass."""
    parent = parent if isinstance(parent, dict) else {}
    child = child if isinstance(child, dict) else {}
    name = child.get("name") or parent.get("name")
    operator = child.get("operator", parent.get("operator"))
    target = child.get("target", parent.get("target"))
    unit = child.get("unit", parent.get("unit", ""))
    result: dict[str, Any] = {
        "name": name,
        "operator": operator,
        "target": target,
        "unit": unit,
        "parent": {"value": parent.get("value"), "passed": parent.get("passed")},
        "child": {"value": child.get("value"), "passed": child.get("passed")},
        "trend": "unknown",
        "comparable": True,
    }
    parent_unit, child_unit = parent.get("unit"), child.get("unit")
    if parent_unit and child_unit and parent_unit != child_unit:
        result["comparable"] = False
        result["reason"] = "unit_mismatch"
        return result
    if parent.get("passed") is False and child.get("passed") is True:
        result["trend"] = "improved"
        return result
    if parent.get("passed") is True and child.get("passed") is False:
        result["trend"] = "regressed"
        return result
    try:
        old_value, new_value = float(parent["value"]), float(child["value"])
    except (KeyError, TypeError, ValueError):
        if parent.get("passed") == child.get("passed") and parent.get("passed") in {True, False}:
            result["trend"] = "unchanged"
        return result
    direction = _metric_direction(operator)
    if direction == "lower":
        result["delta"] = new_value - old_value
        result["trend"] = "improved" if new_value < old_value else "regressed" if new_value > old_value else "unchanged"
    elif direction == "higher":
        result["delta"] = new_value - old_value
        result["trend"] = "improved" if new_value > old_value else "regressed" if new_value < old_value else "unchanged"
    elif direction == "closer":
        try:
            old_distance, new_distance = abs(old_value - float(target)), abs(new_value - float(target))
        except (TypeError, ValueError):
            return result
        result["delta"] = new_value - old_value
        result["distance_to_target"] = {"parent": old_distance, "child": new_distance}
        result["trend"] = "improved" if new_distance < old_distance else "regressed" if new_distance > old_distance else "unchanged"
    elif parent.get("passed") == child.get("passed") and parent.get("passed") in {True, False}:
        result["trend"] = "unchanged"
    return result


def compare_repair_results(parent: dict[str, Any], child: dict[str, Any],
                           candidate: RepairCandidate | dict[str, Any] | None = None) -> dict[str, Any]:
    """Compare a repair child with its immutable parent using engineering evidence.

    This comparison is intentionally descriptive.  It never changes thresholds,
    treats process completion as engineering acceptance, or declares a result
    valid when the child has no independent metric evidence.
    """
    parent_steps, child_steps = _state_evidence(parent), _state_evidence(child)
    parent_metrics = {(step_id, item.get("name")): item
                      for step_id, evidence in parent_steps.items() for item in _metric_verdicts(evidence)}
    child_metrics = {(step_id, item.get("name")): item
                     for step_id, evidence in child_steps.items() for item in _metric_verdicts(evidence)}
    metrics: list[dict[str, Any]] = []
    for key in sorted(set(parent_metrics) | set(child_metrics)):
        item = _metric_detail(parent_metrics.get(key), child_metrics.get(key))
        item["step_id"] = key[0]
        metrics.append(item)
    counts = {name: sum(item["trend"] == name for item in metrics)
              for name in ("improved", "regressed", "unchanged", "unknown")}
    parent_acceptance, child_acceptance = _overall_acceptance(parent), _overall_acceptance(child)
    if child_acceptance == "passed" and parent_acceptance != "passed":
        outcome, next_action = "accepted", "stop_success"
    elif child_acceptance == "failed" and parent_acceptance == "passed":
        outcome, next_action = "regressed", "manual_review"
    elif child_acceptance == "failed" and counts["regressed"] > 0:
        outcome, next_action = "regressed", "manual_review"
    elif child_acceptance == "failed" and counts["improved"] > 0:
        outcome, next_action = "improved", "evaluate_next_candidate"
    elif child_acceptance == "failed" and metrics:
        outcome, next_action = "unchanged", "evaluate_next_candidate"
    else:
        outcome, next_action = "needs_review", "manual_review"
    return {
        "schema_version": COMPARISON_SCHEMA,
        "parent_workflow_id": parent.get("workflow_id"),
        "child_workflow_id": child.get("workflow_id"),
        "candidate": candidate.to_dict() if isinstance(candidate, RepairCandidate) else copy.deepcopy(candidate),
        "parent": {"status": parent.get("status"), "engineering_acceptance": parent_acceptance},
        "child": {"status": child.get("status"), "engineering_acceptance": child_acceptance},
        "metrics": metrics[:256],
        "counts": counts,
        "outcome": outcome,
        "next_action": next_action,
        "should_continue": next_action == "evaluate_next_candidate",
    }


def decide_repair(comparison: dict[str, Any], remaining_candidates: int) -> dict[str, Any]:
    """Apply explicit stop conditions after a child result is available."""
    if comparison.get("outcome") == "accepted":
        return {"status": "stopped", "reason": "engineering_acceptance_passed", "next_action": "stop_success"}
    if comparison.get("outcome") == "regressed":
        return {"status": "stopped", "reason": "child_regressed_against_parent", "next_action": "manual_review"}
    if comparison.get("outcome") in {"improved", "unchanged"} and remaining_candidates > 0:
        return {"status": "continue_allowed", "reason": "remaining_declared_candidates", "next_action": "evaluate_next_candidate"}
    return {"status": "stopped", "reason": "no_safe_next_candidate_or_insufficient_evidence", "next_action": "manual_review"}


def _error_code(entry: dict[str, Any], state: dict[str, Any]) -> str | None:
    error = entry.get("error") or state.get("error") or {}
    if isinstance(error, dict):
        return error.get("code")
    return None


def _step_evidence(state: dict[str, Any], step_id: str) -> dict[str, Any]:
    entry = state.get("steps", {}).get(step_id, {})
    report = state.get("report", {})
    for result in report.get("results", []):
        if result.get("step_id") == step_id:
            details = result.get("details") or {}
            vendor = details.get("vendor_validation")
            normalized = details.get("engineering_result")
            # New workers may expose only the unified envelope.  Present it
            # through the historical evidence slot so existing strategy
            # matching remains compatible while agents can migrate gradually.
            if not isinstance(vendor, dict) and isinstance(normalized, dict):
                vendor = normalized
            return {"status": entry.get("status"), "error_code": _error_code(entry, state),
                    "vendor_validation": vendor if isinstance(vendor, dict) else None,
                    "engineering_result": normalized if isinstance(normalized, dict) else None,
                    "verification": details.get("verification"),
                    "message": result.get("message")}
    return {"status": entry.get("status"), "error_code": _error_code(entry, state),
            "vendor_validation": None, "verification": None,
            "engineering_result": None,
            "message": entry.get("error", {}).get("message") if isinstance(entry.get("error"), dict) else None}


def _failed_metrics(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _metric_verdicts(evidence) if item.get("passed") is False]


def _matches(when: dict[str, Any], evidence: dict[str, Any], metric: dict[str, Any] | None) -> bool:
    if not isinstance(when, dict):
        return False
    code = when.get("error_code")
    if code is not None and code != evidence.get("error_code"):
        return False
    status = when.get("status")
    if status is not None and status != evidence.get("status"):
        return False
    if when.get("failed") is True and not metric:
        return False
    if "metric" in when and (not metric or when["metric"] != metric.get("name")):
        return False
    if "operator" in when and (not metric or when["operator"] != metric.get("operator")):
        return False
    return bool(metric) or code is not None or status is not None


def _validate_patch(patch: Any, step: PlanStep) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise WorkerError("REPAIR_PATCH_REQUIRED", step.id)
    result = copy.deepcopy(patch)
    for key in result:
        if key in _RESERVED or key.startswith("_"):
            raise WorkerError("REPAIR_PATCH_IMMUTABLE_PARAMETER", key)
        if key not in step.parameters:
            raise WorkerError("REPAIR_PATCH_PARAMETER_UNKNOWN", f"{step.id}:{key}")
    return result


def propose_repairs(state: dict[str, Any], plan: ExecutionPlan, agent) -> list[RepairCandidate]:
    """Return all matching engineer-declared candidates, in plan order."""
    candidates: list[RepairCandidate] = []
    for step in plan.steps:
        evidence = _step_evidence(state, step.id)
        adapter = agent.adapters[step.tool]
        binding = adapter.config.get("actions", {}).get(step.action) or {}
        strategies = binding.get("repair_strategies", [])
        if not isinstance(strategies, list):
            continue
        metrics = _failed_metrics(evidence)
        for raw in strategies:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            when = raw.get("when") or {}
            matched_metric = next((item for item in metrics if _matches(when, evidence, item)), None)
            if not _matches(when, evidence, matched_metric):
                continue
            patch = _validate_patch(raw.get("parameter_patch"), step)
            max_attempts = raw.get("max_attempts", 1)
            if type(max_attempts) is not int or max_attempts < 1 or max_attempts > 3:
                raise WorkerError("REPAIR_ATTEMPT_LIMIT_INVALID", raw["id"])
            evidence_copy = copy.deepcopy(evidence)
            if matched_metric:
                evidence_copy["failed_metric"] = copy.deepcopy(matched_metric)
            candidates.append(RepairCandidate(raw["id"], step.id,
                                               str(raw.get("reason") or "按工程师声明的修复策略调整参数"),
                                               patch, evidence_copy, max_attempts))
    return candidates


def remap_value(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [remap_value(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: remap_value(item, mapping) for key, item in value.items()}
    return value


def build_repair_plan(plan: ExecutionPlan, candidate: RepairCandidate, parent_id: str, attempt_id: str) -> ExecutionPlan:
    """Clone a plan, remap every produced file, and apply one approved patch."""
    output_paths = {output for step in plan.steps for output in step.outputs}
    prefix = f"artifacts/repairs/{parent_id}/{attempt_id}/"
    mapping = {path: prefix + path for path in output_paths}
    steps: list[PlanStep] = []
    found = False
    for source in plan.steps:
        step = copy.deepcopy(source)
        step.inputs = [mapping.get(path, path) for path in step.inputs]
        step.outputs = [mapping.get(path, path) for path in step.outputs]
        step.parameters = remap_value(step.parameters, mapping)
        step.parameters["repair_parent_workflow_id"] = parent_id
        step.parameters["repair_attempt_id"] = attempt_id
        if step.id == candidate.step_id:
            step.parameters.update(copy.deepcopy(candidate.parameter_patch))
            found = True
        steps.append(step)
    if not found:
        raise WorkerError("REPAIR_STEP_NOT_FOUND", candidate.step_id)
    request = copy.deepcopy(plan.request)
    request.parameters = remap_value(request.parameters, mapping)
    request.parameters["repair_parent_workflow_id"] = parent_id
    request.parameters["repair_attempt_id"] = attempt_id
    assumptions = list(plan.assumptions) + [
        f"修复策略 {candidate.strategy_id} 基于原工程证据生成；原工作流 {parent_id} 保留。"
    ]
    return ExecutionPlan(request, steps, assumptions)
