"""Small, dependency-free visual diff for semantic CAD documents."""
from __future__ import annotations

import math
from html import escape
from typing import Any


COLORS = {
    "unchanged": "#94a3b8",
    "added": "#16a34a",
    "removed": "#dc2626",
    "changed": "#f59e0b",
}


def _point(value: Any) -> tuple[float, float]:
    value = value or {}
    return float(value.get("x", 0)), float(value.get("y", 0))


def _entity_points(entity: dict[str, Any]) -> list[tuple[float, float]]:
    kind = str(entity.get("type", "")).upper()
    if kind == "LINE":
        return [_point(entity.get("start")), _point(entity.get("end"))]
    if kind in {"LWPOLYLINE", "POLYLINE", "LEADER"}:
        return [_point(value) for value in entity.get("vertices", [])]
    if kind in {"CIRCLE", "ARC", "ELLIPSE"}:
        center = _point(entity.get("center"))
        radius = abs(float(entity.get("radius", 0))) if kind != "ELLIPSE" else math.hypot(*_point(entity.get("major_axis")))
        return [(center[0] - radius, center[1] - radius), (center[0] + radius, center[1] + radius)]
    if kind in {"POINT", "TEXT", "MTEXT", "DIMENSION"}:
        return [_point(entity.get("point") or entity.get("insert") or entity.get("definition"))]
    if kind == "HATCH":
        return [_point(value) for value in entity.get("boundary_vertices", [])]
    return []


