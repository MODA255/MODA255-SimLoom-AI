from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pairs(text: str) -> list[tuple[str, str]]:
    lines = [line.rstrip("\r") for line in text.splitlines()]
    return [(lines[index].strip(), lines[index + 1].strip()) for index in range(0, len(lines) - 1, 2)]


def _value(groups: dict[str, list[str]], code: str, default: Any = None) -> Any:
    values = groups.get(code, [])
    return values[0] if values else default


def _point(groups: dict[str, list[str]], x_code: str = "10", y_code: str = "20", z_code: str = "30") -> dict[str, float]:
    return {"x": _number(_value(groups, x_code)), "y": _number(_value(groups, y_code)), "z": _number(_value(groups, z_code))}


def _entity(entity_type: str, groups: dict[str, list[str]]) -> dict[str, Any]:
    layer = str(_value(groups, "8", "0"))
    if entity_type == "LINE":
        return {"type": "LINE", "layer": layer, "start": _point(groups), "end": _point(groups, "11", "21", "31")}
    if entity_type == "CIRCLE":
        return {"type": "CIRCLE", "layer": layer, "center": _point(groups), "radius": _number(_value(groups, "40"))}
    if entity_type == "ARC":
        return {"type": "ARC", "layer": layer, "center": _point(groups), "radius": _number(_value(groups, "40")), "start_angle": _number(_value(groups, "50")), "end_angle": _number(_value(groups, "51"))}
    if entity_type == "POINT":
        return {"type": "POINT", "layer": layer, "point": _point(groups)}
    if entity_type == "TEXT":
        return {"type": "TEXT", "layer": layer, "insert": _point(groups), "text": str(_value(groups, "1", "")), "height": _number(_value(groups, "40", 1.0), 1.0), "rotation": _number(_value(groups, "50"))}
    if entity_type == "DIMENSION":
        return {"type": "DIMENSION", "layer": layer, "definition": _point(groups), "text": str(_value(groups, "1", "")), "measurement": _number(_value(groups, "42"), 0.0)}
    if entity_type in {"LWPOLYLINE", "POLYLINE"}:
        xs, ys = groups.get("10", []), groups.get("20", [])
        vertices = [{"x": _number(x), "y": _number(ys[index] if index < len(ys) else 0.0), "z": 0.0} for index, x in enumerate(xs)]
        flags = int(_number(_value(groups, "70", 0)))
        return {"type": entity_type, "layer": layer, "vertices": vertices, "closed": bool(flags & 1)}
    return {"type": entity_type, "layer": layer, "data": {code: values[0] if len(values) == 1 else values for code, values in groups.items()}}


def parse_dxf(text: str) -> dict[str, Any]:
    """Parse the common ASCII DXF entity subset into a semantic document.

    The parser deliberately preserves unknown entities and raw group values so
    unsupported vendor extensions remain inspectable instead of being dropped.
    """
    entities: list[dict[str, Any]] = []
    current_type: str | None = None
    groups: dict[str, list[str]] = {}
    in_entities = False

    def flush() -> None:
        nonlocal current_type, groups
        if current_type and in_entities:
            entities.append(_entity(current_type, groups))
        current_type, groups = None, {}

    for code, value in _pairs(text):
        if code == "0":
            if value == "SECTION":
                flush()
                current_type = "SECTION"
                groups = {}
                continue
            if value == "ENDSEC":
                flush()
                in_entities = False
                continue
            if value == "EOF":
                flush()
                break
            if value == "ENTITIES":
                in_entities = True
                current_type = None
                groups = {}
                continue
            if in_entities:
                flush()
                current_type = value.upper()
                groups = {}
                continue
        if code == "2" and current_type == "SECTION" and value.upper() == "ENTITIES":
            in_entities = True
            current_type = None
            groups = {}
            continue
        if current_type is not None and current_type != "SECTION":
            groups.setdefault(code, []).append(value)
        elif current_type == "SECTION" and code == "2" and value.upper() != "ENTITIES":
            current_type = None

    flush()
    layers: dict[str, int] = {}
    for item in entities:
        layer = str(item.get("layer", "0"))
        layers[layer] = layers.get(layer, 0) + 1
    return {"format": "DXF", "entities": entities, "entity_count": len(entities), "entity_types": _counts(entities), "layers": layers, "bbox": bbox(entities)}


