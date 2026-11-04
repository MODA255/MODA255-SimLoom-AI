from __future__ import annotations

import math
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
    valid_faces = [face for face in faces if all(0 <= index < len(vertices) for index in face)]
    bbox = {}
    if vertices:
        bbox = {axis: {"min": min(point[axis] for point in vertices), "max": max(point[axis] for point in vertices)} for axis in ("x", "y", "z")}
    return {"format": "OBJ", "vertices": vertices, "normals": normals, "faces": valid_faces, "groups": groups, "material": material, "vertex_count": len(vertices), "face_count": len(valid_faces), "triangle_count": sum(max(len(face) - 2, 0) for face in valid_faces), "surface_area": sum(_area(vertices, face) for face in valid_faces), "bbox": bbox}


def read_obj(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    return parse_obj(path.read_text(encoding="utf-8", errors="replace"))


def write_obj(path: str | Path, vertices: Iterable[Iterable[float]], faces: Iterable[Iterable[int]], *, name: str = "engineering_agent") -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {name}"]
    lines.extend("v " + " ".join(f"{float(value):.9g}" for value in vertex) for vertex in vertices)
    lines.extend("f " + " ".join(str(int(index) + 1) for index in face) for face in faces)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
