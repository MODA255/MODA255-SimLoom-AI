"""Build a redacted, agent-facing profile for software action bindings.

The runtime connection file intentionally stores only a small set of editable
connection fields.  Action definitions live in the main configuration because
they are reviewed engineering inputs: scripts, command templates, file
contracts, capability requirements and recovery policy.  This module joins the
two views into a deterministic profile that can be inspected from the Web
workbench or exported as a Windows-worker template.

The profile is a configuration diagnostic.  It does not start a process,
contact a worker, dispatch COM, or attest that CATIA, Ansys, Adams or VI-grade
is installed.  A later plan-specific preflight remains authoritative for the
exact inferred contract and project-relative file existence.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .software_contract import normalize_capability_requirements, normalize_recovery_policy


SCHEMA = "simloom-ai/action-profile/v1"
WORKER_CONFIG_SCHEMA = "simloom-ai/worker-config-template/v1"
OPERATION_CATALOG_SCHEMA = "simloom-ai/software-operation-catalog/v1"
TOOLS = ("catia", "ansys", "adams", "vi-grade")
_MAX_TEXT = 512
_SECRET_KEY = re.compile(r"(?:token|secret|password|passwd|api[_-]?key|private[_-]?key)", re.I)


def _text(value: Any, *, limit: int = _MAX_TEXT) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        return None
    return value


def _redact(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Copy JSON data while removing values that could contain credentials."""
    if depth > 8:
        return "[truncated]"
    if _SECRET_KEY.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(name): _redact(item, key=str(name), depth=depth + 1)
                for name, item in list(value.items())[:256]}
    if isinstance(value, list):
        return [_redact(item, key=key, depth=depth + 1) for item in value[:256]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > _MAX_TEXT:
            return value[:_MAX_TEXT] + "…"
        return value
    return str(value)[:_MAX_TEXT]


def _redacted_command(command: Any) -> list[str] | None:
    if not isinstance(command, list):
        return None
    result: list[str] = []
    for item in command[:64]:
        if not isinstance(item, str):
            continue
        text = item[:_MAX_TEXT]
        # Do not export inline credentials accidentally embedded in a command.
        if _SECRET_KEY.search(text):
            text = "[redacted]"
        result.append(text)
    return result or None


def _command_check(command: Any, *, remote: bool) -> dict[str, Any]:
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        return {"status": "missing", "code": "COMMAND_ARGV_REQUIRED", "valid_argv": False}
    if not any("{script}" in item for item in command):
        return {"status": "invalid", "code": "COMMAND_SCRIPT_BINDING_REQUIRED", "valid_argv": False}
    if remote:
        return {"status": "declared", "code": "WINDOWS_CHECK_DEFERRED", "valid_argv": True,
                "executable_check": "deferred_to_windows_worker"}
    executable = command[0]
    resolved = shutil.which(executable)
    # Windows absolute paths are not resolvable from a Mac control plane.  Keep
    # them as declarations and let the Windows worker perform the real check.
    if not resolved and (":" in executable or "\\" in executable):
        return {"status": "declared", "code": "WINDOWS_CHECK_DEFERRED", "valid_argv": True,
                "executable_check": "deferred_to_windows_worker"}
    if not resolved:
        return {"status": "missing", "code": "COMMAND_EXECUTABLE_NOT_FOUND", "valid_argv": True,
                "executable_check": "control_plane_path_lookup", "resolved": None}
    return {"status": "ready", "code": "READY", "valid_argv": True,
            "executable_check": "control_plane_path_lookup", "resolved": resolved}


def _script_check(script: Any, project_root: Path | None) -> dict[str, Any]:
    value = _text(script)
    result: dict[str, Any] = {"path": value, "status": "missing" if not value else "declared"}
    if not value:
        result["code"] = "SCRIPT_REQUIRED"
        return result
    if project_root is None:
        result["code"] = "PROJECT_CHECK_DEFERRED"
        result["path_check"] = "deferred_to_plan_preflight"
        return result
    candidate = (project_root / value).resolve()
    if not candidate.is_relative_to(project_root):
        result.update(status="invalid", code="INVALID_SCRIPT_PATH", resolved=str(candidate))
    elif not candidate.is_file():
        result.update(status="missing", code="SCRIPT_MISSING", resolved=str(candidate))
    elif candidate.stat().st_size == 0:
        result.update(status="invalid", code="SCRIPT_EMPTY", resolved=str(candidate))
    else:
        result.update(status="ready", code="READY", resolved=str(candidate), bytes=candidate.stat().st_size)
    return result


def _binding_digest(tool: str, action: str, binding: dict[str, Any]) -> str:
    payload = {"tool": tool, "action": action, "binding": _redact(binding)}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _operation_catalog(tool: str) -> list[dict[str, Any]]:
    """Return the supported vendor operation index without exposing full schemas.

    The action profile is intentionally useful before a runner is configured.
    Importing the registry lazily avoids an import cycle during module startup,
    while keeping the catalog sourced from the same typed operation registry
    that MCP ``tools/list`` and ``engineering://operations`` publish.
    """
    from .operations import OperationRegistry

    prefix = tool + "."
    catalog: list[dict[str, Any]] = []
    for operation in OperationRegistry().list():
        name = operation.get("name")
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        catalog.append({
            "name": name,
            "description": str(operation.get("description", ""))[:512],
            "version": str(operation.get("version", "1.0")),
            "compatible_versions": list(operation.get("compatible_versions", []))[:16],
            "required_parameters": list(operation.get("required_parameters", []))[:64],
            "output_kinds": list(operation.get("output_kinds", []))[:32],
            "safety": str(operation.get("safety", "safe")),
            "compatibility": _redact(operation.get("compatibility"))
            if operation.get("compatibility") else None,
        })
    return catalog


def _action_record(tool: str, action: str, binding: Any, *, config: dict[str, Any],
                   project_root: Path | None, adapter_capabilities: set[str],
                   worker_commands: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(binding, dict):
        return {"tool": tool, "name": action, "status": "missing", "binding_declared": False,
                "issues": [{"code": "ACTION_BINDING_REQUIRED", "message": "未配置该软件动作"}],
                "next_action": "configure_action_binding"}

    script = _script_check(binding.get("script"), project_root)
    inputs = [item for item in binding.get("inputs", []) if isinstance(item, str) and item][:256] \
        if isinstance(binding.get("inputs"), list) else []
    outputs = [item for item in binding.get("outputs", []) if isinstance(item, str) and item][:256] \
        if isinstance(binding.get("outputs"), list) else []
    requirements = normalize_capability_requirements(binding.get("capability_requirements"))
    missing_adapter = sorted(set(requirements.get("adapter_capabilities", [])) - adapter_capabilities)
    try:
        recovery = normalize_recovery_policy(binding.get("recovery_policy"))
        recovery_error = None
    except ValueError as exc:
        recovery, recovery_error = {}, str(exc)
    bridge_url = ((config.get("tools") or {}).get(tool) or {}).get("bridge_url")
    remote = bool(bridge_url)
    command_source = "windows_bridge.commands" if remote else "tools.actions.command"
    command = worker_commands.get(f"{tool}.{action}") if remote else binding.get("command")
    command_check = _command_check(command, remote=remote)
    issues: list[dict[str, str]] = []
    if script.get("status") in {"missing", "invalid"}:
        issues.append({"code": str(script.get("code", "SCRIPT_REQUIRED")), "message": "动作脚本不可用或未声明"})
    if missing_adapter:
        issues.append({"code": "ADAPTER_CAPABILITY_MISSING", "message": ",".join(missing_adapter)})
    if requirements.get("worker_features") and not remote:
        issues.append({"code": "WORKER_FEATURE_REQUIRES_BRIDGE", "message": ",".join(requirements["worker_features"])})
    if command_check["status"] in {"missing", "invalid"}:
        issues.append({"code": str(command_check["code"]), "message": "动作启动命令不可用"})
    if recovery_error:
        issues.append({"code": "INVALID_RECOVERY_POLICY", "message": recovery_error})
    if not outputs:
        # Typed runners can infer outputs from their validation kind.  Keep this
        # as a warning so the profile does not reject a valid typed action.
        issues.append({"code": "OUTPUTS_INFERRED_OR_REQUIRED", "message": "输出清单将在计划级 preflight 中补全或校验"})
    status = "ready" if not any(item["code"] in {
        "SCRIPT_REQUIRED", "SCRIPT_MISSING", "SCRIPT_EMPTY", "INVALID_SCRIPT_PATH",
        "COMMAND_ARGV_REQUIRED", "COMMAND_SCRIPT_BINDING_REQUIRED", "COMMAND_EXECUTABLE_NOT_FOUND",
        "ADAPTER_CAPABILITY_MISSING", "WORKER_FEATURE_REQUIRES_BRIDGE", "INVALID_RECOVERY_POLICY"
    } for item in issues) else "needs_configuration"
    if status == "ready" and command_check["status"] == "declared":
        status = "deferred_to_windows_worker"
    return {
        "tool": tool,
        "name": action,
        "status": status,
        "binding_declared": True,
        "script": script,
        "command": _redacted_command(command),
        "command_source": command_source,
        "command_check": command_check,
        "artifacts": {"inputs": inputs, "outputs": outputs,
                       "outputs_status": "declared" if outputs else "typed_runner_or_preflight"},
        "validation": _redact(binding.get("validation")),
        "requirements": {"declared": requirements,
                          "missing_adapter_capabilities": missing_adapter,
                          "status": "missing" if missing_adapter else "declared"},
        "recovery_policy": recovery,
        "recovery_policy_status": "invalid" if recovery_error else "declared" if recovery else "default",
        "binding_digest": _binding_digest(tool, action, binding),
        "issues": issues,
        "next_action": "run_plan_preflight" if not issues or status == "deferred_to_windows_worker" else "configure_action_binding",
        "preflight_note": "需要带具体工程目标执行 plan-level preflight，才能推导完整输入输出 contract。",
    }


def build_worker_config_template(config: dict[str, Any] | None) -> dict[str, Any]:
    """Build a redacted Windows worker configuration template.

    The returned object is intentionally not an assertion that the file is
    ready to launch.  A human must set a real worker token, verify paths on the
    Windows host and validate each command with the worker diagnostics endpoint.
    """
    config = config if isinstance(config, dict) else {}
    bridge = config.get("windows_bridge") if isinstance(config.get("windows_bridge"), dict) else {}
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    commands = bridge.get("commands") if isinstance(bridge.get("commands"), dict) else {}
    token_env = "SIMLOOM_BRIDGE_TOKEN"
    for name in TOOLS:
        item = tools.get(name) if isinstance(tools.get(name), dict) else {}
        value = _text(item.get("bridge_token_env"))
        if value:
            token_env = value
            break
    com = bridge.get("com") if isinstance(bridge.get("com"), dict) else {}
    return {
        "schema_version": WORKER_CONFIG_SCHEMA,
        "template_only": True,
        "host": _text(bridge.get("host")) or "127.0.0.1",
        "port": bridge.get("port") if isinstance(bridge.get("port"), int) else 8099,
        "root_dir": _text(bridge.get("root_dir")) or "C:/SimLoom/jobs",
        "timeout_seconds": bridge.get("timeout_seconds") if isinstance(bridge.get("timeout_seconds"), (int, float)) else 1800,
        "token": "<set-on-windows>",
        "token_env": token_env,
        "commands": {str(key): _redacted_command(value) for key, value in sorted(commands.items())},
        "com": _redact(com),
        "setup_checks": [
            "把 token 占位符替换为 Windows worker 的真实随机令牌，不要提交到 Git。",
            "在 Windows 主机上确认 root_dir、脚本解释器和每个 {script} 路径存在。",
            "启动 worker 后读取 /capabilities 和 /diagnostics，再执行项目级 action preflight。",
            "只有 machine-evidence/v1 和原生结果文件都通过，才可记录真实软件验收。",
        ],
        "limitations": [
            "这是配置模板，不会启动 worker 或商业软件。",
            "token_env 只是交付提示；当前 worker 仍需把实际 token 写入受保护配置或启动环境。",
            "命令路径、COM ProgID、许可证和软件版本必须在 Windows 实机确认。",
        ],
    }


def build_action_profile(config: dict[str, Any] | None, *, project_root: str | Path | None = None) -> dict[str, Any]:
    """Return a deterministic, redacted summary of all configured actions."""
    config = config if isinstance(config, dict) else {}
    root = Path(project_root).expanduser().resolve() if project_root else None
    tools_config = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    bridge = config.get("windows_bridge") if isinstance(config.get("windows_bridge"), dict) else {}
    worker_commands = bridge.get("commands") if isinstance(bridge.get("commands"), dict) else {}
    tool_profiles: dict[str, Any] = {}
    all_actions: list[dict[str, Any]] = []
    capability_map = {
        "catia": {"parametric-geometry", "assembly", "step-export", "iges-export"},
        "ansys": {"mesh", "static-structural", "modal", "thermal", "results"},
        "adams": {"multibody", "kinematics", "dynamics", "motion-analysis"},
        "vi-grade": {"vehicle-dynamics", "ride-handling", "lap-simulation", "vehicle-model"},
    }
    for tool in TOOLS:
        item = tools_config.get(tool) if isinstance(tools_config.get(tool), dict) else {}
        actions = item.get("actions") if isinstance(item.get("actions"), dict) else {}
        records = [_action_record(tool, action, actions[action], config=config, project_root=root,
                                  adapter_capabilities=capability_map[tool], worker_commands=worker_commands)
                   for action in sorted(actions)]
        operation_catalog = _operation_catalog(tool)
        tool_profiles[tool] = {
            "bridge_url": _text(item.get("bridge_url")) or "",
            "bridge_token_env": _text(item.get("bridge_token_env")) or "",
            "executable": _text(item.get("executable")) or "",
            "mode": "bridge" if item.get("bridge_url") else "local",
            "actions": records,
            "action_count": len(records),
            "operation_catalog": operation_catalog,
            "operation_catalog_count": len(operation_catalog),
        }
        all_actions.extend(records)
    profile = {
        "schema_version": SCHEMA,
        "operation_catalog_schema": OPERATION_CATALOG_SCHEMA,
        "project_root": str(root) if root else None,
        "configuration_scope": "declarative_action_bindings",
        "tools": tool_profiles,
        "windows_bridge": {
            "host": _text(bridge.get("host")) or "",
            "port": bridge.get("port") if isinstance(bridge.get("port"), int) else None,
            "root_dir": _text(bridge.get("root_dir")) or "",
            "timeout_seconds": bridge.get("timeout_seconds"),
            "command_keys": sorted(str(key) for key in worker_commands),
            "commands": {str(key): _redacted_command(value) for key, value in sorted(worker_commands.items())},
            "com_prog_ids": sorted(str(key) for key in (bridge.get("com") or {}) if isinstance(key, str)),
        },
        "worker_config_template": build_worker_config_template(config),
        "actions": all_actions,
        "summary": {
            "tools": len(TOOLS),
            "actions": len(all_actions),
            "declared": sum(1 for item in all_actions if item.get("binding_declared")),
            "ready": sum(1 for item in all_actions if item.get("status") == "ready"),
            "needs_configuration": sum(1 for item in all_actions if item.get("status") == "needs_configuration"),
            "windows_check_deferred": sum(1 for item in all_actions if item.get("status") == "deferred_to_windows_worker"),
            "catalog_operations": sum(item["operation_catalog_count"] for item in tool_profiles.values()),
        },
        "limitations": [
            "动作绑定摘要不启动商业软件，也不连接 Windows worker。",
            "脚本和项目文件的最终检查必须通过带工程目标的 plan-level preflight。",
            "配置存在、命令可解析或 worker 可达都不能替代真实软件许可证、求解和结果证据。",
        ],
    }
    encoded = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    profile["profile_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return profile

