"""Build the stable, agent-facing capability matrix.

The adapter ``doctor()`` response is intentionally detailed and remains a
diagnostic record.  This module turns that record into a small, deterministic
contract that can be consumed by the CLI, Web workspace and MCP clients.

The matrix describes configuration and protocol evidence only.  A ready
action means that SimLoom can start the configured runner under the currently
observed conditions.  It does not mean that a licensed CATIA, Ansys, Adams or
VI-grade operation has produced an accepted engineering result.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA = "simloom-ai/capability-matrix/v1"
STATUSES = ("ready", "missing", "not_probed", "unavailable")
EVIDENCE_LEVELS = (
    "declared",
    "executable_found",
    "worker_reachable",
    "real_machine_attested",
)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({item for item in value if isinstance(item, str) and item})


def _reason(status: str, *, missing_adapter: list[str], missing_worker: list[str],
            discovered: bool, reachable: bool, bridge: bool) -> str:
    if status == "ready":
        if bridge and reachable:
            return "动作绑定、适配器能力和已探测的 worker 要求均满足"
        return "动作绑定和适配器能力满足，已找到本地启动程序"
    if status == "missing":
        missing = []
        if missing_adapter:
            missing.append("适配器能力：" + ", ".join(missing_adapter))
        if missing_worker:
            missing.append("worker 特性：" + ", ".join(missing_worker))
        return "缺少" + "；".join(missing) if missing else "动作能力检查未通过"
    if status == "not_probed":
        return "动作要求远程 worker 特性，但本次检查没有探测 worker"
    if discovered:
        if bridge and not reachable:
            return "动作已声明，但远程 worker 当前不可达或健康检查失败"
        return "动作已声明，但没有找到可用的启动命令"
    return "动作已声明，但没有可执行的绑定"


def _evidence_level(*, verified: bool, reachable: bool, discovered: bool) -> str:
    # The most conservative level wins.  ``real_machine_attested`` is only
    # emitted when the worker explicitly recorded evidence for this action.
    if verified:
        return "real_machine_attested"
    if reachable:
        return "worker_reachable"
    if discovered:
        return "executable_found"
    return "declared"


def _next_action(status: str, *, missing_adapter: list[str], missing_worker: list[str]) -> str:
    """Return a conservative discovery/configuration hint for an Agent."""
    if status == "ready":
        return "run_preflight"
    if status == "not_probed":
        return "probe_worker"
    if missing_adapter or missing_worker:
        return "configure_capabilities"
    if status == "unavailable":
        return "configure_action_binding"
    return "inspect_diagnostics"


def build_action_matrix(tool: str, diagnostic: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize one adapter diagnostic into deterministic action records."""
    if not isinstance(diagnostic, dict):
        return []
    requirements = diagnostic.get("action_requirements")
    if not isinstance(requirements, dict):
        requirements = {}
    discovered = set(_strings(diagnostic.get("actions")))
    verified = set(_strings(diagnostic.get("verified_actions")))
    reachable = diagnostic.get("reachable") is True
    bridge = bool(diagnostic.get("bridge_url"))
    records: list[dict[str, Any]] = []
    for name in sorted(requirements):
        entry = requirements.get(name)
        entry = entry if isinstance(entry, dict) else {}
        declared = entry.get("declared") if isinstance(entry.get("declared"), dict) else {}
        missing_adapter = _strings(entry.get("missing_adapter_capabilities"))
        missing_worker = _strings(entry.get("missing_worker_features"))
        status = entry.get("status") if entry.get("status") in STATUSES else "unavailable"
        is_verified = name in verified
        action_discovered = name in discovered
        record = {
            "tool": str(tool),
            "name": name,
            "status": status,
            "ready": status == "ready",
            "requirements": {key: _strings(value) for key, value in declared.items()
                             if key in {"adapter_capabilities", "worker_features"} and _strings(value)},
            "missing": {
                "adapter_capabilities": missing_adapter,
                "worker_features": missing_worker,
            },
            "discovered": action_discovered,
            "real_machine_verified": is_verified,
            "evidence": {
                "level": _evidence_level(verified=is_verified, reachable=reachable,
                                          discovered=action_discovered),
                "worker_reachable": reachable,
                "real_machine_verified": is_verified,
            },
        }
        record["reason"] = _reason(status, missing_adapter=missing_adapter,
                                   missing_worker=missing_worker,
                                   discovered=action_discovered, reachable=reachable,
                                   bridge=bridge)
        record["next_action"] = _next_action(status, missing_adapter=missing_adapter,
                                              missing_worker=missing_worker)
        records.append(record)
    return records


def summarize_action_matrix(actions: list[dict[str, Any]]) -> dict[str, int]:
    """Return stable counts used by UIs and agents without re-counting rules."""
    summary = {
        "total": len(actions),
        "ready": 0,
        "missing": 0,
        "not_probed": 0,
        "unavailable": 0,
        "discovered": 0,
        "real_machine_verified": 0,
    }
    for action in actions:
        status = action.get("status")
        if status in summary:
            summary[status] += 1
        if action.get("discovered"):
            summary["discovered"] += 1
        if action.get("real_machine_verified"):
            summary["real_machine_verified"] += 1
    return summary


def build_capability_matrix(diagnostics: list[dict[str, Any]], *, tool_order: tuple[str, ...],
                            probe: bool = True) -> dict[str, Any]:
    """Build the shared matrix portion embedded in ``capabilities()``."""
    by_tool = {item.get("tool"): item for item in diagnostics if isinstance(item, dict) and item.get("tool")}
    tools: dict[str, dict[str, Any]] = {}
    all_actions: list[dict[str, Any]] = []
    for tool in tool_order:
        diagnostic = by_tool.get(tool, {})
        actions = build_action_matrix(tool, diagnostic)
        tools[tool] = {
            "actions": actions,
            "summary": summarize_action_matrix(actions),
        }
        all_actions.extend(actions)
    matrix = {
        "schema_version": SCHEMA,
        "probe": bool(probe),
        "actions": all_actions,
        "summary": summarize_action_matrix(all_actions),
        "tools": tools,
    }
    encoded = json.dumps(matrix, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    matrix["matrix_digest"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return matrix

