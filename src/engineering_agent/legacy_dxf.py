"""Compatibility writer for headerless SimLoom files with opaque records.

Never used to save a versioned DXF; these legacy records cannot be certified
by a native DXF audit. Their exact opaque group sequence is retained.
"""
from pathlib import Path
from typing import Any, Iterable

def write_legacy_dxf(
    path: str | Path,
    lines: Iterable[dict[str, float]] = (),
    circles: Iterable[dict[str, float]] = (),
    *,
    polylines: Iterable[dict[str, Any]] = (),
    points: Iterable[dict[str, float]] = (),
    arcs: Iterable[dict[str, float]] = (),
    texts: Iterable[dict[str, Any]] = (),
    dimensions: Iterable[dict[str, Any]] = (),
    hatches: Iterable[dict[str, Any]] = (),
    inserts: Iterable[dict[str, Any]] = (),
    entities: Iterable[dict[str, Any]] = (),
    blocks: Iterable[dict[str, Any]] = (),
    coordinate_systems: Iterable[dict[str, Any]] = (),
) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    def coord(item: dict[str, Any], axis: str, default: float = 0.0) -> Any:
        if axis in item:
            return item[axis]
        insert = item.get("insert", {})
        if isinstance(insert, dict):
            return insert.get(axis, default)
        index = {"x": 0, "y": 1, "z": 2}[axis]
        return insert[index] if len(insert) > index else default

    systems = list(coordinate_systems)
    if systems:
        ucs = next((item for item in systems if str(item.get("name", "")).upper() == "UCS"), systems[-1])
        origin, x_axis, y_axis = ucs.get("origin", {}), ucs.get("x_axis", {}), ucs.get("y_axis", {})
        chunks += ["0", "SECTION", "2", "HEADER", "9", "$UCSORG", "10", str(origin.get("x", 0)), "20", str(origin.get("y", 0)), "30", str(origin.get("z", 0)), "9", "$UCSXDIR", "10", str(x_axis.get("x", 1)), "20", str(x_axis.get("y", 0)), "30", str(x_axis.get("z", 0)), "9", "$UCSYDIR", "10", str(y_axis.get("x", 0)), "20", str(y_axis.get("y", 1)), "30", str(y_axis.get("z", 0)), "0", "ENDSEC"]
    all_entities: list[dict[str, Any]] = []
    all_entities.extend({"type": "LINE", "layer": item.get("layer", "0"), "start": {"x": item["x1"], "y": item["y1"], "z": item.get("z1", 0)}, "end": {"x": item["x2"], "y": item["y2"], "z": item.get("z2", 0)}} for item in lines)
    all_entities.extend({"type": "CIRCLE", "layer": item.get("layer", "0"), "center": {"x": item["x"], "y": item["y"], "z": item.get("z", 0)}, "radius": item["radius"]} for item in circles)
    all_entities.extend({"type": "LWPOLYLINE", "layer": item.get("layer", "0"), "vertices": [{"x": point[0], "y": point[1], "z": point[2] if len(point) > 2 else 0} for point in item.get("points", item.get("vertices", []))], "closed": bool(item.get("closed", False))} for item in polylines)
    all_entities.extend({"type": "POINT", "layer": item.get("layer", "0"), "point": {"x": item["x"], "y": item["y"], "z": item.get("z", 0)}} for item in points)
    all_entities.extend({"type": "ARC", "layer": item.get("layer", "0"), "center": {"x": item["x"], "y": item["y"], "z": item.get("z", 0)}, "radius": item["radius"], "start_angle": item.get("start_angle", item.get("start", 0)), "end_angle": item.get("end_angle", item.get("end", 360))} for item in arcs)
    all_entities.extend({"type": "TEXT", "layer": item.get("layer", "0"), "insert": {"x": coord(item, "x"), "y": coord(item, "y"), "z": coord(item, "z")}, "text": item.get("text", ""), "height": item.get("height", 1), "rotation": item.get("rotation", 0)} for item in texts)
    all_entities.extend({"type": "DIMENSION", "layer": item.get("layer", "0"), "definition": {"x": item.get("x", 0), "y": item.get("y", 0), "z": item.get("z", 0)}, "text": item.get("text", ""), "measurement": item.get("measurement", 0)} for item in dimensions)
    all_entities.extend({"type": "HATCH", "layer": item.get("layer", "0"), "pattern": item.get("pattern", "SOLID"), "solid": item.get("solid", True), "boundary_vertices": [{"x": point[0], "y": point[1], "z": point[2] if len(point) > 2 else 0} for point in item.get("points", item.get("boundary_vertices", []))]} for item in hatches)
    all_entities.extend({"type": "INSERT", "layer": item.get("layer", "0"), "block_name": item.get("block_name", item.get("name", "")), "insert": {"x": coord(item, "x"), "y": coord(item, "y"), "z": coord(item, "z")}, "scale": item.get("scale", {"x": 1, "y": 1, "z": 1}), "rotation": item.get("rotation", 0)} for item in inserts)
    all_entities.extend(entities)

    def common(item: dict[str, Any], kind: str) -> list[str]:
        """Emit shared DXF attributes while allowing unknown groups to round-trip."""
        output = ["0", kind, "8", str(item.get("layer", "0"))]
        if item.get("handle") is not None:
            output += ["5", str(item["handle"])]
        if item.get("owner") is not None:
            output += ["330", str(item["owner"])]
        if item.get("linetype") is not None:
            output += ["6", str(item["linetype"])]
        if item.get("color_index") is not None:
            output += ["62", str(int(item["color_index"]))]
        if item.get("true_color") is not None:
            output += ["420", str(int(item["true_color"]))]
        if item.get("lineweight") is not None:
            output += ["370", str(int(item["lineweight"]))]
        if item.get("thickness") is not None:
            output += ["39", str(item["thickness"])]
        return output

    def emit(item: dict[str, Any], output: list[str]):
        kind = str(item.get("type", "")).upper()
        if kind == "LINE":
            start, end = item.get("start", {}), item.get("end", {})
            output += common(item, "LINE") + ["10", str(start.get("x", 0)), "20", str(start.get("y", 0)), "30", str(start.get("z", 0)), "11", str(end.get("x", 0)), "21", str(end.get("y", 0)), "31", str(end.get("z", 0))]
        elif kind in {"RAY", "XLINE"}:
            start, direction = item.get("start", {}), item.get("unit_vector", {})
            output += common(item, kind) + ["10", str(start.get("x", 0)), "20", str(start.get("y", 0)), "30", str(start.get("z", 0)), "11", str(direction.get("x", 0)), "21", str(direction.get("y", 0)), "31", str(direction.get("z", 0))]
        elif kind == "CIRCLE":
            center = item.get("center", {})
            output += common(item, "CIRCLE") + ["10", str(center.get("x", 0)), "20", str(center.get("y", 0)), "30", str(center.get("z", 0)), "40", str(item.get("radius", 0))]
        elif kind == "ARC":
            center = item.get("center", {})
            output += common(item, "ARC") + ["10", str(center.get("x", 0)), "20", str(center.get("y", 0)), "30", str(center.get("z", 0)), "40", str(item.get("radius", 0)), "50", str(item.get("start_angle", 0)), "51", str(item.get("end_angle", 360))]
        elif kind == "POINT":
            point = item.get("point", {})
            output += common(item, "POINT") + ["10", str(point.get("x", 0)), "20", str(point.get("y", 0)), "30", str(point.get("z", 0))]
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            output += common(item, "LWPOLYLINE") + ["70", "1" if item.get("closed") else "0"]
            bulges = item.get("bulges") or []
            for index, point in enumerate(item.get("vertices", [])):
                output += ["10", str(point.get("x", 0)), "20", str(point.get("y", 0))]
                if index < len(bulges):
                    output += ["42", str(bulges[index])]
        elif kind == "TEXT":
            insert = item.get("insert", {})
            output += common(item, "TEXT") + ["10", str(insert.get("x", 0)), "20", str(insert.get("y", 0)), "30", str(insert.get("z", 0)), "40", str(item.get("height", 1)), "50", str(item.get("rotation", 0)), "1", str(item.get("text", ""))]
        elif kind == "MTEXT":
            insert = item.get("insert", {})
            output += common(item, "MTEXT") + ["10", str(insert.get("x", 0)), "20", str(insert.get("y", 0)), "30", str(insert.get("z", 0)), "40", str(item.get("height", 1)), "41", str(item.get("width", 0)), "50", str(item.get("rotation", 0)), "1", str(item.get("text", ""))]
        elif kind == "LEADER":
            output += common(item, "LEADER") + ["71", "1" if item.get("arrowhead", True) else "0", "3", str(item.get("text", ""))]
            for point in item.get("vertices", []):
                output += ["10", str(point.get("x", 0)), "20", str(point.get("y", 0)), "30", str(point.get("z", 0))]
        elif kind == "ELLIPSE":
            center, axis = item.get("center", {}), item.get("major_axis", {})
            output += common(item, "ELLIPSE") + ["10", str(center.get("x", 0)), "20", str(center.get("y", 0)), "30", str(center.get("z", 0)), "11", str(axis.get("x", 1)), "21", str(axis.get("y", 0)), "31", str(axis.get("z", 0)), "40", str(item.get("ratio", 1)), "41", str(item.get("start_param", 0)), "42", str(item.get("end_param", 6.283185307179586))]
        elif kind == "DIMENSION":
            definition = item.get("definition", {})
            dimension_type = str(item.get("dimension_type", "linear"))
            dimension_code = {"linear": 0, "angular": 2, "diameter": 3, "radial": 4, "radius": 4, "ordinate": 7 if str(item.get("axis", "x")).lower() == "y" else 6}.get(dimension_type, 0)
            output += common(item, "DIMENSION") + ["70", str(dimension_code), "10", str(definition.get("x", 0)), "20", str(definition.get("y", 0)), "30", str(definition.get("z", 0)), "42", str(item.get("measurement", 0)), "1", str(item.get("text", "")), "2", str(item.get("block_name", ""))]
        elif kind == "INSERT":
            insert, scale = item.get("insert", {}), item.get("scale", {})
            output += common(item, "INSERT") + ["2", str(item.get("block_name", "")), "10", str(insert.get("x", 0)), "20", str(insert.get("y", 0)), "30", str(insert.get("z", 0)), "41", str(scale.get("x", 1)), "42", str(scale.get("y", 1)), "43", str(scale.get("z", 1)), "50", str(item.get("rotation", 0))]
        elif kind == "HATCH":
            output += common(item, "HATCH") + ["2", str(item.get("pattern", "SOLID")), "70", "1" if item.get("solid", True) else "0", "91", str(item.get("boundary_path_count", 1)), "75", str(item.get("hatch_style", 0)), "76", str(item.get("pattern_type", 0)), "52", str(item.get("pattern_angle", 0)), "41", str(item.get("pattern_scale", 1)), "77", "1" if item.get("double", False) else "0", "92", "7", "93", str(len(item.get("boundary_vertices", [])))]
            for point in item.get("boundary_vertices", []):
                output += ["10", str(point.get("x", 0)), "20", str(point.get("y", 0))]
        else:
            raw_tags = item.get("raw_tags") or []
            if raw_tags:
                output += ["0", kind]
                # Repeated subclass markers, coordinates and owner references
                # must retain their original order in an opaque entity.
                overrides = {"8": str(item.get("layer", "0"))}
                seen = set()
                for code, value in raw_tags:
                    code = str(code)
                    output += [code, overrides.get(code, str(value)) if code not in seen else str(value)]
                    seen.add(code)
                if "8" not in seen:
                    output += ["8", overrides["8"]]
                return
            raw_groups = item.get("raw_groups") or {}
            if raw_groups:
                output += common(item, kind)
                for code, values in raw_groups.items():
                    if str(code) in {"0", "5", "8", "330", "6", "62", "420", "370", "39"}:
                        continue
                    for value in (values if isinstance(values, list) else [values]):
                        output += [str(code), str(value)]
            else:
                raise ValueError("UNSUPPORTED_DXF_ENTITY:" + kind)

    if blocks:
        chunks += ["0", "SECTION", "2", "BLOCKS"]
        for block in blocks:
            base = block.get("base_point", {})
            chunks += ["0", "BLOCK", "2", str(block.get("name", "")), "70", "0", "10", str(base.get("x", 0)), "20", str(base.get("y", 0)), "30", str(base.get("z", 0))]
            for entity in block.get("entities", []):
                emit(entity, chunks)
            chunks += ["0", "ENDBLK"]
        chunks += ["0", "ENDSEC"]
    chunks += ["0", "SECTION", "2", "ENTITIES"]
    for item in all_entities:
        emit(item, chunks)
    chunks += ["0", "ENDSEC", "0", "EOF"]
    # UTF-8 preserves GD&T and engineering annotation glyphs (⌀, ⌯, etc.)
    # while remaining readable by the parser and modern DXF consumers.
    path.write_text("\n".join(chunks) + "\n", encoding="utf-8")
    return str(path)

