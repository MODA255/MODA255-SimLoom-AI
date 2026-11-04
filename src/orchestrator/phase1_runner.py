"""Phase one production-safe scheduler.

The runner deliberately separates planning/readiness from execution.  No adapter is
constructed or called until the complete, explicit phase-one configuration passes
validation.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Callable

from .phase1_suite import (
    CONDITIONAL_CHAINS,
    MANDATORY_REAL_CHAINS,
    PHASE1_CHAIN_IDS,
    derive_case_runs,
    dependency_satisfied,
    load_phase1_suite,
    validate_phase1_resources,
)

PHASE1_ALLOWLIST = tuple(PHASE1_CHAIN_IDS)
BATCHES = (("ingest", ("I-LINK", "I-LIVE", "I-UPLOAD")),
           ("manual", ("M-ECOM", "M-IP", "M-THEME")),
           ("automation", ("A-ECOM", "A-IP", "A-THEME")))

# These are read-only prerequisites for a production phase-one readiness check.
# Keep them here (rather than relying on whichever runner happens to execute) so
# a missing route blocks before any remote write can be attempted.
I_LINK_REQUIRED_ROUTES = (
    "vfc_config", "import_url", "list_assets", "asset_detail",
    "asset_progress", "asset_progress_phases",
)
READ_ONLY_REMOTE_ROUTES = (
    "vfc_config", "list_assets", "asset_detail", "asset_progress",
    "asset_progress_phases",
)
AUTOMATION_REQUIRED_ROUTES = {
    "A-ECOM": ("get_task", "get_automation_stages"),
    "A-THEME": ("get_task", "get_automation_stages"),
    "A-IP": ("get_ip_promo_task", "get_ip_promo_phases"),
}
SOURCE_PROBE_ROUTE_ALIASES = ("source_probe", "probe_source", "source_search")


def _test_runner_mode(config: dict[str, Any], suite, runners) -> bool:
    """Allow injected test runners to exercise scheduling with fixture inputs.

    This is intentionally narrow: it requires an explicitly passed suite,
    injected runners, and safe_write at both config and suite boundaries.  The
    CLI production path never supplies these test hooks and therefore keeps the
    full resource/readiness gate.
    """
    return (bool(runners)
            and _config_safe_write(config)
            and (suite is None or (isinstance(suite, dict) and suite.get("safe_write") is True)))


def _suite_source(config: dict[str, Any], suite=None):
    if suite is not None:
        return suite
    # Prefer an explicitly nested suite so top-level orchestration settings
    # (including enabled_chains) cannot accidentally shadow its allowlist.
    phase = config.get("phase1")
    if isinstance(phase, dict) and phase.get("suite"):
        source = phase["suite"]
        if isinstance(source, (str, Path)):
            from .phase1_suite import _read_config
            suite_path = Path(source)
            if not suite_path.is_absolute():
                source_dir = config.get("_source_dir")
                if source_dir:
                    relative_candidate = Path(source_dir) / suite_path
                    suite_path = relative_candidate if relative_candidate.exists() else suite_path
            loaded = _read_config(suite_path)
            loaded["_env_values"] = {**config.get("_env_values", {}), **loaded.get("_env_values", {})}
            return loaded
        return source
    # A phase-one suite is itself a valid top-level configuration.
    if isinstance(config, dict) and config.get("chains"):
        return config
    raise ValueError("explicit phase1 configuration is required")


def _config_safe_write(config: dict[str, Any]) -> bool:
    phase = config.get("phase1", {}) if isinstance(config, dict) else {}
    if isinstance(phase, dict) and "safe_write" in phase:
        return phase["safe_write"] is True
    return isinstance(config, dict) and config.get("safe_write") is True


def _safe_write_allowed(config: dict[str, Any], adapter) -> bool:
    # Writes are opt-in at both boundaries.  Missing, false, or non-boolean
    # values are deliberately denied; neither side may grant the other side's
    # missing consent.
    return _config_safe_write(config) and getattr(adapter, "safe_write", None) is True


def _chain_batch(chain_id: str) -> str:
    return next(batch for batch, ids in BATCHES if chain_id in ids)


def _resolve_case_timeout(case: dict[str, Any], phase: dict[str, Any] | None = None, global_config: dict[str, Any] | None = None) -> int | float:
    """Resolve timeout from the narrowest scope to the global default."""
    for source in (case, phase or {}, global_config or {}):
        value = source.get("timeout_seconds") if isinstance(source, dict) else None
        if value is not None and not isinstance(value, bool):
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return int(value) if value.is_integer() else value
    return 1800


def _write_case_snapshot(output_dir, run_id, case_run_id, payload):
    if not output_dir or not case_run_id:
        return
    path = Path(output_dir) / str(run_id) / "cases" / str(case_run_id) / "progress.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def build_phase1_plan(suite: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Build a fresh plan; it never copies a historical task identifier."""
    chains = []
    all_runs = []
    for batch, ids in BATCHES:
        for chain_id in ids:
            chain = next((copy.deepcopy(c) for c in suite.get("chains", []) if c.get("chain_id") == chain_id), None)
            if chain is None:
                continue
            chains.append({"chain_id": chain_id, "batch": batch, "runner": chain.get("runner"), "status": "planned"})
            all_runs.extend(derive_case_runs({"chains": [chain]}, run_id, batch))
    return {"run_id": run_id, "status": "planned", "chains": chains, "case_runs": all_runs,
            "batches": [name for name, _ in BATCHES], "allowlist": list(PHASE1_ALLOWLIST)}


