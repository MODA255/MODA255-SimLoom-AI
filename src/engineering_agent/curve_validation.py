"""Deterministic, conservative topology checks for 2D curves.

The validator does not replace a vendor geometry kernel.  It samples curved
segments into bounded line pieces and reports crossings/overlaps that are
strong enough to be useful for independent drawing validation.  End points
shared by adjacent polyline segments are intentionally ignored.
"""
from __future__ import annotations

import math
from typing import Any


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, (list, tuple)):
        return (float(value[0]), float(value[1]))
    value = value or {}
    return (float(value.get("x", 0)), float(value.get("y", 0)))


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float], point: tuple[float, float], tolerance: float) -> bool:
    return (min(a[0], b[0]) - tolerance <= point[0] <= max(a[0], b[0]) + tolerance
            and min(a[1], b[1]) - tolerance <= point[1] <= max(a[1], b[1]) + tolerance)


def _intersection_kind(first: tuple[tuple[float, float], tuple[float, float]],
                       second: tuple[tuple[float, float], tuple[float, float]],
                       tolerance: float) -> str | None:
    a, b = first
    c, d = second
    ab = _orientation(a, b, c)
    ad = _orientation(a, b, d)
    ca = _orientation(c, d, a)
    cb = _orientation(c, d, b)
    if abs(ab) <= tolerance and abs(ad) <= tolerance and abs(ca) <= tolerance and abs(cb) <= tolerance:
        axis = 0 if abs(b[0] - a[0]) >= abs(b[1] - a[1]) else 1
        left = sorted((a[axis], b[axis]))
        right = sorted((c[axis], d[axis]))
        return "overlap" if min(left[1], right[1]) - max(left[0], right[0]) > tolerance else "touching"
    if ((ab > tolerance and ad < -tolerance) or (ab < -tolerance and ad > tolerance)) and ((ca > tolerance and cb < -tolerance) or (ca < -tolerance and cb > tolerance)):
        return "crossing"
    if abs(ab) <= tolerance and _on_segment(a, b, c, tolerance):
        return "touching"
    if abs(ad) <= tolerance and _on_segment(a, b, d, tolerance):
        return "touching"
    if abs(ca) <= tolerance and _on_segment(c, d, a, tolerance):
        return "touching"
    if abs(cb) <= tolerance and _on_segment(c, d, b, tolerance):
        return "touching"
    return None


def _bulge_points(start: tuple[float, float], end: tuple[float, float], bulge: float,
                  tolerance: float) -> list[tuple[float, float]]:
    chord = _distance(start, end)
    if chord <= tolerance or not math.isfinite(bulge) or abs(bulge) <= tolerance:
        return [start, end]
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    normal = (-(end[1] - start[1]) / chord, (end[0] - start[0]) / chord)
    offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    center = (midpoint[0] + normal[0] * offset, midpoint[1] + normal[1] * offset)
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    sweep = 4.0 * math.atan(bulge)
    count = max(4, min(96, int(math.ceil(abs(math.degrees(sweep)) / 10.0))))
    return [
        (center[0] + radius * math.cos(start_angle + sweep * index / count),
         center[1] + radius * math.sin(start_angle + sweep * index / count))
        for index in range(count + 1)
    ]


def _polyline_segments(entity: dict[str, Any], tolerance: float) -> tuple[list[dict[str, Any]], bool]:
    vertices = [_point(value) for value in (entity.get("vertices") or [])]
    if len(vertices) < 2:
        return [], bool(entity.get("closed"))
    bulges = list(entity.get("bulges") or [])
    closed = bool(entity.get("closed"))
    count = len(vertices) if closed else len(vertices) - 1
    segments: list[dict[str, Any]] = []
    for source_index in range(count):
        start = vertices[source_index]
        end = vertices[(source_index + 1) % len(vertices)]
        bulge = float(bulges[source_index]) if source_index < len(bulges) else 0.0
        points = _bulge_points(start, end, bulge, tolerance)
        for piece_index, (piece_start, piece_end) in enumerate(zip(points, points[1:])):
            segments.append({"start": piece_start, "end": piece_end, "source_index": source_index,
                             "piece_index": piece_index, "piece_count": len(points) - 1,
                             "source_start": start, "source_end": end})
    return segments, closed


def _spline_segments(entity: dict[str, Any], tolerance: float) -> tuple[list[dict[str, Any]], bool] | None:
    fit_points = entity.get("fit_points") or []
    control_points = entity.get("control_points") or []
    degree = int(entity.get("degree", 3) or 3)
    if len(fit_points) < 2 and len(control_points) < 2:
        return None
    try:
        from ezdxf.math import BSpline
        values = [_point(value) + (float(value.get("z", 0)) if isinstance(value, dict) else 0.0)
                  for value in (control_points or fit_points)]
        if control_points:
            curve = BSpline(values, order=degree + 1, knots=entity.get("knots") or None,
                            weights=entity.get("weights") or None)
        else:
            curve = BSpline.from_fit_points(values, degree=max(1, min(degree, len(values) - 1)))
        params = [float(value) for value in curve.approximation_params()]
        if len(params) < 2 or any(not math.isfinite(value) for value in params):
            return None
        dense: list[float] = []
        subdivisions = 32
        for first, second in zip(params, params[1:]):
            if second <= first:
                continue
            dense.extend(first + (second - first) * index / subdivisions for index in range(subdivisions))
        dense.append(params[-1])
        points = list(curve.points(dense))
        segments = []
        for index, (first, second) in enumerate(zip(points, points[1:])):
            segments.append({"start": (float(first.x), float(first.y)),
                             "end": (float(second.x), float(second.y)),
                             "source_index": index, "piece_index": 0, "piece_count": 1,
                             "source_start": (float(first.x), float(first.y)),
                             "source_end": (float(second.x), float(second.y))})
        return segments, False
    except (AttributeError, TypeError, ValueError, ArithmeticError):
        return None


def _adjacent(first: int, second: int, source_count: int, closed: bool) -> bool:
    if abs(first - second) <= 1:
        return True
    return closed and {first, second} == {0, source_count - 1}


def find_self_intersections(entity: dict[str, Any], tolerance: float = 1e-7) -> list[dict[str, Any]] | None:
    """Return conservative self-crossing findings, or ``None`` if unsupported."""
    kind = str(entity.get("type", "")).upper()
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        segments, closed = _polyline_segments(entity, tolerance)
        source_count = len(entity.get("vertices") or []) if closed else max(0, len(entity.get("vertices") or []) - 1)
    elif kind == "SPLINE":
        result = _spline_segments(entity, tolerance)
        if result is None:
            return None
        segments, closed = result
        source_count = len(segments)
    else:
        return []
    findings: list[dict[str, Any]] = []
    for left_index, left in enumerate(segments):
        for right in segments[left_index + 1:]:
            left_source, right_source = left["source_index"], right["source_index"]
            if left_source == right_source or _adjacent(left_source, right_source, source_count, closed):
                continue
            kind = _intersection_kind((left["start"], left["end"]), (right["start"], right["end"]), tolerance)
            if kind not in {"crossing", "overlap"}:
                continue
            findings.append({"kind": kind, "segments": [left_source, right_source],
                             "sample_segments": [left_index, left_index + 1 + segments[left_index + 1:].index(right)]})
    # A dense spline can report the same crossing in neighboring sample pairs.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[int, int]]] = set()
    for item in findings:
        key = (item["kind"], tuple(sorted(item["segments"])))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique

