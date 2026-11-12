from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .mesh import write_obj


def write_json(path: str | Path, value: Any) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def write_csv(path: str | Path, rows: Iterable[dict[str, Any]], columns: list[str] | None = None) -> str:
    import csv

    rows = list(rows)
    columns = columns or sorted({key for row in rows for key in row})
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def write_step(path: str | Path, points: Iterable[Iterable[float]], name: str = "engineering_agent") -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    point_lines = []
    ids = []
    next_id = 10
    for point in points:
        x, y, z = [float(value) for value in point]
        point_lines.append(f"#{next_id}=CARTESIAN_POINT('',({x:.9g},{y:.9g},{z:.9g}));")
        ids.append(next_id)
        next_id += 1
    content = "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Engineering Agent'),'2;1');\nFILE_NAME('" + name + ".step',('Engineering Agent'),('Engineering Agent'),'','Engineering Agent','','');\nFILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));\nENDSEC;\nDATA;\n" + "\n".join(point_lines) + "\nENDSEC;\nEND-ISO-10303-21;\n"
    path.write_text(content, encoding="utf-8")
    return str(path)


def write_dxf(
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
    def coord(item: dict[str, Any], axis: str, default: float = 0.0) -> Any:
        if axis in item:
            return item[axis]
        insert = item.get("insert", {})
        if isinstance(insert, dict):
            return insert.get(axis, default)
        index = {"x": 0, "y": 1, "z": 2}[axis]
        return insert[index] if len(insert) > index else default

    systems = list(coordinate_systems)
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

    from .native_dxf import write_native
    return write_native(path, entities=all_entities, blocks=list(blocks), coordinate_systems=systems)


def write_svg(path: str | Path, *, width: int = 800, height: int = 500, lines: Iterable[dict[str, float]] = (), circles: Iterable[dict[str, float]] = (), title: str = "Engineering Agent") -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}'><title>{title}</title><rect width='100%' height='100%' fill='#f7f9fc'/>"]
    body.extend(f"<line x1='{item['x1']}' y1='{height-item['y1']}' x2='{item['x2']}' y2='{height-item['y2']}' stroke='#2855c5' stroke-width='2'/>" for item in lines)
    body.extend(f"<circle cx='{item['x']}' cy='{height-item['y']}' r='{item['radius']}' fill='none' stroke='#37a169' stroke-width='2'/>" for item in circles)
    body.append("</svg>")
    path.write_text("".join(body), encoding="utf-8")
    return str(path)


def write_pdf(path: str | Path, text: str) -> str:
    """Write a dependency-free one-page PDF report with plain text."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")[:4000]
    stream = f"BT /F1 11 Tf 50 760 Td ({safe}) Tj ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(stream.encode('latin1'))} >>\nstream\n{stream}\nendstream"]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n{obj}\nendobj\n".encode("latin1"))
    xref = len(output)
    output.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    path.write_bytes(bytes(output))
    return str(path)
