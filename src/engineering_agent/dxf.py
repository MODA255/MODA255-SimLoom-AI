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
    return [(lines[index].strip(), lines[index + 1]) for index in range(0, len(lines) - 1, 2)]


def _value(groups: dict[str, list[str]], code: str, default: Any = None) -> Any:
    values = groups.get(code, [])
    return values[0] if values else default


def _point(groups: dict[str, list[str]], x_code: str = "10", y_code: str = "20", z_code: str = "30") -> dict[str, float]:
    return {"x": _number(_value(groups, x_code)), "y": _number(_value(groups, y_code)), "z": _number(_value(groups, z_code))}


def _entity(entity_type: str, groups: dict[str, list[str]]) -> dict[str, Any]:
    layer = str(_value(groups, "8", "0"))
    common: dict[str, Any] = {"type": entity_type, "layer": layer}
    if _value(groups, "5") is not None:
        common["handle"] = str(_value(groups, "5"))
    if _value(groups, "330") is not None:
        common["owner"] = str(_value(groups, "330"))
    if _value(groups, "6") is not None:
        common["linetype"] = str(_value(groups, "6"))
    if _value(groups, "62") is not None:
        common["color_index"] = int(_number(_value(groups, "62")))
    if _value(groups, "420") is not None:
        common["true_color"] = int(_number(_value(groups, "420")))
    if _value(groups, "370") is not None:
        common["lineweight"] = int(_number(_value(groups, "370")))
    if _value(groups, "39") is not None:
        common["thickness"] = _number(_value(groups, "39"))
    if entity_type == "LINE":
        return common | {"start": _point(groups), "end": _point(groups, "11", "21", "31")}
    if entity_type == "CIRCLE":
        return common | {"center": _point(groups), "radius": _number(_value(groups, "40"))}
    if entity_type == "ARC":
        return common | {"center": _point(groups), "radius": _number(_value(groups, "40")), "start_angle": _number(_value(groups, "50")), "end_angle": _number(_value(groups, "51"))}
    if entity_type == "POINT":
        return common | {"point": _point(groups)}
    if entity_type == "TEXT":
        return common | {"insert": _point(groups), "text": str(_value(groups, "1", "")), "height": _number(_value(groups, "40", 1.0), 1.0), "rotation": _number(_value(groups, "50"))}
    if entity_type == "MTEXT":
        return common | {"insert": _point(groups), "text": str(_value(groups, "1", "")), "height": _number(_value(groups, "40", 1.0), 1.0), "width": _number(_value(groups, "41", 0.0)), "rotation": _number(_value(groups, "50"))}
    if entity_type == "LEADER":
        xs, ys, zs = groups.get("10", []), groups.get("20", []), groups.get("30", [])
        vertices = [{"x": _number(x), "y": _number(ys[index] if index < len(ys) else 0), "z": _number(zs[index] if index < len(zs) else 0)} for index, x in enumerate(xs)]
        return common | {"vertices": vertices, "text": str(_value(groups, "3", "")), "arrowhead": bool(int(_number(_value(groups, "71", 1))))}
    if entity_type == "ELLIPSE":
        return common | {"center": _point(groups), "major_axis": _point(groups, "11", "21", "31"), "ratio": _number(_value(groups, "40", 1.0), 1.0), "start_param": _number(_value(groups, "41", 0.0)), "end_param": _number(_value(groups, "42", 6.283185307179586), 6.283185307179586)}
    if entity_type == "DIMENSION":
        dimension_codes = {0: "linear", 1: "linear", 2: "angular", 3: "diameter", 4: "radial", 6: "ordinate", 7: "ordinate"}
        dimension_type = dimension_codes.get(int(_number(_value(groups, "70", 0))), "linear")
        return common | {"definition": _point(groups), "text": str(_value(groups, "1", "")), "measurement": _number(_value(groups, "42"), 0.0), "block_name": _value(groups, "2"), "dimension_type": dimension_type, "axis": "y" if int(_number(_value(groups, "70", 0))) == 7 else "x"}
    if entity_type in {"LWPOLYLINE", "POLYLINE"}:
        xs, ys = groups.get("10", []), groups.get("20", [])
        vertices = [{"x": _number(x), "y": _number(ys[index] if index < len(ys) else 0.0), "z": 0.0} for index, x in enumerate(xs)]
        flags = int(_number(_value(groups, "70", 0)))
        bulges = [_number(value) for value in groups.get("42", [])]
        entity = common | {"vertices": vertices, "closed": bool(flags & 1)}
        if bulges:
            entity["bulges"] = bulges
        return entity
    if entity_type == "INSERT":
        return common | {"block_name": str(_value(groups, "2", "")), "insert": _point(groups), "scale": {"x": _number(_value(groups, "41", 1.0), 1.0), "y": _number(_value(groups, "42", 1.0), 1.0), "z": _number(_value(groups, "43", 1.0), 1.0)}, "rotation": _number(_value(groups, "50"))}
    if entity_type == "HATCH":
        xs, ys = groups.get("10", []), groups.get("20", [])
        boundary = [{"x": _number(x), "y": _number(ys[index] if index < len(ys) else 0.0), "z": 0.0} for index, x in enumerate(xs)]
        return common | {
            "pattern": str(_value(groups, "2", "SOLID")),
            "solid": bool(int(_number(_value(groups, "70", 0)))),
            "boundary_path_count": int(_number(_value(groups, "91", 0))),
            "boundary_vertices": boundary,
            "boundary_refs": [str(value) for value in groups.get("330", [])],
            "hatch_style": int(_number(_value(groups, "75", 0))),
            "pattern_type": int(_number(_value(groups, "76", 0))),
            "pattern_angle": _number(_value(groups, "52", 0)),
            "pattern_scale": _number(_value(groups, "41", 1), 1),
            "double": bool(int(_number(_value(groups, "77", 0)))),
        }
    # Keep every group for unsupported/proxy entities.  The writer can emit
    # these groups again, so a read/edit/write cycle does not silently delete
    # vendor extensions that this semantic layer does not understand yet.
    return common | {
        "data": {code: values[0] if len(values) == 1 else values for code, values in groups.items()},
        "raw_groups": {str(code): list(values) for code, values in groups.items()},
        "raw_tags": list(getattr(groups, "tags", [])),
    }


