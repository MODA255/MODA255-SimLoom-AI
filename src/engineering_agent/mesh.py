from __future__ import annotations

import math
import json
import struct
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


def _glb_accessor(binary: bytes, document: dict[str, Any], index: int) -> list[Any]:
    accessors = document.get("accessors") or []
    views = document.get("bufferViews") or []
    if not isinstance(index, int) or index < 0 or index >= len(accessors):
        raise ValueError("GLB_ACCESSOR_INVALID")
    accessor = accessors[index]
    view_index = accessor.get("bufferView")
    if not isinstance(view_index, int) or view_index < 0 or view_index >= len(views):
        raise ValueError("GLB_BUFFERVIEW_INVALID")
    view = views[view_index]
    component = {5121: ("<B", 1), 5123: ("<H", 2), 5125: ("<I", 4), 5126: ("<f", 4)}.get(accessor.get("componentType"))
    shape = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}.get(accessor.get("type"))
    if component is None or shape is None:
        raise ValueError("GLB_ACCESSOR_TYPE_UNSUPPORTED")
    count = int(accessor.get("count", 0))
    if count < 0 or count > 600_000:
        raise ValueError("GLB_ACCESSOR_COUNT_INVALID")
    fmt, size = component
    stride = int(view.get("byteStride", size * shape))
    if stride < size * shape:
        raise ValueError("GLB_BYTE_STRIDE_INVALID")
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    output: list[Any] = []
    for row in range(count):
        offset = start + row * stride
        end = offset + size * shape
        if offset < 0 or end > len(binary):
            raise ValueError("GLB_ACCESSOR_OUT_OF_RANGE")
        values = [struct.unpack_from(fmt, binary, offset + column * size)[0] for column in range(shape)]
        output.append(values[0] if shape == 1 else values)
    return output