def _readiness_routes(config: dict[str, Any]) -> dict[str, Any]:
    """Return every configured route relevant to the phase-one readiness gate."""
    remote_routes = (config.get("remote_api") or {}).get("routes") or {}
    automation_routes = config.get("automation_routes") or {}
    routes = {name: remote_routes[name] for name in (*I_LINK_REQUIRED_ROUTES, *SOURCE_PROBE_ROUTE_ALIASES, "upload_file", "ingest_file") if name in remote_routes}
    routes.update({name: automation_routes[name] for name in ("get_task", "get_automation_stages", "get_ip_promo_task", "get_ip_promo_phases") if name in automation_routes})
    return routes


def _route_error(name: str, route: Any) -> str | None:
    try:
        method, path = route if isinstance(route, (list, tuple)) else str(route).split(" ", 1)
        if str(method).upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"} or not str(path).startswith("/"):
            raise ValueError
    except (ValueError, TypeError):
        return f"invalid readiness route: {name}"
    return None


def _validate_readiness_routes(config: dict[str, Any]) -> list[str]:
    """Validate the declared route contract locally; never make a request."""
    errors: list[str] = []
    remote_routes = (config.get("remote_api") or {}).get("routes") or {}
    automation_routes = config.get("automation_routes") or {}
    missing = [name for name in I_LINK_REQUIRED_ROUTES if name not in remote_routes]
    if "I-UPLOAD" in (config.get("enabled_chains") or []) and "upload_file" not in remote_routes and "ingest_file" not in remote_routes:
        missing.append("upload_file")
    # A remote source-probe route is optional evidence only. I-LIVE readiness is
    # based on the local runner and resolved inputs, not an unimplemented API.
    for chain_id, required in AUTOMATION_REQUIRED_ROUTES.items():
        for name in required:
            if name not in automation_routes:
                missing.append(name)
    errors.extend(f"readiness routes missing: {name}" for name in sorted(set(missing)))
    expected_methods = {name: "GET" for name in READ_ONLY_REMOTE_ROUTES}
    expected_methods["import_url"] = "POST"
    expected_methods["upload_file"] = "POST"
    expected_methods["ingest_file"] = "POST"
    expected_methods.update({name: "GET" for names in AUTOMATION_REQUIRED_ROUTES.values() for name in names})
    for name, route in _readiness_routes(config).items():
        error = _route_error(name, route)
        if error:
            errors.append(error)
            continue
        method = route[0] if isinstance(route, (list, tuple)) else str(route).split(" ", 1)[0]
        expected = expected_methods.get(name)
        if expected and str(method).upper() != expected:
            errors.append(f"readiness route method must be {expected}: {name}")
    return errors