class _GroupValues(dict):
    """Retain group order for records whose vendor-specific schema is unknown."""

    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, str]] = []


def _records(pairs: list[tuple[str, str]]) -> list[tuple[str, dict[str, list[str]]]]:
    records: list[tuple[str, dict[str, list[str]]]] = []
    current_type: str | None = None
    groups = _GroupValues()
    for code, value in pairs:
        if code == "0":
            if current_type is not None:
                records.append((current_type, groups))
            current_type, groups = value.upper(), _GroupValues()
        elif current_type is not None:
            groups.setdefault(code, []).append(value)
            groups.tags.append((code, value))
    if current_type is not None:
        records.append((current_type, groups))
    return records


def _sections(pairs: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    sections: dict[str, list[tuple[str, str]]] = {}
    index = 0
    while index < len(pairs):
        if pairs[index] == ("0", "SECTION") and index + 1 < len(pairs) and pairs[index + 1][0] == "2":
            name = pairs[index + 1][1].upper()
            index += 2
            start = index
            while index < len(pairs) and pairs[index] != ("0", "ENDSEC"):
                index += 1
            sections[name] = pairs[start:index]
        index += 1
    return sections


def _header_coordinate_systems(pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    values: dict[str, dict[str, float]] = {}
    variable: str | None = None
    for code, value in pairs:
        if code == "9":
            variable = value
        elif variable in {"$UCSORG", "$UCSXDIR", "$UCSYDIR"} and code in {"10", "20", "30"}:
            axis = {"10": "x", "20": "y", "30": "z"}[code]
            values.setdefault(variable, {})[axis] = _number(value)
    systems = [{"name": "WCS", "origin": {"x": 0.0, "y": 0.0, "z": 0.0}, "x_axis": {"x": 1.0, "y": 0.0, "z": 0.0}, "y_axis": {"x": 0.0, "y": 1.0, "z": 0.0}}]
    if values:
        systems.append({"name": "UCS", "origin": values.get("$UCSORG", {"x": 0.0, "y": 0.0, "z": 0.0}), "x_axis": values.get("$UCSXDIR", {"x": 1.0, "y": 0.0, "z": 0.0}), "y_axis": values.get("$UCSYDIR", {"x": 0.0, "y": 1.0, "z": 0.0})})
    return systems


def _blocks(pairs: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    blocks: dict[str, dict[str, Any]] = {}
    active: dict[str, Any] | None = None
    for entity_type, groups in _records(pairs):
        if entity_type == "BLOCK":
            if active:
                blocks[str(active["name"])] = active
            active = {"name": str(_value(groups, "2", "")), "handle": _value(groups, "5"), "base_point": _point(groups), "entities": []}
        elif entity_type == "ENDBLK":
            if active:
                blocks[str(active["name"])] = active
                active = None
        elif active is not None:
            active["entities"].append(_entity(entity_type, groups))
    if active:
        blocks[str(active["name"])] = active
    for block in blocks.values():
        block["entity_count"] = len(block["entities"])
    return blocks


def parse_dxf(text: str) -> dict[str, Any]:
    """Parse the common ASCII DXF entity subset into a semantic document.

    The parser deliberately preserves unknown entities and raw group values so
    unsupported vendor extensions remain inspectable instead of being dropped.
    """
    sections = _sections(_pairs(text))
    entities = [_entity(entity_type, groups) for entity_type, groups in _records(sections.get("ENTITIES", [])) if entity_type not in {"ENDSEC", "EOF"}]
    blocks = _blocks(sections.get("BLOCKS", []))
    coordinate_systems = _header_coordinate_systems(sections.get("HEADER", []))
    relations: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    for entity in entities:
        handle = entity.get("handle")
        if handle:
            nodes.append({"id": handle, "type": entity.get("type"), "layer": entity.get("layer", "0")})
        if entity.get("type") == "INSERT" and entity.get("block_name"):
            relations.append({"from": handle, "to": f"block:{entity['block_name']}", "kind": "inserts"})
        if entity.get("type") == "HATCH":
            relations.extend({"from": handle, "to": ref, "kind": "boundary_ref"} for ref in entity.get("boundary_refs", []))
    nodes.extend({"id": f"block:{name}", "type": "BLOCK", "entity_count": block.get("entity_count", 0)} for name, block in blocks.items())

    layers: dict[str, int] = {}
    for item in entities:
        layer = str(item.get("layer", "0"))
        layers[layer] = layers.get(layer, 0) + 1
    return {"format": "DXF", "entities": entities, "entity_count": len(entities), "entity_types": _counts(entities), "layers": layers, "bbox": bbox(entities), "blocks": blocks, "coordinate_systems": coordinate_systems, "topology": {"nodes": nodes, "relations": relations}}


def read_dxf(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(16384)
    if b"$ACADVER" in header or header.startswith(b"AutoCAD Binary DXF"):
        from .native_dxf import read_native
        return read_native(path)
    # Compatibility with the headerless ASCII fixtures emitted by early
    # SimLoom versions. Full DXFs never silently fall back after a parse error.
    payload = parse_dxf(path.read_text(encoding="utf-8", errors="strict"))
    payload["backend"] = "legacy-ascii"
    return payload


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
    if kind in {"TEXT", "MTEXT"}:
        return [entity.get("insert", {})]
    if kind == "DIMENSION":
        return [entity.get("definition", {})]
    if kind == "INSERT":
        return [entity.get("insert", {})]
    if kind == "HATCH":
        return list(entity.get("boundary_vertices", []))
    if kind == "LEADER":
        return list(entity.get("vertices", []))
    if kind == "ELLIPSE":
        center, axis = entity.get("center", {}), entity.get("major_axis", {})
        radius = math.sqrt(_number(axis.get("x")) ** 2 + _number(axis.get("y")) ** 2 + _number(axis.get("z")) ** 2)
        return [{"x": _number(center.get("x")) - radius, "y": _number(center.get("y")) - radius, "z": _number(center.get("z"))}, {"x": _number(center.get("x")) + radius, "y": _number(center.get("y")) + radius, "z": _number(center.get("z"))}]
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
        elif kind in {"TEXT", "MTEXT"}:
            item["insert"] = transform_point(item.get("insert", {}))
        elif kind == "DIMENSION":
            item["definition"] = transform_point(item.get("definition", {}))
        elif kind == "INSERT":
            item["insert"] = transform_point(item.get("insert", {}))
        elif kind == "HATCH":
            item["boundary_vertices"] = [transform_point(point) for point in item.get("boundary_vertices", [])]
        elif kind == "LEADER":
            item["vertices"] = [transform_point(point) for point in item.get("vertices", [])]
        elif kind == "ELLIPSE":
            item["center"] = transform_point(item.get("center", {}))
            axis = item.get("major_axis", {})
            item["major_axis"] = {"x": _number(axis.get("x")) * float(scale), "y": _number(axis.get("y")) * float(scale), "z": _number(axis.get("z")) * float(scale)}
            item["ratio"] = _number(item.get("ratio"), 1.0)
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            item["vertices"] = [transform_point(point) for point in item.get("vertices", [])]
        transformed.append(item)
    blocks = {}
    for name, block in (document.get("blocks") or {}).items():
        block_copy = {key: value for key, value in block.items()}
        block_copy["base_point"] = transform_point(block.get("base_point", {}))
        block_copy["entities"] = transform_document({"entities": block.get("entities", [])}, translate=translate, scale=scale, rotate_deg=rotate_deg)["entities"]
        blocks[name] = block_copy
    return {"format": "DXF", "entities": transformed, "entity_count": len(transformed), "entity_types": _counts(transformed), "layers": {layer: sum(1 for item in transformed if item.get("layer", "0") == layer) for layer in sorted({str(item.get("layer", "0")) for item in transformed})}, "bbox": bbox(transformed), "blocks": blocks, "coordinate_systems": document.get("coordinate_systems", []), "topology": document.get("topology", {})}


def diff_documents(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_entities, right_entities = left.get("entities", []), right.get("entities", [])
    changes: list[dict[str, Any]] = []
    for key in ("layers", "bbox", "blocks", "coordinate_systems", "topology"):
        if left.get(key) != right.get(key):
            changes.append({"path": key, "kind": "changed", "before": left.get(key, {}), "after": right.get(key, {})})
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