def _bbox(entities: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    points = [point for entity in entities for point in _entity_points(entity)]
    if not points:
        return 0.0, 1.0, 0.0, 1.0
    xs, ys = [value[0] for value in points], [value[1] for value in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad = max(x1 - x0, y1 - y0, 1.0) * 0.08
    return x0 - pad, x1 + pad, y0 - pad, y1 + pad


def _path(point: tuple[float, float], bbox: tuple[float, float, float, float], width: int, height: int) -> tuple[float, float]:
    x0, x1, y0, y1 = bbox
    scale = min((width - 90) / max(x1 - x0, 1e-9), (height - 90) / max(y1 - y0, 1e-9))
    return 45 + (point[0] - x0) * scale, height - 45 - (point[1] - y0) * scale


def _style(kind: str, color: str) -> str:
    return f"stroke:{color};stroke-width:2;fill:none;vector-effect:non-scaling-stroke" + (";stroke-dasharray:7 4" if kind == "removed" else "")


def _draw(entity: dict[str, Any], kind: str, bbox: tuple[float, float, float, float], width: int, height: int) -> str:
    color = COLORS[kind]
    entity_id = escape(str(entity.get("id", "")))
    entity_type = escape(str(entity.get("type", "")))
    attrs = f"data-entity-id='{entity_id}' data-diff-kind='{kind}' data-entity-type='{entity_type}'"
    style = _style(kind, color)
    name = str(entity.get("type", "")).upper()
    if name == "LINE":
        start, end = _path(_point(entity.get("start")), bbox, width, height), _path(_point(entity.get("end")), bbox, width, height)
        return f"<line {attrs} x1='{start[0]:.2f}' y1='{start[1]:.2f}' x2='{end[0]:.2f}' y2='{end[1]:.2f}' style='{style}'/>"
    if name in {"LWPOLYLINE", "POLYLINE", "LEADER"}:
        points = [_path(_point(value), bbox, width, height) for value in entity.get("vertices", [])]
        if len(points) < 2:
            return ""
        value = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        tag = "polygon" if entity.get("closed") else "polyline"
        return f"<{tag} {attrs} points='{value}' style='{style}'/>"
    if name in {"CIRCLE", "ARC"}:
        center = _path(_point(entity.get("center")), bbox, width, height)
        edge = _path(((_point(entity.get("center"))[0] + float(entity.get("radius", 0))), _point(entity.get("center"))[1]), bbox, width, height)
        radius = abs(edge[0] - center[0])
        if name == "CIRCLE":
            return f"<circle {attrs} cx='{center[0]:.2f}' cy='{center[1]:.2f}' r='{radius:.2f}' style='{style}'/>"
        start = math.radians(float(entity.get("start_angle", 0)))
        end = math.radians(float(entity.get("end_angle", 360)))
        first = _path((_point(entity.get("center"))[0] + float(entity.get("radius", 0)) * math.cos(start), _point(entity.get("center"))[1] + float(entity.get("radius", 0)) * math.sin(start)), bbox, width, height)
        last = _path((_point(entity.get("center"))[0] + float(entity.get("radius", 0)) * math.cos(end), _point(entity.get("center"))[1] + float(entity.get("radius", 0)) * math.sin(end)), bbox, width, height)
        large = 1 if abs(float(entity.get("end_angle", 360)) - float(entity.get("start_angle", 0))) > 180 else 0
        return f"<path {attrs} d='M{first[0]:.2f},{first[1]:.2f} A{radius:.2f},{radius:.2f} 0 {large} 0 {last[0]:.2f},{last[1]:.2f}' style='{style}'/>"
    if name == "POINT":
        x, y = _path(_point(entity.get("point")), bbox, width, height)
        return f"<circle {attrs} cx='{x:.2f}' cy='{y:.2f}' r='3' style='fill:{color};stroke:none'/>"
    if name in {"TEXT", "MTEXT", "DIMENSION"}:
        x, y = _path(_point(entity.get("insert") or entity.get("definition")), bbox, width, height)
        label = escape(str(entity.get("text", ""))[:60] or name)
        return f"<text {attrs} x='{x:.2f}' y='{y:.2f}' fill='{color}' font-size='12'>{label}</text>"
    if name == "HATCH":
        points = [_path(_point(value), bbox, width, height) for value in entity.get("boundary_vertices", [])]
        if len(points) >= 3:
            value = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
            return f"<polygon {attrs} points='{value}' style='fill:{color}33;stroke:{color};stroke-width:2'/>"
    return ""


def visual_diff(left: dict[str, Any], right: dict[str, Any], *, width: int = 900, height: int = 560) -> dict[str, Any]:
    """Return semantic entity diff plus an SVG overlay with stable colors."""
    left_entities = {str(entity.get("id")): entity for entity in left.get("entities", [])}
    right_entities = {str(entity.get("id")): entity for entity in right.get("entities", [])}
    statuses: list[dict[str, Any]] = []
    render: list[tuple[dict[str, Any], str]] = []
    for entity_id in sorted(set(left_entities) | set(right_entities)):
        before, after = left_entities.get(entity_id), right_entities.get(entity_id)
        if before is None:
            statuses.append({"entity_id": entity_id, "kind": "added"})
            render.append((after, "added"))
        elif after is None:
            statuses.append({"entity_id": entity_id, "kind": "removed"})
            render.append((before, "removed"))
        elif before == after:
            statuses.append({"entity_id": entity_id, "kind": "unchanged"})
            render.append((after, "unchanged"))
        else:
            statuses.append({"entity_id": entity_id, "kind": "changed"})
            render.extend(((before, "removed"), (after, "changed")))
    bbox = _bbox([entity for entity, _ in render])
    body = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' role='img' data-cad-visual-diff='v1'>",
            "<rect width='100%' height='100%' fill='#f8fafc'/>",
            "<text x='20' y='25' font-family='system-ui' font-size='14'>CAD visual diff</text>"]
    for entity, kind in render:
        if entity:
            body.append(_draw(entity, kind, bbox, width, height))
    legend = [("unchanged", "unchanged"), ("added", "added"), ("removed", "removed"), ("changed", "changed")]
    for index, (label, kind) in enumerate(legend):
        x = 20 + index * 150
        body.append(f"<rect x='{x}' y='{height-28}' width='12' height='12' fill='{COLORS[kind]}'/><text x='{x+18}' y='{height-18}' font-size='11'>{label}</text>")
    body.append("</svg>")
    counts = {kind: sum(item["kind"] == kind for item in statuses) for kind in COLORS}
    return {"schema_version": "simloom-ai/cad-visual-diff/v1", "counts": counts,
            "entities": statuses, "change_count": counts["added"] + counts["removed"] + counts["changed"],
            "svg": "".join(body)}
