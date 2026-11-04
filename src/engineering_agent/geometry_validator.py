from __future__ import annotations

from typing import Any


def validate_geometry_object(asset: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if asset.get("kind") != "geometry":
        errors.append("NOT_GEOMETRY_ASSET")
    geometry = asset.get("geometry") or {}
    fmt = geometry.get("format")
    if fmt == "STEP":
        if geometry.get("entity_count", 0) <= 0:
            errors.append("STEP_HAS_NO_ENTITIES")
        if geometry.get("cartesian_point_count", 0) == 0:
            warnings.append("STEP_HAS_NO_CARTESIAN_POINTS")
        bbox = geometry.get("bbox") or {}
        for axis in ("x", "y", "z"):
            if axis in bbox and bbox[axis].get("min", 0) > bbox[axis].get("max", 0):
                errors.append(f"INVALID_BBOX:{axis}")
    elif fmt == "DXF":
        if not geometry.get("entity_types"):
            errors.append("DXF_HAS_NO_ENTITIES")
    elif fmt == "SVG":
        if sum(int(geometry.get(key, 0)) for key in ("line_count", "circle_count", "path_count")) == 0:
            warnings.append("SVG_HAS_NO_DRAWING_ELEMENTS")
    else:
        warnings.append("UNKNOWN_GEOMETRY_FORMAT")
    return {"valid": not errors, "errors": errors, "warnings": warnings}