def read_dxf(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    return parse_dxf(path.read_text(encoding="utf-8", errors="replace"))


def _counts(entities: Iterable[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in entities:
        name = str(item.get("type", "UNKNOWN"))
        result[name] = result.get(name, 0) + 1
    return result


def _points(entity: dict[str, Any]) -> list[dict[str, float]]:
    kind = entity.get("type")
    if kind == "LINE":
        return [entity.get("start", {}), entity.get("end", {})]
    if kind in {"CIRCLE", "ARC"}:
        center = entity.get("center", {})
        radius = abs(_number(entity.get("radius")))
        return [{"x": _number(center.get("x")) - radius, "y": _number(center.get("y")) - radius, "z": _number(center.get("z"))}, {"x": _number(center.get("x")) + radius, "y": _number(center.get("y")) + radius, "z": _number(center.get("z"))}]
    if kind == "POINT":
        return [entity.get("point", {})]
    if kind == "TEXT":
        return [entity.get("insert", {})]
    if kind == "DIMENSION":
        return [entity.get("definition", {})]
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        return list(entity.get("vertices", []))
    return []


def bbox(entities: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    points = [point for entity in entities for point in _points(entity)]
    if not points:
        return {}
    return {axis: {"min": min(_number(point.get(axis)) for point in points), "max": max(_number(point.get(axis)) for point in points)} for axis in ("x", "y", "z")}


def transform_document(document: dict[str, Any], *, translate: Iterable[float] = (0.0, 0.0, 0.0), scale: float = 1.0, rotate_deg: float = 0.0) -> dict[str, Any]:
    delta = list(translate)
    delta += [0.0] * (3 - len(delta))
    radians = math.radians(float(rotate_deg))
    cos_value, sin_value = math.cos(radians), math.sin(radians)

    def transform_point(point: dict[str, Any]) -> dict[str, float]:
        x = _number(point.get("x")) * float(scale)
        y = _number(point.get("y")) * float(scale)
        return {"x": x * cos_value - y * sin_value + _number(delta[0]), "y": x * sin_value + y * cos_value + _number(delta[1]), "z": _number(point.get("z")) * float(scale) + _number(delta[2])}

    transformed: list[dict[str, Any]] = []
    for original in document.get("entities", []):
        item = {key: value for key, value in original.items()}
        kind = item.get("type")
        if kind == "LINE":
            item["start"], item["end"] = transform_point(item.get("start", {})), transform_point(item.get("end", {}))
        elif kind in {"CIRCLE", "ARC"}:
            item["center"] = transform_point(item.get("center", {}))
            item["radius"] = abs(_number(item.get("radius")) * float(scale))
        elif kind == "POINT":
            item["point"] = transform_point(item.get("point", {}))
        elif kind == "TEXT":
            item["insert"] = transform_point(item.get("insert", {}))
        elif kind == "DIMENSION":
            item["definition"] = transform_point(item.get("definition", {}))
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            item["vertices"] = [transform_point(point) for point in item.get("vertices", [])]
        transformed.append(item)
    return {"format": "DXF", "entities": transformed, "entity_count": len(transformed), "entity_types": _counts(transformed), "layers": {layer: sum(1 for item in transformed if item.get("layer", "0") == layer) for layer in sorted({str(item.get("layer", "0")) for item in transformed})}, "bbox": bbox(transformed)}


def diff_documents(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_entities, right_entities = left.get("entities", []), right.get("entities", [])
    changes: list[dict[str, Any]] = []
    if left.get("layers") != right.get("layers"):
        changes.append({"path": "layers", "kind": "changed", "before": left.get("layers", {}), "after": right.get("layers", {})})
    if left.get("bbox") != right.get("bbox"):
        changes.append({"path": "bbox", "kind": "changed", "before": left.get("bbox", {}), "after": right.get("bbox", {})})
    count = max(len(left_entities), len(right_entities))
    for index in range(count):
        path = f"entities[{index}]"
        if index >= len(left_entities):
            changes.append({"path": path, "kind": "added", "after": right_entities[index]})
        elif index >= len(right_entities):
            changes.append({"path": path, "kind": "removed", "before": left_entities[index]})
        elif left_entities[index] != right_entities[index]:
            changes.append({"path": path, "kind": "changed", "before": left_entities[index], "after": right_entities[index]})
    return {"change_count": len(changes), "changes": changes}
