"""Deterministic 2D CAD constraint checks and bounded sequential enforcement.

This module deliberately implements the AgentCAD-style direct constraint contract.
It checks actual canonical geometry and enforces one constraint at a time. It is
not a simultaneous parametric solver.
"""
from __future__ import annotations

import math
from typing import Any

from .cad_model import CadDocument

CHECK_TYPES = {
    "coincident", "parallel", "perpendicular", "horizontal", "vertical", "tangent",
    "concentric", "distance", "length", "angle", "symmetric", "equal", "radius",
    "diameter", "fixed",
}
ENFORCE_TYPES = {
    "coincident", "horizontal", "vertical", "concentric", "length", "distance",
    "radius", "diameter", "fixed", "parallel", "perpendicular", "equal",
}


def _point(value: Any) -> dict[str, float]:
    if isinstance(value, (list, tuple)):
        value = {"x": value[0] if len(value) > 0 else 0, "y": value[1] if len(value) > 1 else 0,
                 "z": value[2] if len(value) > 2 else 0}
    value = value or {}
    return {axis: float(value.get(axis, 0)) for axis in ("x", "y", "z")}


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.dist((a["x"], a["y"], a["z"]), (b["x"], b["y"], b["z"]))


def _add(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    return {axis: a[axis] + b[axis] for axis in ("x", "y", "z")}


def _subtract(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    return {axis: a[axis] - b[axis] for axis in ("x", "y", "z")}


def _scale(value: dict[str, float], amount: float) -> dict[str, float]:
    return {axis: value[axis] * amount for axis in ("x", "y", "z")}


def _norm(value: dict[str, float]) -> float:
    return math.sqrt(sum(value[axis] * value[axis] for axis in ("x", "y", "z")))


def _unit(value: dict[str, float]) -> dict[str, float]:
    length = _norm(value)
    if length <= 1e-12:
        raise ValueError("ZERO_LENGTH_GEOMETRY")
    return _scale(value, 1.0 / length)


def _dot(a: dict[str, float], b: dict[str, float]) -> float:
    return sum(a[axis] * b[axis] for axis in ("x", "y", "z"))


def _angle_between(a: dict[str, float], b: dict[str, float]) -> float:
    """Unsigned angle between line directions, treating lines as unoriented."""
    denominator = _norm(a) * _norm(b)
    if denominator <= 1e-12:
        raise ValueError("ZERO_LENGTH_GEOMETRY")
    cosine = max(-1.0, min(1.0, abs(_dot(a, b)) / denominator))
    return math.degrees(math.acos(cosine))


def _entity(document: CadDocument, selector: Any) -> dict[str, Any]:
    if isinstance(selector, dict):
        selector = selector.get("entity_id", selector.get("id", selector.get("handle", selector.get("ref"))))
    if isinstance(selector, str) and selector.startswith("tag:"):
        tag = selector[4:]
        matches = [item for item in document.entities if str(item.get("tag", "")) == tag]
        if len(matches) != 1:
            raise ValueError("TARGET_NOT_UNIQUE:" + selector)
        return matches[0]
    if selector is None or str(selector).strip() == "":
        raise ValueError("CONSTRAINT_ENTITY_REQUIRED")
    try:
        return document.entity(str(selector))
    except KeyError as exc:
        raise ValueError("ENTITY_NOT_FOUND:" + str(selector)) from exc


def _point_ref(document: CadDocument, value: Any, field: str) -> tuple[dict[str, Any], str]:
    if not isinstance(value, str) or "@" not in value:
        raise ValueError("BAD_POINT_REF:" + field)
    selector, name = value.rsplit("@", 1)
    return _entity(document, selector), name


def get_point(entity: dict[str, Any], name: str) -> dict[str, float]:
    kind = str(entity.get("type", "")).upper()
    if kind == "LINE" and name in {"start", "end"}:
        return _point(entity[name])
    if kind == "LINE" and name == "mid":
        return _scale(_add(_point(entity["start"]), _point(entity["end"])), 0.5)
    if kind in {"CIRCLE", "ARC", "ELLIPSE"} and name == "center":
        return _point(entity.get("center"))
    if name in {"insert", "location"} and entity.get(name) is not None:
        return _point(entity[name])
    if kind in {"LWPOLYLINE", "POLYLINE"} and name.startswith("v"):
        try:
            index = int(name[1:])
            return _point((entity.get("vertices") or [])[index])
        except (ValueError, IndexError, TypeError) as exc:
            raise ValueError("BAD_POINT_REF:" + name) from exc
    if kind == "ARC" and name in {"start", "end"}:
        center = _point(entity.get("center"))
        radius = float(entity.get("radius", 0))
        angle = float(entity.get("start_angle" if name == "start" else "end_angle", 0))
        radians = math.radians(angle)
        return {"x": center["x"] + radius * math.cos(radians),
                "y": center["y"] + radius * math.sin(radians), "z": center["z"]}
    raise ValueError("BAD_POINT_REF:" + name)


def set_point(entity: dict[str, Any], name: str, value: dict[str, float]) -> None:
    kind = str(entity.get("type", "")).upper()
    value = _point(value)
    if kind == "LINE" and name in {"start", "end"}:
        entity[name] = value
        return
    if kind in {"CIRCLE", "ARC", "ELLIPSE"} and name == "center":
        current = get_point(entity, name)
        delta = _subtract(value, current)
        entity["center"] = _add(_point(entity.get("center")), delta)
        return
    if name in {"insert", "location"} and entity.get(name) is not None:
        current = get_point(entity, name)
        delta = _subtract(value, current)
        entity[name] = _add(_point(entity.get(name)), delta)
        return
    if kind in {"LWPOLYLINE", "POLYLINE"} and name.startswith("v"):
        index = int(name[1:])
        vertices = list(entity.get("vertices") or [])
        if not -len(vertices) <= index < len(vertices):
            raise ValueError("BAD_POINT_REF:" + name)
        vertices[index] = value
        entity["vertices"] = vertices
        return
    raise ValueError("UNSUPPORTED_ENFORCEMENT:" + kind + "@" + name)


def _line_direction(entity: dict[str, Any]) -> dict[str, float]:
    if str(entity.get("type", "")).upper() != "LINE":
        raise ValueError("UNSUPPORTED_ENTITY:LINE_REQUIRED")
    return _subtract(_point(entity.get("end")), _point(entity.get("start")))


def _size(entity: dict[str, Any]) -> float:
    kind = str(entity.get("type", "")).upper()
    if kind == "LINE":
        return _norm(_line_direction(entity))
    if kind in {"CIRCLE", "ARC"}:
        return float(entity.get("radius", 0))
    raise ValueError("UNSUPPORTED_ENTITY:EQUAL_REQUIRES_LINE_OR_CIRCLE")


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, dict):
        return {key: _round(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(item) for item in value]
    return value


def _measure(document: CadDocument, constraint: dict[str, Any]) -> tuple[Any, Any, float, list[str]]:
    kind = str(constraint.get("type", constraint.get("kind", ""))).lower()
    if kind not in CHECK_TYPES:
        raise ValueError("UNKNOWN_CONSTRAINT:" + kind)
    a_ref = constraint.get("a", constraint.get("first"))
    b_ref = constraint.get("b", constraint.get("second"))
    if kind in {"coincident", "distance"}:
        ea, na = _point_ref(document, a_ref, "a")
        eb, nb = _point_ref(document, b_ref, "b")
        actual = _distance(get_point(ea, na), get_point(eb, nb))
        expected = 0.0 if kind == "coincident" else float(constraint.get("value"))
        return expected, actual, abs(actual - expected), [str(ea.get("id")), str(eb.get("id"))]
    if kind == "fixed":
        ea, na = _point_ref(document, a_ref, "a")
        expected = _point(constraint.get("value"))
        actual = get_point(ea, na)
        return expected, actual, _distance(actual, expected), [str(ea.get("id"))]
    ea = _entity(document, a_ref)
    ids = [str(ea.get("id"))]
    if kind in {"horizontal", "vertical", "length"}:
        direction = _line_direction(ea)
        if kind == "length":
            expected = float(constraint.get("value"))
            actual = _norm(direction)
            return expected, actual, abs(actual - expected), ids
        actual = math.degrees(math.atan2(direction["y"], direction["x"]))
        if kind == "horizontal":
            error = min(abs(actual), abs(abs(actual) - 180.0))
            return 0.0, actual, error, ids
        error = abs(abs(actual) - 90.0)
        return 90.0, actual, error, ids
    if kind in {"parallel", "perpendicular", "angle"}:
        eb = _entity(document, b_ref)
        ids.append(str(eb.get("id")))
        actual = _angle_between(_line_direction(ea), _line_direction(eb))
        expected = 0.0 if kind == "parallel" else 90.0 if kind == "perpendicular" else float(constraint.get("value"))
        return expected, actual, abs(actual - expected), ids
    if kind in {"radius", "diameter"}:
        if str(ea.get("type", "")).upper() not in {"CIRCLE", "ARC"}:
            raise ValueError("UNSUPPORTED_ENTITY:RADIUS_REQUIRES_CIRCLE_OR_ARC")
        radius = float(ea.get("radius", 0))
        expected = float(constraint.get("value"))
        actual = radius * (2.0 if kind == "diameter" else 1.0)
        return expected, actual, abs(actual - expected), ids
    if kind == "concentric":
        eb = _entity(document, b_ref)
        ids.append(str(eb.get("id")))
        actual = _distance(get_point(ea, "center"), get_point(eb, "center"))
        return 0.0, actual, actual, ids
    if kind == "equal":
        eb = _entity(document, b_ref)
        ids.append(str(eb.get("id")))
        actual = _size(eb)
        expected = _size(ea)
        return expected, actual, abs(actual - expected), ids
    if kind == "tangent":
        eb = _entity(document, b_ref)
        ids.append(str(eb.get("id")))
        first, second = ea, eb
        if str(first.get("type", "")).upper() == "LINE":
            first, second = second, first
        if str(first.get("type", "")).upper() not in {"CIRCLE", "ARC"}:
            raise ValueError("UNSUPPORTED_ENTITY:TANGENT_REQUIRES_CIRCLE_OR_ARC")
        center = get_point(first, "center")
        radius = float(first.get("radius", 0))
        if str(second.get("type", "")).upper() == "LINE":
            start = _point(second.get("start")); direction = _unit(_line_direction(second))
            distance = abs(direction["x"] * (center["y"] - start["y"]) - direction["y"] * (center["x"] - start["x"]))
            return radius, distance, abs(distance - radius), ids
        if str(second.get("type", "")).upper() in {"CIRCLE", "ARC"}:
            distance = _distance(center, get_point(second, "center"))
            other = float(second.get("radius", 0))
            error = min(abs(distance - (radius + other)), abs(distance - abs(radius - other)))
            return radius + other, distance, error, ids
        raise ValueError("UNSUPPORTED_ENTITY:TANGENT_TARGET")
    raise ValueError("UNSUPPORTED_CONSTRAINT:" + kind)


def check_constraints(document: CadDocument, constraints: list[dict[str, Any]], *, tolerance: float = 1e-6) -> list[dict[str, Any]]:
    results = []
    for index, constraint in enumerate(constraints):
        item_id = str(constraint.get("id", f"C{index + 1}"))
        kind = str(constraint.get("type", constraint.get("kind", ""))).lower()
        tol = float(constraint.get("tolerance", tolerance))
        try:
            expected, actual, error, entities = _measure(document, constraint)
            results.append({"id": item_id, "type": kind, "satisfied": error <= tol,
                            "expected": _round(expected), "actual": _round(actual),
                            "error": _round(error), "tolerance": tol, "entities": entities})
        except Exception as exc:
            results.append({"id": item_id, "type": kind, "satisfied": False, "status": "error",
                            "error_detail": {"code": str(exc).split(":", 1)[0], "message": str(exc)}})
    return results


def _enforce_one(document: CadDocument, constraint: dict[str, Any]) -> list[str]:
    kind = str(constraint.get("type", constraint.get("kind", ""))).lower()
    if kind not in ENFORCE_TYPES:
        raise ValueError("UNSUPPORTED_ENFORCEMENT:" + kind)
    a_ref, b_ref = constraint.get("a", constraint.get("first")), constraint.get("b", constraint.get("second"))
    if kind == "fixed":
        entity, point_name = _point_ref(document, a_ref, "a")
        set_point(entity, point_name, _point(constraint.get("value")))
        return [str(entity.get("id"))]
    if kind == "coincident":
        first, first_name = _point_ref(document, a_ref, "a")
        second, second_name = _point_ref(document, b_ref, "b")
        set_point(second, second_name, get_point(first, first_name))
        return [str(second.get("id"))]
    if kind == "distance":
        first, first_name = _point_ref(document, a_ref, "a")
        second, second_name = _point_ref(document, b_ref, "b")
        origin, target = get_point(first, first_name), get_point(second, second_name)
        direction = _unit(_subtract(target, origin))
        set_point(second, second_name, _add(origin, _scale(direction, float(constraint.get("value")))))
        return [str(second.get("id"))]
    first = _entity(document, a_ref)
    if kind in {"horizontal", "vertical", "length"}:
        start = _point(first.get("start")); direction = _line_direction(first)
        if kind == "length":
            replacement = _scale(_unit(direction), float(constraint.get("value")))
        elif kind == "horizontal":
            replacement = {"x": math.copysign(_norm(direction), direction["x"] or 1), "y": 0.0, "z": 0.0}
        else:
            replacement = {"x": 0.0, "y": math.copysign(_norm(direction), direction["y"] or 1), "z": 0.0}
        first["end"] = _add(start, replacement)
        return [str(first.get("id"))]
    if kind in {"radius", "diameter"}:
        value = float(constraint.get("value"))
        if value <= 0:
            raise ValueError("OUT_OF_RANGE:radius_or_diameter")
        first["radius"] = value / 2.0 if kind == "diameter" else value
        return [str(first.get("id"))]
    second = _entity(document, b_ref)
    if kind == "concentric":
        set_point(second, "center", get_point(first, "center"))
        return [str(second.get("id"))]
    if kind == "equal":
        value = _size(first)
        if str(second.get("type", "")).upper() == "LINE":
            start = _point(second.get("start")); second["end"] = _add(start, _scale(_unit(_line_direction(second)), value))
        else:
            second["radius"] = value
        return [str(second.get("id"))]
    if kind in {"parallel", "perpendicular"}:
        direction = _unit(_line_direction(first))
        if kind == "perpendicular":
            direction = {"x": -direction["y"], "y": direction["x"], "z": direction["z"]}
        target = _line_direction(second)
        if _dot(direction, target) < 0:
            direction = _scale(direction, -1)
        start = _point(second.get("start")); second["end"] = _add(start, _scale(direction, _norm(target)))
        return [str(second.get("id"))]
    raise ValueError("UNSUPPORTED_ENFORCEMENT:" + kind)


def enforce_constraints(document: CadDocument, constraints: list[dict[str, Any]], *, tolerance: float = 1e-6) -> dict[str, Any]:
    touched = []
    for constraint in constraints:
        touched.extend(_enforce_one(document, constraint))
    checks = check_constraints(document, constraints, tolerance=tolerance)
    conflicts = [item for item in checks if not item.get("satisfied")]
    if conflicts:
        raise ValueError("CONSTRAINT_CONFLICT:" + ",".join(str(item.get("id")) for item in conflicts))
    return {"checks": checks, "touched_ids": list(dict.fromkeys(touched)), "enforced_count": len(constraints)}
