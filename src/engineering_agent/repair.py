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
            return {"status": entry.get("status"), "error_code": _error_code(entry, state),
                    "vendor_validation": vendor if isinstance(vendor, dict) else None,
                    "verification": details.get("verification"),
                    "message": result.get("message")}
    return {"status": entry.get("status"), "error_code": _error_code(entry, state),
            "vendor_validation": None, "verification": None,
            "message": entry.get("error", {}).get("message") if isinstance(entry.get("error"), dict) else None}


def _failed_metrics(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    vendor = evidence.get("vendor_validation") or {}
    return [item for item in vendor.get("metric_verdicts", [])
            if isinstance(item, dict) and item.get("passed") is False]


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
    """Return at most one candidate per failed step, in plan order."""
    history = state.get("repair_history", [])
    attempts = {item.get("parent_workflow_id", state.get("workflow_id")): 0 for item in history}
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
            break
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
