from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable


def _point(value: Iterable[Any]) -> dict[str, float]:
    values = list(value)
    return {"x": float(values[0]), "y": float(values[1]), "z": float(values[2] if len(values) > 2 else 0.0)}


def _cross(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    return {"x": a["y"] * b["z"] - a["z"] * b["y"], "y": a["z"] * b["x"] - a["x"] * b["z"], "z": a["x"] * b["y"] - a["y"] * b["x"]}


def _sub(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    return {axis: a[axis] - b[axis] for axis in ("x", "y", "z")}


def _norm(value: dict[str, float]) -> float:
    return math.sqrt(sum(value[axis] ** 2 for axis in ("x", "y", "z")))


def _area(vertices: list[dict[str, float]], face: list[int]) -> float:
    if len(face) < 3:
        return 0.0
    origin = vertices[face[0]]
    total = 0.0
    for index in range(1, len(face) - 1):
        total += _norm(_cross(_sub(vertices[face[index]], origin), _sub(vertices[face[index + 1]], origin))) / 2.0
    return total


def _mesh_result(vertices: list[dict[str, float]], faces: list[list[int]], *,
                 fmt: str, scalars: list[float] | None = None,
                 scalar_name: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    valid_faces = [face for face in faces if len(face) >= 3 and all(0 <= index < len(vertices) for index in face)]
    if len(valid_faces) != len(faces):
        raise ValueError("MESH_FACE_INDEX_INVALID")
    bbox = {}
    if vertices:
        bbox = {axis: {"min": min(point[axis] for point in vertices), "max": max(point[axis] for point in vertices)}
                for axis in ("x", "y", "z")}
    result = {"format": fmt, "vertices": vertices, "faces": valid_faces,
              "vertex_count": len(vertices), "face_count": len(valid_faces),
              "triangle_count": sum(max(len(face) - 2, 0) for face in valid_faces),
              "surface_area": sum(_area(vertices, face) for face in valid_faces), "bbox": bbox}
    if scalars is not None:
        if len(scalars) != len(vertices) or not all(math.isfinite(value) for value in scalars):
            raise ValueError("MESH_SCALAR_COUNT_INVALID")
        result["scalars"] = scalars
        result["scalar_name"] = scalar_name or "scalar"
        result["scalar_range"] = [min(scalars), max(scalars)] if scalars else None
    if metadata:
        result.update(metadata)
    return result


def parse_obj(text: str) -> dict[str, Any]:
    vertices: list[dict[str, float]] = []
    normals: list[dict[str, float]] = []
    faces: list[list[int]] = []
    groups: list[str] = []
    material: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "v" and len(parts) >= 3:
            vertices.append(_point(parts[1:]))
        elif parts[0] == "vn" and len(parts) >= 4:
            normals.append(_point(parts[1:]))
        elif parts[0] == "f" and len(parts) >= 4:
            indices: list[int] = []
            for token in parts[1:]:
                raw_index = token.split("/", 1)[0]
                index = int(raw_index)
                indices.append(index - 1 if index > 0 else len(vertices) + index)
            faces.append(indices)
        elif parts[0] in {"g", "o"} and len(parts) > 1:
            groups.append(" ".join(parts[1:]))
        elif parts[0] == "usemtl" and len(parts) > 1:
            material = parts[1]
    result = _mesh_result(vertices, faces, fmt="OBJ")
    result.update({"normals": normals, "groups": groups, "material": material})
    return result


def read_obj(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    return parse_obj(path.read_text(encoding="utf-8", errors="replace"))


def _numbers(tokens: list[str], start: int, count: int, code: str) -> tuple[list[float], int]:
    end = start + count
    if end > len(tokens):
        raise ValueError(code)
    try:
        values = [float(value) for value in tokens[start:end]]
    except (TypeError, ValueError) as exc:
        raise ValueError(code) from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError(code)
    return values, end


def parse_vtk(text: str) -> dict[str, Any]:
    """Read an ASCII legacy VTK POLYDATA mesh and optional point scalars."""
    tokens = text.replace("\r", " ").split()
    if len(tokens) < 5 or tokens[0] != "#" or tokens[1].lower() != "vtk":
        raise ValueError("VTK_HEADER_INVALID")
    try:
        dataset = tokens.index("DATASET")
    except ValueError as exc:
        raise ValueError("VTK_DATASET_MISSING") from exc
    if dataset + 1 >= len(tokens) or tokens[dataset + 1].upper() != "POLYDATA":
        raise ValueError("VTK_POLYDATA_REQUIRED")
    try:
        point_index = tokens.index("POINTS", dataset + 2)
        point_count = int(tokens[point_index + 1])
    except (ValueError, IndexError) as exc:
        raise ValueError("VTK_POINTS_INVALID") from exc
    if point_count < 0 or point_count > 600_000:
        raise ValueError("VTK_POINT_LIMIT")
    values, cursor = _numbers(tokens, point_index + 3, point_count * 3, "VTK_POINTS_INVALID")
    vertices = [_point(values[index:index + 3]) for index in range(0, len(values), 3)]
    try:
        polygon_index = tokens.index("POLYGONS", cursor)
    except ValueError:
        raise ValueError("VTK_POLYGONS_REQUIRED")
    try:
        polygon_count, _size = int(tokens[polygon_index + 1]), int(tokens[polygon_index + 2])
    except (ValueError, IndexError) as exc:
        raise ValueError("VTK_POLYGONS_INVALID") from exc
    if polygon_count < 0 or polygon_count > 600_000:
        raise ValueError("VTK_POLYGON_LIMIT")
    cursor = polygon_index + 3
    faces: list[list[int]] = []
    for _ in range(polygon_count):
        try:
            count = int(tokens[cursor])
        except (ValueError, IndexError) as exc:
            raise ValueError("VTK_POLYGON_INVALID") from exc
        cursor += 1
        if count < 3 or cursor + count > len(tokens):
            raise ValueError("VTK_POLYGON_INVALID")
        try:
            face = [int(item) for item in tokens[cursor:cursor + count]]
        except ValueError as exc:
            raise ValueError("VTK_POLYGON_INVALID") from exc
        faces.append(face)
        cursor += count
    scalars = None
    scalar_name = None
    try:
        point_data = tokens.index("POINT_DATA", cursor)
        data_count = int(tokens[point_data + 1])
        scalar_index = tokens.index("SCALARS", point_data + 2)
        scalar_name = tokens[scalar_index + 1]
        component_cursor = scalar_index + 3
        components = 1
        if component_cursor < len(tokens) and tokens[component_cursor].upper() != "LOOKUP_TABLE":
            components = int(tokens[component_cursor])
            component_cursor += 1
        lookup = tokens.index("LOOKUP_TABLE", component_cursor)
        value_cursor = lookup + 2
        raw, _ = _numbers(tokens, value_cursor, data_count * components, "VTK_SCALARS_INVALID")
        scalars = raw[::components]
    except (ValueError, IndexError):
        # Point data is optional; malformed optional data is rejected only if
        # a SCALARS section was explicitly present.
        if "SCALARS" in tokens[cursor:]:
            raise ValueError("VTK_SCALARS_INVALID")
    return _mesh_result(vertices, faces, fmt="VTK", scalars=scalars, scalar_name=scalar_name,
                        metadata={"dataset": "POLYDATA"})


def _xml_data_array(element: ET.Element | None, *, expected: int | None = None) -> list[float]:
    if element is None or str(element.get("format", "ascii")).lower() != "ascii":
        raise ValueError("VTU_ASCII_REQUIRED")
    text = " ".join((element.text or "").split())
    values, _ = _numbers(text.split(), 0, len(text.split()), "VTU_DATA_INVALID")
    if expected is not None and len(values) != expected:
        raise ValueError("VTU_DATA_COUNT_INVALID")
    return values


def parse_vtu(text: str) -> dict[str, Any]:
    """Read an ASCII VTU UnstructuredGrid with triangle/quad/polygon cells."""
    try:
        root = ET.fromstring(text)
        piece = root.find(".//Piece")
        points_array = piece.find("./Points/DataArray") if piece is not None else None
    except ET.ParseError as exc:
        raise ValueError("VTU_XML_INVALID") from exc
    if piece is None or points_array is None:
        raise ValueError("VTU_POINTS_MISSING")
    point_count = int(piece.get("NumberOfPoints", "0"))
    if point_count < 0 or point_count > 600_000:
        raise ValueError("VTU_POINT_LIMIT")
    point_values = _xml_data_array(points_array, expected=point_count * int(points_array.get("NumberOfComponents", "3")))
    components = int(points_array.get("NumberOfComponents", "3"))
    if components < 3:
        raise ValueError("VTU_POINTS_INVALID")
    vertices = [_point(point_values[index:index + components]) for index in range(0, len(point_values), components)]
    cells = piece.find("./Cells")
    if cells is None:
        raise ValueError("VTU_CELLS_MISSING")
    arrays = {str(item.get("Name", "")): item for item in cells.findall("DataArray")}
    connectivity = [int(value) for value in _xml_data_array(arrays.get("connectivity"))]
    offsets = [int(value) for value in _xml_data_array(arrays.get("offsets"))]
    types = [int(value) for value in _xml_data_array(arrays.get("types"))]
    if not (len(offsets) == len(types)):
        raise ValueError("VTU_CELL_DATA_INVALID")
    faces, start = [], 0
    for offset, cell_type in zip(offsets, types):
        if offset < start or offset > len(connectivity):
            raise ValueError("VTU_CELL_DATA_INVALID")
        cell = connectivity[start:offset]
        # VTK cell types: triangle=5, polygon=7, quad=9.
        if cell_type in {5, 7, 9}:
            faces.append(cell)
        start = offset
    scalars, scalar_name = None, None
    point_data = piece.find("./PointData")
    if point_data is not None:
        for array in point_data.findall("DataArray"):
            values = _xml_data_array(array)
            if len(values) == point_count:
                scalars, scalar_name = values, array.get("Name") or "scalar"
                break
    return _mesh_result(vertices, faces, fmt="VTU", scalars=scalars, scalar_name=scalar_name,
                        metadata={"dataset": "UnstructuredGrid", "cell_count": len(types)})


def read_mesh(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".obj":
        return parse_obj(text)
    if suffix == ".vtk":
        return parse_vtk(text)
    if suffix == ".vtu":
        return parse_vtu(text)
    raise ValueError("UNSUPPORTED_MESH_FORMAT:" + suffix)


def write_obj(path: str | Path, vertices: Iterable[Iterable[float]], faces: Iterable[Iterable[int]], *, name: str = "engineering_agent") -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {name}"]
    lines.extend("v " + " ".join(f"{float(value):.9g}" for value in vertex) for vertex in vertices)
    lines.extend("f " + " ".join(str(int(index) + 1) for index in face) for face in faces)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
