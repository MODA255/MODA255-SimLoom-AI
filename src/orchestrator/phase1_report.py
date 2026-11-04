"""Durable phase-one report writer with conservative cleanup planning."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .daily_report import render_daily_report
from .phase1_suite import PHASE1_CHAIN_IDS, SUBMITTED_STATUSES, TERMINAL_STATUSES

_STATUS_KEYS = ("planned", "passed", "poll_timeout", "running", "unknown", "submitted", "terminal", "executed", "blocked", "inconclusive", "disabled")


def _cases(run: dict[str, Any]) -> list[dict[str, Any]]:
    return list(run.get("case_runs") or run.get("cases") or [])


def _case_counts(case_runs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "planned": len(case_runs),
        "passed": sum(item.get("status") == "passed" for item in case_runs),
        "poll_timeout": sum(item.get("status") == "poll_timeout" for item in case_runs),
        "running": sum(item.get("status") == "running" for item in case_runs),
        "unknown": sum(item.get("status") == "unknown" for item in case_runs),
        "submitted": sum(item.get("status") in SUBMITTED_STATUSES for item in case_runs),
        "terminal": sum(item.get("status") in TERMINAL_STATUSES for item in case_runs),
        "executed": sum(item.get("status") in {"completed", "passed"} and item.get("hard_acceptance") is True for item in case_runs),
        "blocked": sum(item.get("status") == "blocked" for item in case_runs),
        "inconclusive": sum(item.get("status") == "inconclusive" for item in case_runs),
        "disabled": sum(item.get("status") == "disabled" for item in case_runs),
    }


def build_phase1_report(run: dict[str, Any] | None) -> dict[str, Any]:
    run = dict(run or {})
    case_runs = _cases(run)
    by_chain = {chain_id: [] for chain_id in PHASE1_CHAIN_IDS}
    for item in case_runs:
        by_chain.setdefault(item.get("chain_id"), []).append(item)
    chains = []
    for chain_id in PHASE1_CHAIN_IDS:
        items = by_chain[chain_id]
        statuses = [str(item.get("status", "planned")) for item in items]
        if not statuses:
            status = "planned"
        elif all(value == "disabled" for value in statuses):
            status = "disabled"
        elif any(value == "blocked" for value in statuses):
            status = "blocked"
        elif any(value == "inconclusive" for value in statuses):
            status = "inconclusive"
        elif any(value == "poll_timeout" for value in statuses):
            status = "poll_timeout"
        elif any(value == "unknown" for value in statuses):
            status = "unknown"
        elif any(value == "running" for value in statuses):
            status = "running"
        elif all(value == "passed" for value in statuses):
            status = "passed"
        elif all(value in TERMINAL_STATUSES for value in statuses):
            status = "terminal"
        elif any(value in SUBMITTED_STATUSES for value in statuses):
            status = "submitted"
        else:
            status = "planned"
        chains.append({"chain_id": chain_id, "status": status, "case_count": len(items), "case_run_ids": [i.get("case_run_id") for i in items]})
    counts = {key: 0 for key in _STATUS_KEYS}
    # planned is a case-run count; chain count is represented by chain_ids.
    counts["planned"] = len(case_runs)
    for item in case_runs:
        status = item.get("status")
        if status in {"passed", "poll_timeout", "running", "unknown"}: counts[status] += 1
        if status in SUBMITTED_STATUSES: counts["submitted"] += 1
        if status in TERMINAL_STATUSES: counts["terminal"] += 1
        if status in {"completed", "passed"} and item.get("hard_acceptance") is True: counts["executed"] += 1
        if status in {"blocked", "inconclusive", "disabled"}: counts[status] += 1
    return {"run_id": run.get("run_id") or run.get("id"), "chain_ids": list(PHASE1_CHAIN_IDS), "chains": chains, "counts": counts, "case_counts": _case_counts(case_runs), "case_runs": case_runs}


def _cleanup_plan(resources: list[dict[str, Any]]) -> dict[str, Any]:
    actions = []
    for resource in resources:
        terminal = resource.get("terminal_status") in {"completed", "failed", "cancelled", "success", "error"}
        unknown = resource.get("status") == "submission_unknown" or not resource.get("resource_id")
        protected = bool(resource.get("protected")) or not terminal or unknown
        actions.append({"resource_id": resource.get("id"), "remote_resource_id": resource.get("resource_id"), "action": "protect" if protected else "retain", "protected": protected, "reason": resource.get("protection_reason") or ("unknown_or_non_terminal" if protected else "first_day_no_delete")})
    return {"status": "planned", "first_day": True, "remote_deletes_executed": False, "remote_writes": False, "actions": actions}


def write_phase1_report(report_input: dict[str, Any], runs_dir, *, resource_registry=None, preflight=None, run=None) -> dict[str, str]:
    report = build_phase1_report(report_input)
    run_id = report["run_id"] or "daily"
    root = Path(runs_dir) / run_id
    cases_root = root / "cases"
    cases_root.mkdir(parents=True, exist_ok=True)
    resources = resource_registry.list_resources(run_id) if resource_registry is not None else []
    cleanup = _cleanup_plan(resources)
    payloads = {"run": run or {"run_id": run_id, "status": report_input.get("status")}, "preflight": preflight or {}, "resource-registry": resources, "cleanup-plan": cleanup, "daily-report": report}
    files = {}
    for name, payload in payloads.items():
        path = root / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        files[name] = str(path)
    for case in report["case_runs"]:
        case_id = case.get("case_run_id") or case.get("case_id") or "unknown"
        case_dir = cases_root / str(case_id)
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md = root / "daily-report.md"
    md.write_text(render_daily_report({"run_id": run_id, "daily_report": {"overall": "phase1", "status_counts": report["counts"], "failure_count": report["counts"]["blocked"] + report["counts"]["inconclusive"], "failures": []}}), encoding="utf-8")
    files["resource_registry"] = files["resource-registry"]
    files["cleanup_plan"] = files["cleanup-plan"]
    files["daily_report"] = files["daily-report"]
    files["markdown"] = str(md)
    files["root"] = str(root)
    return files
