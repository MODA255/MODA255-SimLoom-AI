"""Phase one nine-chain configuration and case-run expansion."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PHASE1_CHAIN_IDS = (
    "I-LINK", "I-LIVE", "I-UPLOAD", "M-ECOM", "M-IP", "M-THEME",
    "A-ECOM", "A-IP", "A-THEME",
)
MANDATORY_REAL_CHAINS = ("I-LINK", "I-UPLOAD", "M-ECOM", "M-IP", "M-THEME", "A-IP")
CONDITIONAL_CHAINS = ("I-LIVE", "A-ECOM", "A-THEME")
TERMINAL_STATUSES = {"completed", "passed", "failed", "cancelled"}
SUBMITTED_STATUSES = TERMINAL_STATUSES | {"submitted", "running"}
REPORT_STATUSES = SUBMITTED_STATUSES | {"poll_timeout", "unknown"}
HISTORICAL_ID_KEYS = {"task_id", "parent_task_id", "child_task_id", "vfc_task_id", "vfc_task_group_id"}
INVALID_PLACEHOLDERS = {"example.invalid", "example.com", "localhost", "placeholder", "changeme", "your-folder-id"}


def _is_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    text = value.strip().lower()
    return (not text or text.startswith("env:") or text.startswith("${")
            or text in INVALID_PLACEHOLDERS or text.startswith("phase1-")
            or text.startswith("your_") or text.startswith("your-"))


def _unresolved_env_refs(value: Any, config: dict[str, Any], path: str = "") -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            refs.extend(_unresolved_env_refs(child, config, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            refs.extend(_unresolved_env_refs(child, config, f"{path}[{index}]"))
    elif isinstance(value, str) and value.strip().startswith("env:"):
        name = value.strip()[4:].strip()
        resolved = _resolve_ref(value, config, kind="env")
        if _is_placeholder(resolved):
            refs.append(f"{path}: {value}")
    return refs


def _resolve_ref(value: Any, config: dict[str, Any], *, kind: str) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    ref = value.strip()
    env_values = config.get("_env_values", {})
    if ref.startswith("env:"):
        return os.getenv(ref[4:].strip()) or env_values.get(ref[4:].strip(), "")
    if ref.startswith("config:"):
        node: Any = config
        for part in ref[7:].split("."):
            if not isinstance(node, dict):
                return ""
            node = node.get(part)
        return node or ""
    return value


def _valid_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value))
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not any(host in parsed.netloc.lower() for host in INVALID_PLACEHOLDERS)
    except Exception:
        return False


def _valid_folder(value: Any) -> bool:
    return isinstance(value, str) and not _is_placeholder(value)


def _resolve_inputs(config: dict[str, Any]) -> dict[str, Any]:
    return {key: _resolve_ref(value, config, kind="input") for key, value in (config.get("inputs") or {}).items()}


def _contains_historical_id(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in HISTORICAL_ID_KEYS for key in value) or any(_contains_historical_id(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_historical_id(v) for v in value)
    return False


def _read_config(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        config = json.loads(path.read_text())
        env_values = {}
        # Suite fixtures commonly live in ``configs/`` while the repository
        # environment file lives at its parent.  Keep production resolution
        # strict (missing values remain unresolved), but support this legacy
        # layout when loading a suite by walking up to the repository root.
        env_path = path.parent / ".env"
        if not env_path.exists():
            parent_env = path.parent.parent / ".env"
            if parent_env.exists():
                env_path = parent_env
        # Phase-one local protected inputs live beside the suite's run config.
        # Prefer that .env over the repository-level credentials file so
        # DAILY_* values resolve without importing secrets or process state.
        local_phase_env = path.parent / ".env"
        if local_phase_env.exists():
            env_path = local_phase_env
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    env_values[key.strip()] = value.strip().strip('"').strip("'")
        config["_env_values"] = env_values
        config["_source_dir"] = str(path.parent)
        return config
    return copy.deepcopy(source)


def validate_phase1_config(config: dict[str, Any]) -> list[str]:
    errors = []
    if set(config.get("enabled_chains", [])) != set(PHASE1_CHAIN_IDS):
        errors.append("enabled_chains must contain exactly all nine phase-one chains")
    if set(config.get("mandatory_real_chains", [])) != set(MANDATORY_REAL_CHAINS):
        errors.append("mandatory_real_chains does not match phase-one scope")
    if set(config.get("conditional_chains", [])) != set(CONDITIONAL_CHAINS):
        errors.append("conditional_chains does not match phase-one scope")
    if config.get("min_p0_cases") != 10:
        errors.append("min_p0_cases must be 10")
    chains = config.get("chains", [])
    if {chain.get("chain_id") for chain in chains} != set(PHASE1_CHAIN_IDS):
        errors.append("chains must define exactly all nine phase-one chains")
    all_cases = []
    for chain in config.get("chains", []):
        for case in chain.get("cases", []):
            if "runner" in case and case.get("runner") != chain.get("runner"):
                errors.append(f"runner mismatch for {case.get('case_id')}")
            case = dict(case)
            case.setdefault("chain_id", chain.get("chain_id"))
            all_cases.append(case)
    case_ids = [case.get("case_id") for case in all_cases]
    duplicate_case_ids = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicate_case_ids:
        errors.append(f"duplicate case_id values: {', '.join(map(str, duplicate_case_ids))}")
    if not 15 <= len(all_cases) <= 25:
        errors.append("planned case count must be between 15 and 25")
    if sum(case.get("priority") == "P0" for case in all_cases) < 10:
        errors.append("planned P0 case count must be at least 10")
    if sum(case.get("priority") == "P0" and case.get("chain_id") == "I-LINK" for case in all_cases) < 2:
        errors.append("I-LINK must have at least two P0 cases")
    folder = config.get("folder") or config.get("folders", {}).get("material")
    if not isinstance(folder, str) or not folder.strip():
        errors.append("folder must be a non-empty valid reference")
    inputs = config.get("inputs", {}) or {}
    for case in all_cases:
        input_ref = case.get("input_ref")
        if input_ref is not None and (not isinstance(input_ref, str) or not input_ref.strip() or input_ref not in inputs):
            errors.append(f"input missing for {case.get('case_id')}: {input_ref}")
    for chain_id in PHASE1_CHAIN_IDS:
        if chain_id != "I-LINK" and not any(case.get("priority") == "P0" and case.get("chain_id") == chain_id for case in all_cases):
            errors.append(f"{chain_id} must have at least one P0 case")
    if _contains_historical_id(config):
        errors.append("historical task ID found in phase-one configuration")
    return errors


def validate_phase1_resources(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    folder = _resolve_ref(config.get("folder") or config.get("folders", {}).get("material"), config, kind="folder")
    if not _valid_folder(folder):
        errors.append("folder must resolve to a non-empty protected folder ID")
    errors.extend(f"unresolved environment reference: {ref}" for ref in _unresolved_env_refs(config, config))
    inputs = _resolve_inputs(config)
    for chain in config.get("chains", []):
        for case in chain.get("cases", []):
            ref = case.get("input_ref")
            value = inputs.get(ref, _resolve_ref(ref, config, kind="input")) if ref else None
            if chain.get("chain_id") in {"I-LINK", "I-LIVE"} and (not value or not _valid_url(value)):
                errors.append(f"input URL is invalid or placeholder for {case.get('case_id')}: {ref}")
            if chain.get("chain_id") == "I-UPLOAD" and (not isinstance(value, str) or _is_placeholder(value) or not Path(value).is_file()):
                errors.append(f"upload file is missing for {case.get('case_id')}: {ref}")
    return errors


def dependency_satisfied(case: dict[str, Any], successful_chains: set[str]) -> bool:
    groups = case.get("depends_on") or []
    if "depends_on_any" in case:
        groups = [{"any_of": case["depends_on_any"]}]
    for group in groups:
        if isinstance(group, str):
            if group not in successful_chains:
                return False
        elif not any(dep in successful_chains for dep in group.get("any_of", [])):
            return False
    return True


def load_phase1_suite(source: str | Path | dict[str, Any], *, validate: bool = True) -> dict[str, Any]:
    config = _read_config(source)
    if validate:
        errors = validate_phase1_config(config)
        if errors:
            raise ValueError("; ".join(errors))
    for chain in config.get("chains", []):
        for case in chain.get("cases", []):
            case.setdefault("chain_id", chain["chain_id"])
            case.setdefault("runner", chain.get("runner"))
            case.setdefault("scene", case.get("scene_type", chain.get("scene_type", chain.get("runner"))))
            case.setdefault("folder", _resolve_ref(config.get("folder", config.get("folders", {}).get("material", "")), config, kind="folder"))
            case.setdefault("folder_id", case.get("folder"))
            if case.get("input_ref"):
                value = _resolve_inputs(config).get(case["input_ref"], _resolve_ref(case["input_ref"], config, kind="input"))
                case["input"] = value
            case.setdefault("payload", {})
            if case["chain_id"] in {"I-LINK", "I-LIVE"}:
                url = case.get("input") or case.get("source_url") or (case.get("payload") or {}).get("url")
                source_type = "live_recording" if case["chain_id"] == "I-LIVE" else "master_tape"
                case["payload"] = {"url": url, "source_type": source_type, **({"folder_id": case.get("folder_id")} if case.get("folder_id") else {})}
            elif case.get("input_ref"):
                case["payload"].setdefault("input", case["input"])
                if case["chain_id"] == "I-UPLOAD":
                    case["payload"].setdefault("file_path", case["input"])
            case["request"] = {"runner": case["runner"], "scene": case["scene"], "payload": case["payload"], "folder": case["folder"], "input": case.get("input"), "input_ref": case.get("input_ref")}
    return config


def derive_case_runs(config: dict[str, Any], run_id: str, batch_id: str) -> list[dict[str, Any]]:
    runs = []
    seen_case_run_ids: set[str] = set()
    for chain in config["chains"]:
        for case in chain.get("cases", []):
            case_id = case["case_id"]
            digest = hashlib.sha256(f"{run_id}:{batch_id}:{case_id}".encode()).hexdigest()[:12]
            case_run_id = f"{batch_id}:{run_id}:{case_id}:{digest}"
            if case_run_id in seen_case_run_ids:
                suffix = 2
                while f"{case_run_id}:{suffix}" in seen_case_run_ids:
                    suffix += 1
                case_run_id = f"{case_run_id}:{suffix}"
            seen_case_run_ids.add(case_run_id)
            item = copy.deepcopy(case)
            item.update({"run_id": run_id, "batch_id": batch_id,
                         "case_run_id": case_run_id,
                         "status": "planned"})
            runs.append(item)
    return runs


def summarize_case_runs(case_runs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "planned": len(case_runs),
        "submitted": sum(item.get("status") in SUBMITTED_STATUSES for item in case_runs),
        "terminal": sum(item.get("status") in TERMINAL_STATUSES for item in case_runs),
        "executed": sum(item.get("status") == "completed" and item.get("hard_acceptance") is True for item in case_runs),
        "blocked": sum(item.get("status") == "blocked" for item in case_runs),
        "inconclusive": sum(item.get("status") == "inconclusive" for item in case_runs),
        "disabled": sum(item.get("status") == "disabled" for item in case_runs),
    }
