from __future__ import annotations

import math
from typing import Any


def validate_result_object(asset: dict[str, Any], required_metrics: list[str] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if asset.get("kind") not in {"result", "table"}:
        errors.append("NOT_RESULT_OR_TABLE_ASSET")
    metrics = asset.get("metrics") or {}
    for name in required_metrics or []:
        if name not in metrics:
            errors.append(f"MISSING_METRIC:{name}")
    for name, value in metrics.items():
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            errors.append(f"NON_FINITE_METRIC:{name}")
    tables = asset.get("tables") or {}
    numeric = tables.get("numeric") or {}
    for name, summary in numeric.items():
        for field in ("min", "max", "mean"):
            if not math.isfinite(float(summary[field])):
                errors.append(f"NON_FINITE_COLUMN:{name}")
    if asset.get("kind") == "table" and not tables.get("columns"):
        warnings.append("TABLE_HAS_NO_COLUMNS")
    return {"valid": not errors, "errors": errors, "warnings": warnings}
