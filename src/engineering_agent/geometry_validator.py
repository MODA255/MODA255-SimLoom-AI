from __future__ import annotations

import math
from typing import Any


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def validate_geometry_object(asset: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if asset.get("kind") != "geometry":
        errors.append("NOT_GEOMETRY_ASSET")
    geometry = asset.get("geometry") or {}
    fmt = geometry.get("format")
    if fmt == "OBJ":
        vertices = geometry.get("vertices") or []
        faces = geometry.get("faces") or []
        if not vertices:
            errors.append("OBJ_HAS_NO_VERTICES")
        if not faces:
            warnings.append("OBJ_HAS_NO_FACES")
        for index, point in enumerate(vertices):
            if not all(_finite(point.get(axis)) for axis in ("x", "y", "z")):
                errors.append(f"OBJ_NON_FINITE_VERTEX:{index}")
        for index, face in enumerate(faces):
            if len(face) < 3:
                errors.append(f"OBJ_FACE_TOO_SHORT:{index}")
            if any(not isinstance(vertex, int) or vertex < 0 or vertex >= len(vertices) for vertex in face):
                errors.append(f"OBJ_FACE_INDEX_OUT_OF_RANGE:{index}")
            if len(set(face)) != len(face):
                errors.append(f"OBJ_FACE_REPEATED_VERTEX:{index}")
    elif fmt == "STEP":
        if geometry.get("entity_count", 0) <= 0:
            errors.append("STEP_HAS_NO_ENTITIES")
        if geometry.get("cartesian_point_count", 0) == 0:
            warnings.append("STEP_HAS_NO_CARTESIAN_POINTS")
        bbox = geometry.get("bbox") or {}
        for axis in ("x", "y", "z"):
            if axis in bbox and bbox[axis].get("min", 0) > bbox[axis].get("max", 0):
                errors.append(f"INVALID_BBOX:{axis}")
    elif fmt == "DXF":
        entities = geometry.get("entities") or []
        if not geometry.get("entity_types") or not entities:
            errors.append("DXF_HAS_NO_ENTITIES")
        bbox = geometry.get("bbox") or {}
        for axis in ("x", "y", "z"):
            if axis in bbox and bbox[axis].get("min", 0) > bbox[axis].get("max", 0):
                errors.append(f"INVALID_BBOX:{axis}")
        for index, entity in enumerate(entities):
            kind = str(entity.get("type", "UNKNOWN")).upper()
            if kind == "LINE":
                start, end = entity.get("start", {}), entity.get("end", {})
                if all(_finite(start.get(axis, 0)) and _finite(end.get(axis, 0)) for axis in ("x", "y", "z")):
                    if all(float(start.get(axis, 0)) == float(end.get(axis, 0)) for axis in ("x", "y", "z")):
                        errors.append(f"ZERO_LENGTH_LINE:{index}")
                else:
                    errors.append(f"NON_FINITE_LINE:{index}")
            elif kind in {"CIRCLE", "ARC"}:
                radius = float(entity.get("radius", 0)) if _finite(entity.get("radius", 0)) else 0
                if radius <= 0:
                    errors.append(f"INVALID_RADIUS:{index}")
            elif kind in {"LWPOLYLINE", "POLYLINE"}:
                vertices = entity.get("vertices") or []
                if len(vertices) < 2:
                    errors.append(f"POLYLINE_TOO_SHORT:{index}")
                if entity.get("closed") and len(vertices) < 3:
                    errors.append(f"CLOSED_POLYLINE_TOO_SHORT:{index}")
            elif kind == "INSERT" and not str(entity.get("block_name", "")):
                errors.append(f"INSERT_WITHOUT_BLOCK:{index}")
            elif kind == "HATCH":
                boundary = entity.get("boundary_vertices") or []
                if len(boundary) < 3:
                    errors.append(f"HATCH_BOUNDARY_TOO_SHORT:{index}")
            elif kind == "TEXT" and not str(entity.get("text", "")):
                warnings.append(f"EMPTY_TEXT:{index}")
            elif kind == "DIMENSION" and entity.get("measurement") is None:
                warnings.append(f"DIMENSION_WITHOUT_MEASUREMENT:{index}")
    elif fmt == "SVG":
        if sum(int(geometry.get(key, 0)) for key in ("line_count", "circle_count", "path_count")) == 0:
            warnings.append("SVG_HAS_NO_DRAWING_ELEMENTS")
    else:
        warnings.append("UNKNOWN_GEOMETRY_FORMAT")
    return {"valid": not errors, "errors": errors, "warnings": warnings}
