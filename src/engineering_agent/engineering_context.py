"""Independent validation for explicit native software context.

Vendor runners may export a small, bounded summary of native objects,
coordinate systems, load cases and model references.  The controller must not
invent that context, but it can check whether the exported references are
internally coherent before an Agent uses them for planning or comparison.

This validator is deliberately structural.  It does not prove that CATIA,
Ansys, Adams or VI-grade created the objects, that a license was valid, or
that a model solved correctly.  Those claims still require vendor evidence and
Windows machine evidence.
"""
from __future__ import annotations

import math
from pathlib import PurePosixPath
from typing import Any


SCHEMA = "simloom-ai/engineering-context-validation/v1"
CONTEXT_SCHEMA = "simloom-ai/engineering-context/v1"
_CATEGORIES = ("native_objects", "coordinate_systems", "load_cases", "model_references")


def _record_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _ids(records: list[dict[str, Any]], category: str, errors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append({"code": "CONTEXT_ID_REQUIRED", "category": category, "index": index})
            continue
        identifier = identifier.strip()
        if identifier in result:
            errors.append({"code": "CONTEXT_DUPLICATE_ID", "category": category, "id": identifier})
            continue
        result[identifier] = record
    return result


def _reference(value: Any, *, allowed: set[str], code: str, field: str,
               category: str, index: int, errors: list[dict[str, Any]]) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        errors.append({"code": code, "category": category, "index": index, "field": field,
                       "reference": value})
    elif value not in allowed:
        errors.append({"code": code, "category": category, "index": index, "field": field,
                       "reference": value})


def _finite_numbers(value: Any) -> bool:
    if not isinstance(value, (list, tuple)):
        return False
    return bool(value) and all(isinstance(item, (int, float)) and not isinstance(item, bool)
                               and math.isfinite(float(item)) for item in value)


def _safe_project_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    if len(value) >= 2 and value[1] == ":":
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def validate_engineering_context(context: Any, *, expected_tool: str | None = None) -> dict[str, Any]:
    """Validate a normalized ``engineering-context/v1`` object.

    The return value is always JSON-compatible and safe to persist beside a
    result.  Errors identify a structural problem without copying arbitrary
    vendor payloads into the audit record.
    """
    if context is None:
        return {"schema_version": SCHEMA, "checked": False, "valid": True,
                "errors": [], "warnings": [], "counts": {category: 0 for category in _CATEGORIES}}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not isinstance(context, dict):
        return {"schema_version": SCHEMA, "checked": True, "valid": False,
                "errors": [{"code": "CONTEXT_NOT_OBJECT"}], "warnings": [],
                "counts": {category: 0 for category in _CATEGORIES}}
    if context.get("schema_version") != CONTEXT_SCHEMA:
        errors.append({"code": "CONTEXT_SCHEMA_UNSUPPORTED"})
    software = context.get("software") if isinstance(context.get("software"), dict) else {}
    if expected_tool and software.get("name") not in {None, expected_tool}:
        errors.append({"code": "CONTEXT_TOOL_MISMATCH", "expected": expected_tool,
                       "actual": software.get("name")})
    records = {category: _record_list(context.get(category)) for category in _CATEGORIES}
    indexed = {category: _ids(records[category], category, errors) for category in _CATEGORIES}
    native_ids = set(indexed["native_objects"])
    coordinate_ids = set(indexed["coordinate_systems"])
    model_ids = set(indexed["model_references"])
    all_object_ids = native_ids | model_ids

    for index, record in enumerate(records["native_objects"]):
        _reference(record.get("parent_id"), allowed=all_object_ids, code="CONTEXT_PARENT_NOT_FOUND",
                   field="parent_id", category="native_objects", index=index, errors=errors)
    for index, record in enumerate(records["coordinate_systems"]):
        if "origin" in record and not _finite_numbers(record.get("origin")):
            errors.append({"code": "CONTEXT_ORIGIN_INVALID", "category": "coordinate_systems", "index": index})
        if "axes" in record and not isinstance(record.get("axes"), (dict, list)):
            errors.append({"code": "CONTEXT_AXES_INVALID", "category": "coordinate_systems", "index": index})
    for index, record in enumerate(records["load_cases"]):
        _reference(record.get("model_reference"), allowed=all_object_ids,
                   code="CONTEXT_MODEL_REFERENCE_NOT_FOUND", field="model_reference",
                   category="load_cases", index=index, errors=errors)
        _reference(record.get("coordinate_system"), allowed=coordinate_ids,
                   code="CONTEXT_COORDINATE_SYSTEM_NOT_FOUND", field="coordinate_system",
                   category="load_cases", index=index, errors=errors)
    for index, record in enumerate(records["model_references"]):
        if "path" in record and not _safe_project_path(record.get("path")):
            errors.append({"code": "CONTEXT_MODEL_PATH_INVALID", "category": "model_references", "index": index})
    if not software and any(records.values()):
        warnings.append({"code": "CONTEXT_SOFTWARE_METADATA_MISSING"})
    if records["load_cases"] and not records["native_objects"] and not records["model_references"]:
        warnings.append({"code": "CONTEXT_LOAD_CASE_MODEL_CATALOG_EMPTY"})
    return {
        "schema_version": SCHEMA,
        "checked": True,
        "valid": not errors,
        "errors": errors[:256],
        "warnings": warnings[:256],
        "counts": {category: len(records[category]) for category in _CATEGORIES},
    }

