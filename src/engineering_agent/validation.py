from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .models import ExecutionPlan, RunReport
from .operations import OperationRegistry
from .geometry_validator import validate_geometry_object
from .result_validator import validate_result_object


def validate_plan(plan: ExecutionPlan, registry: OperationRegistry | None = None) -> dict[str, Any]:
    registry = registry or OperationRegistry()
    errors: list[str] = []
    ids = [step.id for step in plan.steps]
    if len(ids) != len(set(ids)):
        errors.append("DUPLICATE_STEP_ID")
    id_set = set(ids)
    for step in plan.steps:
        for dependency in step.depends_on:
            if dependency not in id_set:
                errors.append(f"MISSING_DEPENDENCY:{step.id}:{dependency}")
        errors.extend(f"{step.id}:{error}" for error in registry.validate_step(step))
    # Kahn-style cycle check keeps the validator independent from execution.
    remaining = {step.id: set(step.depends_on) for step in plan.steps}
    while remaining:
        ready = {key for key, deps in remaining.items() if not deps}
        if not ready:
            errors.append("DEPENDENCY_CYCLE")
            break
        for key in ready:
            remaining.pop(key)
        for deps in remaining.values():
            deps.difference_update(ready)
    return {"valid": not errors, "errors": sorted(set(errors)), "step_count": len(plan.steps)}


def hash_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifacts(files: dict[str, str] | list[str]) -> dict[str, Any]:
    paths = list(files.values()) if isinstance(files, dict) else list(files)
    entries = []
    missing = []
    for item in paths:
        path = Path(item)
        if not path.exists():
            missing.append(str(path))
            continue
        entries.append({"path": str(path), "bytes": path.stat().st_size, "sha256": hash_file(path)})
    return {"valid": not missing, "missing": missing, "files": entries}


def validate_report(report: RunReport) -> dict[str, Any]:
    statuses = [result.status for result in report.results]
    invalid = [status for status in statuses if status not in {"planned", "completed", "failed", "blocked", "unavailable"}]
    return {
        "valid": not invalid,
        "invalid_statuses": invalid,
        "result_count": len(statuses),
        "terminal_success": bool(statuses) and all(status in {"planned", "completed"} for status in statuses),
    }


def validate_asset(asset: dict[str, Any], required_metrics: list[str] | None = None) -> dict[str, Any]:
    if asset.get("kind") == "geometry":
        return validate_geometry_object(asset)
    return validate_result_object(asset, required_metrics)
