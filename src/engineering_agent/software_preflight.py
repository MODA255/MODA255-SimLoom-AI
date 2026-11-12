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


def _step_preflight(adapter: Any, step: PlanStep, root: Path, *, probe: bool = False) -> dict[str, Any]:
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
    }
    if not isinstance(binding, dict):
        result["issues"].append({"code": "ACTION_BINDING_REQUIRED", "message": "未配置该软件动作"})
        return result

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
                                              "worker_action_found": step.action in health.get("actions", [])})
                if not health.get("reachable"):
                    result["issues"].append({"code": "BRIDGE_UNREACHABLE", "message": "Windows worker 健康检查失败"})
                elif step.action not in health.get("actions", []):
                    result["issues"].append({"code": "WORKER_ACTION_NOT_FOUND", "message": "worker 未声明该软件动作"})
            except Exception as exc:
                result["connection"].update(status="unreachable", error=str(exc))
                result["issues"].append({"code": "BRIDGE_UNREACHABLE", "message": str(exc)})
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
        result["outputs"] = outputs
    except Exception as exc:
        result["issues"].append({"code": getattr(exc, "code", "ACTION_CONTRACT_INVALID"), "message": str(exc)})

    result["ready"] = not result["issues"]
    result["status"] = "ready" if result["ready"] else "needs_configuration"
    return result


def preflight_plan(adapters: dict[ToolName, Any], plan: ExecutionPlan, project_dir: str | Path,
                   *, probe: bool = False, plan_validation: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    steps = [_step_preflight(adapters[step.tool], step, root, probe=probe) for step in plan.steps
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
