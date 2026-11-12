"""Compatibility validator for v1 self-reported check envelopes.

This checks report structure and declarations only. Native observation and
artifact validation belongs to vendor_evidence; v1 is never reported as that.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


REPORT_SCHEMA = "simloom-ai/vendor-validation/v1"


class VendorValidationError(ValueError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


def _finite(value: Any, label: str) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)) and not math.isfinite(float(value)):
        raise VendorValidationError("VENDOR_NONFINITE_VALUE", label)


def _walk(value: Any, label: str = "report") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _walk(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{label}[{index}]")
    else:
        _finite(value, label)


def validate_report(report: Any, *, request_id: str, tool: str, action: str,
                    required_checks: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Validate and return a normalized vendor report.

    A report must identify the exact worker request and operation.  Every named
    check must be present and passed.  Numeric values may be accompanied by a
    unit string; the controller does not silently convert units.
    """
    if not isinstance(report, dict) or report.get("schema_version") != REPORT_SCHEMA:
        raise VendorValidationError("VENDOR_REPORT_SCHEMA_UNSUPPORTED")
    if report.get("request_id") != request_id or report.get("tool") != tool or report.get("action") != action:
        raise VendorValidationError("VENDOR_REPORT_MISMATCH")
    _walk(report)
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise VendorValidationError("VENDOR_CHECKS_MISSING")
    by_name: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("name"), str) or type(check.get("passed")) is not bool:
            raise VendorValidationError("VENDOR_CHECK_INVALID")
        if check["name"] in by_name:
            raise VendorValidationError("VENDOR_CHECK_DUPLICATE", check["name"])
        by_name[check["name"]] = check
        if not check["passed"]:
            raise VendorValidationError("VENDOR_CHECK_FAILED", check["name"])
    missing = [name for name in required_checks if name not in by_name]
    if missing:
        raise VendorValidationError("VENDOR_CHECK_MISSING", ",".join(missing))
    if report.get("passed") is not True:
        raise VendorValidationError("VENDOR_REPORT_FAILED")
    return report


def read_report(root: Path, path: str, *, request_id: str, tool: str, action: str,
                required_checks: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    from .worker_protocol import safe_file

    target = safe_file(root, path)
    if not target.is_file() or target.stat().st_size == 0:
        raise VendorValidationError("VENDOR_REPORT_MISSING", path)
    try:
        report = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VendorValidationError("VENDOR_REPORT_INVALID", path) from exc
    return validate_report(report, request_id=request_id, tool=tool, action=action,
                           required_checks=required_checks)