def parse_glb(data: bytes) -> dict[str, Any]:
    """Read a self-contained glTF 2.0 binary mesh with vertex colors."""
    if len(data) < 20:
        raise ValueError("GLB_HEADER_INVALID")
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2 or declared_length != len(data):
        raise ValueError("GLB_HEADER_INVALID")
    cursor, json_chunk, binary = 12, None, b""
    while cursor + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, cursor)
        cursor += 8
        end = cursor + chunk_length
        if end > len(data):
            raise ValueError("GLB_CHUNK_OUT_OF_RANGE")
        chunk = data[cursor:end]
        if chunk_type == 0x4E4F534A:
            json_chunk = chunk.rstrip(b" \t\r\n\x00")
        elif chunk_type == 0x004E4942:
            binary = chunk
        cursor = end
    if not json_chunk:
        raise ValueError("GLB_JSON_MISSING")
    try:
        document = json.loads(json_chunk.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GLB_JSON_INVALID") from exc
    meshes = document.get("meshes") or []
    if not meshes or not meshes[0].get("primitives"):
        raise ValueError("GLB_MESH_MISSING")
    primitive = meshes[0]["primitives"][0]
    if int(primitive.get("mode", 4)) != 4:
        raise ValueError("GLB_TRIANGLES_REQUIRED")
    attributes = primitive.get("attributes") or {}
    positions = _glb_accessor(binary, document, attributes.get("POSITION"))
    if not all(isinstance(point, list) and len(point) >= 3 for point in positions):
        raise ValueError("GLB_POSITION_INVALID")
    vertices = [_point(point) for point in positions]
    indices = _glb_accessor(binary, document, primitive.get("indices"))
    if len(indices) % 3:
        raise ValueError("GLB_INDEX_COUNT_INVALID")
    faces = [[int(indices[index]), int(indices[index + 1]), int(indices[index + 2])] for index in range(0, len(indices), 3)]
    metadata: dict[str, Any] = {"asset_version": document.get("asset", {}).get("version", "2.0")}
    color_accessor = attributes.get("COLOR_0")
    if color_accessor is not None:
        colors = _glb_accessor(binary, document, color_accessor)
        if len(colors) != len(vertices):
            raise ValueError("GLB_COLOR_COUNT_INVALID")
        metadata["colors"] = [[float(channel) for channel in color] for color in colors]
    extras = (meshes[0].get("extras") or {})
    if isinstance(extras, dict):
        if extras.get("scalar_name") is not None:
            metadata["scalar_name"] = str(extras["scalar_name"])
        if extras.get("scalar_range") is not None:
            metadata["scalar_range"] = extras["scalar_range"]
    return _mesh_result(vertices, faces, fmt="GLB", metadata=metadata)


def read_glb(path: str | Path) -> dict[str, Any]:
    return parse_glb(Path(path).read_bytes())


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
    if suffix == ".glb":
        return read_glb(path)
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


def write_glb(path: str | Path, vertices: Iterable[Iterable[float]], faces: Iterable[Iterable[int]], *,
              colors: Iterable[Iterable[float]] | None = None, scalar_name: str | None = None,
              scalar_range: Iterable[float] | None = None, name: str = "engineering_agent") -> str:
    """Write a compact self-contained glTF 2.0 triangle mesh."""
    path = Path(path)
    points = []
    for value in vertices:
        if isinstance(value, dict):
            point = [float(value.get(axis, 0.0)) for axis in ("x", "y", "z")]
        else:
            values = list(value)
            point = [float(values[index]) if index < len(values) else 0.0 for index in range(3)]
        if not all(math.isfinite(item) for item in point):
            raise ValueError("GLB_NON_FINITE_VERTEX")
        points.append(point)
    triangles: list[int] = []
    for face in faces:
        indices = [int(value) for value in face]
        if len(indices) < 3 or any(index < 0 or index >= len(points) for index in indices):
            raise ValueError("GLB_FACE_INDEX_INVALID")
        for index in range(1, len(indices) - 1):
            triangles.extend((indices[0], indices[index], indices[index + 1]))
    if not points or not triangles:
        raise ValueError("GLB_MESH_EMPTY")
    vertex_colors = []
    if colors is None:
        vertex_colors = [[0.36, 0.62, 0.96] for _ in points]
    else:
        vertex_colors = [[float(channel) for channel in list(color)[:3]] for color in colors]
        if len(vertex_colors) != len(points) or any(len(color) != 3 or not all(math.isfinite(channel) for channel in color) for color in vertex_colors):
            raise ValueError("GLB_COLOR_COUNT_INVALID")
    position_bytes = struct.pack("<" + "f" * (len(points) * 3), *(value for point in points for value in point))
    color_bytes = struct.pack("<" + "f" * (len(vertex_colors) * 3), *(value for color in vertex_colors for value in color))
    index_component = 5123 if len(points) <= 65535 else 5125
    index_format = "<H" if index_component == 5123 else "<I"
    index_bytes = struct.pack("<" + ("H" if index_component == 5123 else "I") * len(triangles), *triangles)

    def align(data: bytes) -> bytes:
        return data + b"\x00" * ((4 - len(data) % 4) % 4)

    position_offset = 0
    color_offset = len(align(position_bytes))
    index_offset = color_offset + len(align(color_bytes))
    binary = align(position_bytes) + align(color_bytes) + align(index_bytes)
    mins = [min(point[index] for point in points) for index in range(3)]
    maxs = [max(point[index] for point in points) for index in range(3)]
    extras: dict[str, Any] = {"name": name}
    if scalar_name is not None:
        extras["scalar_name"] = scalar_name
    if scalar_range is not None:
        values = [float(value) for value in scalar_range]
        if len(values) != 2 or not all(math.isfinite(value) for value in values) or values[0] > values[1]:
            raise ValueError("GLB_SCALAR_RANGE_INVALID")
        extras["scalar_range"] = values
    document = {
        "asset": {"version": "2.0", "generator": "SimLoom AI"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"name": name, "extras": extras, "primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1}, "indices": 2, "mode": 4}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": position_offset, "byteLength": len(position_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": color_offset, "byteLength": len(color_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": index_offset, "byteLength": len(index_bytes), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(points), "type": "VEC3", "min": mins, "max": maxs},
            {"bufferView": 1, "componentType": 5126, "count": len(vertex_colors), "type": "VEC3"},
            {"bufferView": 2, "componentType": index_component, "count": len(triangles), "type": "SCALAR"},
        ],
    }
    json_bytes = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    total_length = 12 + 8 + len(json_bytes) + 8 + len(binary)
    output = struct.pack("<4sII", b"glTF", 2, total_length)
    output += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    output += struct.pack("<II", len(binary), 0x004E4942) + binary
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output)
    return str(path)