def _local_test_inputs(config: dict[str, Any], loaded: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expose local readiness facts without contacting any remote endpoint."""
    phase = config.get("phase1") if isinstance(config.get("phase1"), dict) else {}
    live_cases = []
    for chain in (loaded or {}).get("chains", []):
        if chain.get("chain_id") == "I-LIVE":
            live_cases.extend(chain.get("cases") or [])
    try:
        from .live_source_selector import choose_live_source
        probe_runner_available = callable(choose_live_source)
    except Exception:
        probe_runner_available = False
    live_inputs_configured = any(
        case.get("input") or case.get("source_url") or case.get("source_urls")
        or (case.get("payload") or {}).get("url")
        for case in live_cases
    )
    return {"suite": phase.get("suite"), "source_dir": config.get("_source_dir"),
            "routes": sorted(_readiness_routes(config)),
            "live_source_probe_runner": probe_runner_available,
            "live_source_inputs_configured": live_inputs_configured}


def phase1_readiness(config: dict[str, Any], *, suite=None, adapter=None, strict: bool | None = None) -> dict[str, Any]:
    """Read-only local/configuration gate.  Malformed input is always a safe block."""
    try:
        source = _suite_source(config, suite)
        loaded = load_phase1_suite(source)
        # Legacy structured test suites often intentionally omit live inputs.  Keep
        # their local shape/readiness contract, while every referenced production
        # suite still gets the resource gate below.
        is_legacy_structured = suite is None and isinstance(config, dict) and config.get("chains") and not config.get("release_mode")
        if strict is None:
            strict = not (suite is None and isinstance(config, dict) and config.get("chains"))
        resource_errors = [] if (is_legacy_structured or not strict) else validate_phase1_resources(loaded)
        if strict and (config.get("mode") == "remote_live" or config.get("target")):
            # Route contract is checked first so every missing capability is
            # reported structurally, even when other production inputs are also
            # incomplete.  This remains a read-only gate with writes=0.
            route_errors = _validate_readiness_routes(config)
            if route_errors:
                resource_errors = route_errors + resource_errors
        if resource_errors:
            return {"status": "blocked", "reason": "production inputs are not ready", "errors": resource_errors, "writes": 0,
                    "local_test": _local_test_inputs(config, loaded)}
        ids = [c.get("chain_id") for c in loaded.get("chains", [])]
        enabled = config.get("enabled_chains", PHASE1_ALLOWLIST)
        if set(ids) != set(PHASE1_ALLOWLIST) or set(enabled) != set(PHASE1_ALLOWLIST):
            return {"status": "blocked", "reason": "phase1 allowlist does not contain exactly nine chains",
                    "errors": ["phase1 allowlist does not contain exactly nine chains"], "writes": 0}
        if (set(loaded.get("mandatory_real_chains", [])) != set(MANDATORY_REAL_CHAINS)
                or set(loaded.get("conditional_chains", [])) != set(CONDITIONAL_CHAINS)):
            return {"status": "blocked", "reason": "phase1 allowlist mismatch",
                    "errors": ["phase1 allowlist mismatch"], "writes": 0}
        phase = config.get("phase1", {})
        if phase.get("enabled", True) is False:
            return {"status": "blocked", "reason": "phase1 is disabled",
                    "errors": ["phase1 is disabled"], "writes": 0}
        result = {"status": "ready", "reason": "ok", "writes": 0,
                  "chains": list(PHASE1_ALLOWLIST), "suite_id": loaded.get("suite_id"),
                  "local_test": _local_test_inputs(config, loaded)}
        return result
    except Exception as exc:
        reason = str(exc) or type(exc).__name__
        if "chains" in reason or "enabled_chains" in reason:
            reason = f"phase1 allowlist: {reason}"
        return {"status": "blocked", "reason": reason,
                "errors": [f"{type(exc).__name__}: {reason}"], "writes": 0}


def _evidence(kind, status, **extra):
    return {"kind": kind, "status": status, **extra}


def _summarize_chain_statuses(statuses: list[str]) -> str:
    if statuses and all(item == "passed" for item in statuses):
        return "passed"
    if statuses and all(item == "blocked" for item in statuses):
        return "blocked"
    if any(item in {"inconclusive", "poll_timeout"} for item in statuses):
        return "inconclusive"
    return "failed"


def _has_ingest_capability(adapter) -> bool:
    try:
        return callable(getattr(adapter, "ingest_file", None))
    except Exception:
        return False


def _run_ingest(adapter, chain, output_dir):
    from .phase1_ingest_runners import run_link_ingest, run_live_ingest, run_upload_ingest
    runner = {"I-LINK": run_link_ingest, "I-LIVE": run_live_ingest, "I-UPLOAD": run_upload_ingest}[chain["chain_id"]]
    chain.setdefault("payload", {})
    chain["payload"] = {**chain["payload"], "chain_id": chain["chain_id"]}
    if chain["chain_id"] == "I-UPLOAD" and not callable(getattr(adapter, "ingest_file", None)):
        return "blocked", {"test_status": "blocked", "execution_status": "blocked", "failure": {"code": "ADAPTER_CAPABILITY_MISSING", "message": "ingest_file adapter is required"}, "evidence": []}
    if chain["chain_id"] in {"I-LINK", "I-LIVE"}:
        chain.setdefault("folder_id", chain.get("folder") or chain.get("payload", {}).get("folder_id", ""))
        source_url = chain.get("source_url") or chain.get("input") or chain.get("payload", {}).get("url")
        chain.setdefault("source_url", source_url)
        chain.setdefault("source_urls", chain.get("source_urls") or ([source_url] if source_url else []))
    if chain["chain_id"] == "I-UPLOAD":
        chain.setdefault("folder_id", chain.get("folder") or chain.get("payload", {}).get("folder_id", ""))
        chain["payload"].setdefault("file_path", chain.get("input"))
    try:
        result = runner(adapter, chain, output_dir)
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}", "test_status": "blocked", "evidence": []}
    status = result.get("test_status", "blocked")
    if status in {"completed", "success"}: status = "passed"
    if status in {"timeout", "timed_out"}: status = "poll_timeout"
    if status in {"submission_unknown", "inconclusive"}: status = "unknown" if status == "submission_unknown" else status
    result["test_status"] = status
    result["execution_status"] = status
    return "passed" if status == "passed" else status, result


def _ready_resource_ids(case_runs):
    ids = []
    for item in case_runs:
        if item.get("status") != "passed":
            continue
        result = item.get("result") or {}
        poll = result.get("poll") or {}
        if result.get("downstream_ready") is not True:
            continue
        if str(poll.get("status", "")).lower() not in {"ready", "passed", "completed"}:
            continue
        candidates = list(result.get("resources") or []) + [result, result.get("submit") or {}, poll]
        for candidate in candidates:
            if isinstance(candidate, dict):
                rid = candidate.get("resource_id") or candidate.get("asset_id") or candidate.get("id")
                if rid and str(rid) not in ids:
                    ids.append(str(rid))
    return ids


def _build_non_live_payload(case_spec, ready_resource_ids):
    """Build a wire payload only from this run's ready assets.

    VFC accepts the plural and singular master asset fields.  IP payloads also
    carry the confirmed material/voice configuration in their nested snapshot;
    do not leave historical IDs from the suite as a silent fallback.
    """
    payload = copy.deepcopy(case_spec.get("payload") or {})
    payload.setdefault("chain_id", case_spec["chain_id"])
    payload.setdefault("scene", case_spec.get("scene"))
    payload["resource_ids"] = list(ready_resource_ids)
    payload["master_asset_ids"] = list(ready_resource_ids)
    payload["master_asset_id"] = ready_resource_ids[0]
    if case_spec.get("scene_type") == "ip_promo" or case_spec.get("chain_id") in {"M-IP", "A-IP"}:
        payload["material_asset_ids"] = list(ready_resource_ids)
        mix = copy.deepcopy(payload.get("mix_config_snapshot") or {})
        mix["material_asset_ids"] = list(ready_resource_ids)
        mix.setdefault("timbre_voice_ids", payload.get("timbre_voice_ids") or [])
        if payload.get("timbre_voice_id"):
            mix.setdefault("timbre_voice_id", payload["timbre_voice_id"])
        mix["delivery_enabled"] = False
        payload["mix_config_snapshot"] = mix
    payload["delivery_enabled"] = False
    payload["auto_delivery_enabled"] = False
    payload.setdefault("auto_delivery_products", [])
    return payload


def _run_generated(adapter, chain):
    try:
        from .chain_runners import build_chain_runner
        result = build_chain_runner(adapter, chain)
        status = result.get("test_status") or result.get("execution_status") or "blocked"
        if status in {"completed", "success"}: status = "passed"
        return status, result
    except Exception as exc:
        return "blocked", {"error": f"{type(exc).__name__}: {exc}"}


def run_phase1(adapter, config: dict[str, Any], run_id: str, output_dir, *, suite=None, runners: dict[str, Callable] | None = None, resource_registry=None, preflight=None):
    from .phase1_report import write_phase1_report
    report_input = {"run_id": run_id, "status": "blocked", "case_runs": []}
    test_runner_mode = (_test_runner_mode(config, suite, runners)
                        or (suite is None and isinstance(config, dict)
                            and isinstance(config.get("phase1"), dict)
                            and config["phase1"].get("safe_write") is True
                            and not preflight))
    readiness = preflight or phase1_readiness(config, suite=suite, strict=not test_runner_mode)
    if readiness["status"] != "ready":
        # A preflight block must still be a complete nine-chain run.  This makes
        # every skipped case auditable without dispatching any remote operation.
        try:
            loaded = load_phase1_suite(_suite_source(config, suite))
            plan = build_phase1_plan(loaded, run_id)
            for case_run in plan["case_runs"]:
                failure_code = "READINESS_BLOCKED"
                if (case_run.get("chain_id") == "I-UPLOAD"
                        and not _has_ingest_capability(adapter)):
                    failure_code = "ADAPTER_CAPABILITY_MISSING"
                evidence = [_evidence("preflight", "blocked")]
                if case_run.get("chain_id") == "I-LIVE":
                    evidence.extend([_evidence("probe", "blocked", reason=readiness.get("reason")), _evidence("dependency", "blocked", reason=readiness.get("reason"))])
                if case_run.get("chain_id") == "I-UPLOAD":
                    capability_status = "unknown" if not _has_ingest_capability(adapter) else "passed"
                    evidence.append(_evidence("capability", capability_status, capability="ingest_file"))
                case_run.update({"status": "blocked", "reason": readiness.get("reason"), "result": {"failure": {"code": failure_code, "reason": readiness.get("reason")}}, "evidence": evidence, "hard_acceptance": False})
            for chain in plan["chains"]:
                evidence = [_evidence("preflight", "blocked")]
                if chain["chain_id"] in CONDITIONAL_CHAINS:
                    evidence.extend([_evidence("probe", "blocked"), _evidence("dependency", "blocked")])
                chain.update({"status": "blocked", "evidence": evidence, "detail": {"reason": readiness.get("reason")}})
            blocked = {**plan, "status": "blocked", "readiness": readiness, "writes": 0}
        except Exception:
            blocked = {"status": "blocked", "run_id": run_id, "readiness": readiness, "case_runs": [], "chains": [], "writes": 0}
        blocked["report_files"] = write_phase1_report(blocked, output_dir, resource_registry=resource_registry, preflight=readiness, run=blocked)
        return blocked
    if not _safe_write_allowed(config, adapter):
        readiness = {"status": "blocked", "reason": "safe_write is not enabled", "writes": 0}
        try:
            loaded = load_phase1_suite(_suite_source(config, suite))
            plan = build_phase1_plan(loaded, run_id)
            for case_run in plan["case_runs"]:
                evidence = [_evidence("safety", "blocked")]
                if case_run.get("chain_id") == "I-LIVE":
                    evidence.extend([_evidence("probe", "blocked", reason=readiness["reason"]), _evidence("dependency", "blocked", reason=readiness["reason"])])
                if case_run.get("chain_id") == "I-UPLOAD":
                    evidence.append(_evidence("capability", "not_probed", capability="ingest_file"))
                case_run.update({"status": "blocked", "reason": readiness["reason"], "result": {"failure": {"code": "SAFE_WRITE_DISABLED", "reason": readiness["reason"]}}, "evidence": evidence, "hard_acceptance": False})

            for chain in plan["chains"]:
                chain.update({"status": "blocked", "evidence": [_evidence("safety", "blocked")], "detail": {"reason": readiness["reason"]}})
            blocked = {**plan, "status": "blocked", "readiness": readiness, "writes": 0}
        except Exception:
            blocked = {"status": "blocked", "run_id": run_id, "readiness": readiness, "case_runs": [], "chains": [], "writes": 0}
        blocked["report_files"] = write_phase1_report(blocked, output_dir, resource_registry=resource_registry, preflight=readiness, run=blocked)
        return blocked
    loaded = load_phase1_suite(_suite_source(config, suite))
    plan = build_phase1_plan(loaded, run_id)
    out = {**plan, "status": "running", "readiness": readiness, "writes": 0}
    phase_timeout = (config.get("phase1") or {}).get("timeout_seconds", config.get("timeout_seconds"))
    phase_started = time.monotonic()
    phase_deadline = phase_started + float(phase_timeout) if phase_timeout is not None else None
    global_timeout = {"timeout_seconds": config.get("timeout_seconds")} if config.get("timeout_seconds") is not None else {}
    successful: set[str] = set()
    by_id = {c["chain_id"]: c for c in loaded["chains"]}
    runners = runners or {}
    for batch, ids in BATCHES:
        for chain_id in ids:
            chain = copy.deepcopy(by_id[chain_id]); cases = chain.get("cases", [])
            chain_statuses = []
            chain_evidence = []
            chain_detail = {}
            for case in cases or [{}]:
                case_spec = copy.deepcopy(chain); case_spec.update(case); case_spec["chain_id"] = chain_id
                case_timeout = _resolve_case_timeout(case, {"timeout_seconds": phase_timeout} if phase_timeout is not None else {}, global_timeout)
                if phase_deadline is not None:
                    remaining = max(0.0, phase_deadline - time.monotonic())
                    case_timeout = min(float(case_timeout), remaining)
                case_spec["timeout_seconds"] = case_timeout
                case_run = next((row for row in out["case_runs"] if row.get("batch_id") == batch and row.get("chain_id") == chain_id and row.get("case_id") == case.get("case_id")), None)
                if case_run:
                    case_spec.update({"run_id": run_id, "case_run_id": case_run["case_run_id"], "resource_registry": resource_registry, "preflight": readiness})
                    def progress_callback(snapshot, *, _case_run=case_run, _case_spec=case_spec):
                        _case_run.setdefault("progress_history", []).append(copy.deepcopy(snapshot))
                        _write_case_snapshot(output_dir, run_id, _case_run.get("case_run_id"), {"run_id": run_id, "case_run_id": _case_run.get("case_run_id"), "chain_id": chain_id, "case_id": _case_run.get("case_id"), "status": "running", "progress": copy.deepcopy(snapshot), "progress_history": copy.deepcopy(_case_run["progress_history"])})
                    case_spec["progress_callback"] = progress_callback
                evidence = [_evidence("probe", "passed", batch=batch)]
                if any(not dependency_satisfied(case, successful) for _ in [0]):
                    status, detail = "blocked", {"reason": "dependency not satisfied", "failure": {"code": "READY_RESOURCE_MISSING", "message": "dependency resource is not ready"}}; evidence.append(_evidence("dependency", "blocked"))
                else:
                    deadline_exhausted = phase_deadline is not None and phase_deadline - time.monotonic() <= 0
                    if deadline_exhausted:
                        status, detail = "inconclusive", {"test_status": "inconclusive", "execution_status": "inconclusive", "failure": {"code": "PHASE_DEADLINE_EXCEEDED", "message": "phase deadline exhausted before case dispatch"}}
                    else:
                        if case_run is not None:
                            _write_case_snapshot(output_dir, run_id, case_run.get("case_run_id"), {"run_id": run_id, "case_run_id": case_run.get("case_run_id"), "chain_id": chain_id, "case_id": case_run.get("case_id"), "status": "running", "step": "dispatch", "timeout_seconds": case_spec.get("timeout_seconds")})
                        if batch != "ingest":
                            ready_ids = _ready_resource_ids(out["case_runs"])
                            if not ready_ids:
                                status, detail = "blocked", {"failure": {"code": "READY_RESOURCE_MISSING", "message": "no ready resource from this batch"}, "test_status": "blocked", "execution_status": "blocked"}
                            else:
                                case_spec["payload"] = _build_non_live_payload(case_spec, ready_ids)
                                if chain_id in runners: status, detail = runners[chain_id](adapter, case_spec, output_dir)
                                else: status, detail = _run_generated(adapter, case_spec)
                        elif chain_id in runners: status, detail = runners[chain_id](adapter, case_spec, output_dir)
                        else: status, detail = _run_ingest(adapter, case_spec, output_dir)
                        if isinstance(detail, dict) and detail.get("test_status") in {"timeout", "timed_out", "poll_timeout"}:
                            status = "inconclusive"
                            detail["test_status"] = "inconclusive"
                            detail["execution_status"] = "inconclusive"
                        if status == "poll_timeout":
                            status = "inconclusive"
                        if status in {"completed", "success"}: status = "passed"
                    evidence.append(_evidence("dispatch", status))
                if chain_id in CONDITIONAL_CHAINS and status != "passed":
                    evidence.append(_evidence("dependency", "blocked"))
                chain_statuses.append(status); chain_evidence.extend(evidence); chain_detail = detail
                if case_run is not None:
                    case_run.update({"status": status, "result": copy.deepcopy(detail), "evidence": copy.deepcopy(evidence), "hard_acceptance": status == "passed"})
                    _write_case_snapshot(output_dir, run_id, case_run.get("case_run_id"), {"run_id": run_id, "case_run_id": case_run.get("case_run_id"), "chain_id": chain_id, "case_id": case_run.get("case_id"), "status": status, "result": copy.deepcopy(detail), "evidence": copy.deepcopy(evidence)})
                out["writes"] += int(status == "passed" and batch != "automation")
            status = _summarize_chain_statuses(chain_statuses)
            if status == "passed": successful.add(chain_id)
            target = next(item for item in out["chains"] if item["chain_id"] == chain_id)
            target.update({"status": status, "evidence": chain_evidence, "detail": chain_detail})
    out["status"] = "completed"
    out["report_files"] = write_phase1_report(out, output_dir, resource_registry=resource_registry, preflight=readiness, run=out)
    return out
