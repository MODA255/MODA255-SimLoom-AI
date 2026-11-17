"""Read-only checks for a planned CATIA/Ansys/Adams/VI-grade run.

The preflight intentionally does not start a vendor application.  It checks
the same action binding and file contract that execution will use, so a user
can fix configuration before spending time on a real solve.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .models import ExecutionPlan, PlanStep, ToolName
from .worker_protocol import WorkerError, relative_path
from .software_contract import build_operation_contract, normalize_capability_requirements


PREFLIGHT_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["schema_version", "steps", "ready", "ready_count", "step_count"],
    "properties": {
        "schema_version": {"type": "string", "const": "simloom-ai/software-preflight/v1"},
        "project_dir": {"type": "string"},
        "probe_requested": {"type": "boolean"},
        "plan": {"type": "object"},
        "plan_validation": {"type": "object"},
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "step_id": {"type": "string"}, "tool": {"type": "string"}, "action": {"type": "string"},
            "ready": {"type": "boolean"}, "issues": {"type": "array"},
            "contract": {"type": ["object", "null"]},
            "operation_contract": {"type": ["object", "null"], "properties": {
                "schema_version": {"type": "string", "const": "simloom-ai/software-operation-contract/v1"},
                "tool": {"type": "string"}, "action": {"type": "string"},
                "definition_digest": {"type": "string"}, "execution": {"type": "object"},
                "artifacts": {"type": "object"},
                "engineering_context": {"type": ["object", "null"]},
                "validation": {"type": ["object", "null"]},
                "lifecycle": {"type": "object"}, "recovery": {"type": "object", "properties": {
                    "request_id": {"type": "boolean"}, "reconnect": {"type": "boolean"},
                    "workflow": {"type": "boolean"}, "policy": {"type": ["object", "null"]},
                }},
                "requirements": {"type": ["object", "null"], "properties": {
                    "adapter_capabilities": {"type": "array", "items": {"type": "string"}},
                    "worker_features": {"type": "array", "items": {"type": "string"}},
                }},
            }},
        }}},
        "skipped_steps": {"type": "array", "items": {"type": "string"}},
        "ready_count": {"type": "integer", "minimum": 0},
        "step_count": {"type": "integer", "minimum": 0},
        "ready": {"type": "boolean"},
    },
}


def _command_status(command: Any) -> tuple[bool, str, str | None]:
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        return False, "COMMAND_ARGV_REQUIRED", None
    if not any("{script}" in item for item in command):
        return False, "COMMAND_SCRIPT_BINDING_REQUIRED", None
    resolved = shutil.which(command[0])
    if not resolved:
        return False, "COMMAND_EXECUTABLE_NOT_FOUND", None
    return True, "READY", resolved


def _script_status(root: Path, script: Any) -> tuple[bool, str, str | None]:
    if not isinstance(script, str) or not script:
        return False, "SCRIPT_REQUIRED", None
    try:
        relative_path(script)
    except WorkerError as exc:
        return False, getattr(exc, "code", "INVALID_SCRIPT_PATH"), None
    path = (root / script).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return False, "SCRIPT_MISSING", str(path)
    if not path.stat().st_size:
        return False, "SCRIPT_EMPTY", str(path)
    return True, "READY", str(path)


def _step_preflight(adapter: Any, step: PlanStep, root: Path, *, probe: bool = False,
                    workflow: bool = False) -> dict[str, Any]:
    binding = getattr(adapter, "config", {}).get("actions", {}).get(step.action)
    result: dict[str, Any] = {
        "step_id": step.id,
        "tool": step.tool.value,
        "action": step.action,
        "ready": False,
        "mode": "bridge" if getattr(adapter, "bridge_url", None) else "local",
        "issues": [],
        "script": None,
        "command": None,
        "outputs": list(step.outputs),
        "requirements": None,
    }
    if not isinstance(binding, dict):
        result["issues"].append({"code": "ACTION_BINDING_REQUIRED", "message": "未配置该软件动作"})
        return result

    requirements = normalize_capability_requirements(binding.get("capability_requirements"))
    result["requirements"] = {"declared": requirements, "status": "ready" if requirements else "not_requested"}
    missing_adapter = sorted(set(requirements.get("adapter_capabilities", [])) - set(getattr(adapter, "capabilities", frozenset())))
    if missing_adapter:
        result["requirements"].update(status="missing", missing_adapter_capabilities=missing_adapter)
        result["issues"].append({"code": "ADAPTER_CAPABILITY_MISSING", "message": ",".join(missing_adapter)})
    worker_features = requirements.get("worker_features", [])
    if worker_features and not getattr(adapter, "bridge_url", None):
        result["requirements"].update(status="missing", missing_worker_features=worker_features)
        result["issues"].append({"code": "WORKER_FEATURE_REQUIRES_BRIDGE", "message": ",".join(worker_features)})

    script_ok, script_code, script_path = _script_status(root, binding.get("script"))
    result["script"] = {"path": binding.get("script"), "resolved": script_path, "status": script_code}
    if not script_ok:
        result["issues"].append({"code": script_code, "message": "动作脚本不可用"})

    bridge_url = getattr(adapter, "bridge_url", None)
    if bridge_url:
        result["connection"] = {"mode": "bridge", "url": bridge_url, "status": "configured"}
        if probe:
            try:
                health = adapter.doctor(probe=True)
                result["connection"].update({"status": "reachable" if health.get("reachable") else "unreachable",
                                              "worker_platform": health.get("worker_platform"),
                                              "worker_action_found": step.action in health.get("actions", []),
                                              "worker_features": health.get("worker_features", [])})
                if not health.get("reachable"):
                    result["issues"].append({"code": "BRIDGE_UNREACHABLE", "message": "Windows worker 健康检查失败"})
                elif step.action not in health.get("actions", []):
                    result["issues"].append({"code": "WORKER_ACTION_NOT_FOUND", "message": "worker 未声明该软件动作"})
                if worker_features:
                    advertised = set(health.get("worker_features", []) or [])
                    missing_worker = sorted(set(worker_features) - advertised)
                    result["requirements"].update(status="ready" if not missing_worker and not missing_adapter else "missing")
                    if missing_worker:
                        result["requirements"]["missing_worker_features"] = missing_worker
                        result["issues"].append({"code": "WORKER_FEATURE_MISSING", "message": ",".join(missing_worker)})
            except Exception as exc:
                result["connection"].update(status="unreachable", error=str(exc))
                result["issues"].append({"code": "BRIDGE_UNREACHABLE", "message": str(exc)})
        elif worker_features:
            result["requirements"].update(status="not_probed", probe_required=True)
            result["issues"].append({"code": "WORKER_CAPABILITY_PROBE_REQUIRED",
                                     "message": "执行前必须探测远程 worker 能力"})
    else:
        command = binding.get("command")
        result["command"] = command
        command_ok, command_code, resolved = _command_status(command)
        result["command_status"] = {"status": command_code, "resolved": resolved}
        if not command_ok:
            result["issues"].append({"code": command_code, "message": "本机动作启动命令不可用"})

    try:
        # Reuse the execution adapter's exact output and evidence contract.
        script, inputs, outputs, validation, kind = adapter.file_contract(step, binding)
        result["contract"] = {"script": script, "inputs": inputs, "outputs": outputs,
                               "validation_kind": kind, "validation": validation}
        result["operation_contract"] = build_operation_contract(
            tool=step.tool.value, action=step.action, script=script, inputs=inputs,
            outputs=outputs, validation=validation,
            mode="bridge" if getattr(adapter, "bridge_url", None) else "local",
            bridge=bool(getattr(adapter, "bridge_url", None)), workflow=workflow,
            engineering_context=(binding.get("vendor_options") or {}).get("engineering_context")
            if isinstance(binding.get("vendor_options"), dict) else None,
            capability_requirements=requirements,
            recovery_policy=binding.get("recovery_policy"))
        result["outputs"] = outputs
    except Exception as exc:
        result["issues"].append({"code": getattr(exc, "code", "ACTION_CONTRACT_INVALID"), "message": str(exc)})

    result["ready"] = not result["issues"]
    result["status"] = "ready" if result["ready"] else "needs_configuration"
    return result


def preflight_plan(adapters: dict[ToolName, Any], plan: ExecutionPlan, project_dir: str | Path,
                   *, probe: bool = False, plan_validation: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    steps = [_step_preflight(adapters[step.tool], step, root, probe=probe, workflow=len(plan.steps) > 1) for step in plan.steps
             if step.tool in adapters and step.action != "clarify_request"]
    skipped = [step.id for step in plan.steps if step.action == "clarify_request"]
    ready_count = sum(bool(item["ready"]) for item in steps)
    return {
        "schema_version": "simloom-ai/software-preflight/v1",
        "project_dir": str(root),
        "probe_requested": probe,
        "plan": plan.to_dict(),
        "plan_validation": plan_validation or {"valid": True, "errors": []},
        "steps": steps,
        "skipped_steps": skipped,
        "ready_count": ready_count,
        "step_count": len(steps),
        "ready": bool(steps) and ready_count == len(steps) and not skipped,
    }
