from __future__ import annotations

"""Transactional, vendor-neutral 2D CAD operations.

Every mutating operation is applied to a document copy, validated, diffed and
only then committed through :class:`RunTransaction`.  The same engine is used
by the Python API, MCP and Web endpoints.
"""

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from .cad_model import Block, CadDocument, Constraint, Layer, SelectionSet, transform_entity, validate_cad_document
from .dxf import diff_documents
from .formats import write_dxf
from .transactions import RunTransaction
from .cad_history import CadHistory
from .native_dxf import write_native
from .controlled_scripts import ScriptPolicyError, run_controlled_script
from .cad_diff_view import visual_diff
from .pid_validation import audit_pid


def _point(value: Any) -> dict[str, float]:
    if isinstance(value, (list, tuple)):
        value = {"x": value[0] if len(value) > 0 else 0, "y": value[1] if len(value) > 1 else 0, "z": value[2] if len(value) > 2 else 0}
    value = value or {}
    return {"x": float(value.get("x", 0)), "y": float(value.get("y", 0)), "z": float(value.get("z", 0))}


def _refs(arguments: dict[str, Any], document: CadDocument) -> list[str]:
    values = arguments.get("entity_ids") or arguments.get("refs") or arguments.get("selection") or []
    if isinstance(values, str):
        values = [values]
    if not values and arguments.get("entity_id"):
        values = [arguments["entity_id"]]
    if isinstance(values, dict):
        values = [values.get("entity_id")]
    if not values and arguments.get("selection_set"):
        selection = document.selection_sets.get(str(arguments["selection_set"]))
        values = selection.refs if selection else []
    if not values and arguments.get("all_entities"):
        values = [str(item.get("id")) for item in document.entities]
    return [str(value.get("entity_id") if isinstance(value, dict) else value) for value in values if value]


def _expand_associative_refs(document: CadDocument, ids: list[str]) -> list[str]:
    """Expand a transform selection to keep associative HATCH boundaries intact."""
    by_ref = {str(item.get("id")): item for item in document.entities}
    by_ref.update({str(item.get("handle")): item for item in document.entities if item.get("handle")})
    selected = {str(value) for value in ids}
    hatches = [item for item in document.entities
               if str(item.get("type", "")).upper() == "HATCH" and item.get("associative")]
    changed = True
    while changed:
        changed = False
        for hatch in hatches:
            hatch_id = str(hatch.get("id"))
            source_handles = {str(handle)
                              for path in hatch.get("boundary_paths", [])
                              for handle in path.get("source_handles", [])}
            missing = sorted(handle for handle in source_handles if by_ref.get(handle) is None)
            source_entities = [by_ref[handle] for handle in source_handles if handle in by_ref]
            if hatch_id in selected or any(str(entity.get("id")) in selected for entity in source_entities if entity):
                if missing:
                    raise ValueError("ASSOCIATIVE_HATCH_SOURCE_NOT_FOUND:" + hatch_id)
                for entity in source_entities:
                    entity_id = str(entity.get("id"))
                    if entity_id not in selected:
                        selected.add(entity_id)
                        changed = True
                if hatch_id not in selected:
                    selected.add(hatch_id)
                    changed = True
    return [str(item.get("id")) for item in document.entities if str(item.get("id")) in selected]


def _merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(target)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _line_offset(entity: dict[str, Any], distance: float) -> dict[str, Any]:
    start, end = entity.get("start", {}), entity.get("end", {})
    dx, dy = float(end.get("x", 0)) - float(start.get("x", 0)), float(end.get("y", 0)) - float(start.get("y", 0))
    length = math.hypot(dx, dy)
    if length == 0:
        return entity
    nx, ny = -dy / length * distance, dx / length * distance
    output = copy.deepcopy(entity)
    output["start"] = {"x": float(start.get("x", 0)) + nx, "y": float(start.get("y", 0)) + ny, "z": float(start.get("z", 0))}
    output["end"] = {"x": float(end.get("x", 0)) + nx, "y": float(end.get("y", 0)) + ny, "z": float(end.get("z", 0))}
    return output


def _polyline_offset(entity: dict[str, Any], distance: float, tolerance: float = 1e-10) -> dict[str, Any]:
    """Offset a straight LWPOLYLINE/POLYLINE while preserving connectivity.

    The operation uses the signed left-hand normal of each segment.  At a
    vertex, adjacent offset supporting lines are intersected instead of
    simply translating the vertex; this keeps corners connected and mirrors
    the geometric meaning of a CAD offset.  Bulged segments need a curve
    offset kernel and are rejected explicitly rather than approximated.
    """
    vertices = [_point(value) for value in entity.get("vertices", [])]
    if len(vertices) < 2:
        raise ValueError("OFFSET_POLYLINE_TOO_SHORT")
    bulges = entity.get("bulges") or []
    if any(abs(float(value)) > tolerance for value in bulges):
        raise ValueError("OFFSET_BULGED_POLYLINE_UNSUPPORTED")
    closed = bool(entity.get("closed"))
    if closed and len(vertices) < 3:
        raise ValueError("OFFSET_CLOSED_POLYLINE_TOO_SHORT")
    segment_count = len(vertices) if closed else len(vertices) - 1
    segments = []
    for index in range(segment_count):
        start, end = vertices[index], vertices[(index + 1) % len(vertices)]
        segment = {"start": start, "end": end}
        shifted = _line_offset(segment, distance)
        if shifted["start"] == shifted["end"]:
            raise ValueError("OFFSET_ZERO_LENGTH_SEGMENT")
        segments.append(shifted)
    output_vertices = []
    if closed:
        for index in range(len(vertices)):
            corner = _line_intersection(segments[index - 1], segments[index], tolerance)
            if corner is None:
                raise ValueError("OFFSET_PARALLEL_POLYLINE_SEGMENTS")
            output_vertices.append(corner)
    else:
        output_vertices.append(segments[0]["start"])
        for index in range(1, len(vertices) - 1):
            corner = _line_intersection(segments[index - 1], segments[index], tolerance)
            if corner is None:
                raise ValueError("OFFSET_PARALLEL_POLYLINE_SEGMENTS")
            output_vertices.append(corner)
        output_vertices.append(segments[-1]["end"])
    output = copy.deepcopy(entity)
    output["vertices"] = output_vertices
    if "bulges" in output:
        output["bulges"] = [0.0] * len(output_vertices)
    return output


def _distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    return math.sqrt(sum((float(left.get(axis, 0)) - float(right.get(axis, 0))) ** 2 for axis in ("x", "y", "z")))


def _line_point(entity: dict[str, Any], key: str) -> dict[str, float]:
    return _point(entity.get(key) or {})


def _copy_with_transform(document: CadDocument, entity: dict[str, Any], *, dx: float = 0.0, dy: float = 0.0,
                         dz: float = 0.0, angle: float = 0.0, center: dict[str, Any] | None = None,
                         prefix: str = "copy") -> dict[str, Any]:
    copied = transform_entity(entity, dx=dx, dy=dy, dz=dz, angle=angle, center=center)
    copied["id"] = document.next_id(prefix)
    return copied


def _entity_change_ids(before: CadDocument, after: CadDocument) -> dict[str, list[str]]:
    """Return stable IDs for added, removed and changed CAD entities."""
    left = {str(item.get("id")): item for item in before.entities}
    right = {str(item.get("id")): item for item in after.entities}
    added = sorted(set(right) - set(left))
    removed = sorted(set(left) - set(right))
    changed = sorted(identifier for identifier in set(left) & set(right) if left[identifier] != right[identifier])
    return {"added_ids": added, "removed_ids": removed, "changed_ids": changed,
            "entity_ids": sorted(set(added) | set(removed) | set(changed))}


def _chain_lines(lines: list[dict[str, Any]], tolerance: float) -> list[dict[str, float]]:
    """Build one ordered vertex chain from connected LINE entities."""
    if not lines:
        return []
    remaining = [copy.deepcopy(item) for item in lines]
    first = remaining.pop(0)
    vertices = [_line_point(first, "start"), _line_point(first, "end")]
    while remaining:
        tail = vertices[-1]
        match_index = None
        match_point = None
        for index, candidate in enumerate(remaining):
            start, end = _line_point(candidate, "start"), _line_point(candidate, "end")
            if _distance(tail, start) <= tolerance:
                match_index, match_point = index, end
                break
            if _distance(tail, end) <= tolerance:
                match_index, match_point = index, start
                break
        if match_index is None:
            raise ValueError("LINES_NOT_CONNECTED")
        remaining.pop(match_index)
        vertices.append(match_point)
    return vertices


def _line_intersection(first: dict[str, Any], second: dict[str, Any], tolerance: float = 1e-12) -> dict[str, float] | None:
    a, b = _line_point(first, "start"), _line_point(first, "end")
    c, d = _line_point(second, "start"), _line_point(second, "end")
    abx, aby = b["x"] - a["x"], b["y"] - a["y"]
    cdx, cdy = d["x"] - c["x"], d["y"] - c["y"]
    denominator = abx * cdy - aby * cdx
    if abs(denominator) <= tolerance:
        return None
    acx, acy = c["x"] - a["x"], c["y"] - a["y"]
    factor = (acx * cdy - acy * cdx) / denominator
    return {"x": a["x"] + factor * abx, "y": a["y"] + factor * aby, "z": a["z"] + factor * (b["z"] - a["z"])}


def _segment_intersection(first: dict[str, Any], second: dict[str, Any], tolerance: float = 1e-9) -> dict[str, float] | None:
    """Return an intersection only when it lies on both finite segments."""
    a, b = _line_point(first, "start"), _line_point(first, "end")
    c, d = _line_point(second, "start"), _line_point(second, "end")
    abx, aby = b["x"] - a["x"], b["y"] - a["y"]
    cdx, cdy = d["x"] - c["x"], d["y"] - c["y"]
    denominator = abx * cdy - aby * cdx
    if abs(denominator) <= tolerance:
        return None
    acx, acy = c["x"] - a["x"], c["y"] - a["y"]
    first_factor = (acx * cdy - acy * cdx) / denominator
    second_factor = (acx * aby - acy * abx) / denominator
    if first_factor < -tolerance or first_factor > 1 + tolerance or second_factor < -tolerance or second_factor > 1 + tolerance:
        return None
    first_factor = min(1.0, max(0.0, first_factor))
    return {"x": a["x"] + first_factor * abx, "y": a["y"] + first_factor * aby,
            "z": a["z"] + first_factor * (b["z"] - a["z"])}


def _line_polyline_intersections(line: dict[str, Any], boundary: dict[str, Any], tolerance: float = 1e-9) -> list[dict[str, float]]:
    vertices = [_point(value) for value in boundary.get("vertices", [])]
    if len(vertices) < 2:
        raise ValueError("TRIM_BOUNDARY_TOO_SHORT")
    bulges = boundary.get("bulges") or []
    if any(abs(float(value)) > tolerance for value in bulges):
        raise ValueError("TRIM_BULGED_BOUNDARY_UNSUPPORTED")
    count = len(vertices) if boundary.get("closed") else len(vertices) - 1
    points: list[dict[str, float]] = []
    for index in range(count):
        segment = {"start": vertices[index], "end": vertices[(index + 1) % len(vertices)]}
        point = _segment_intersection(line, segment, tolerance)
        if point is not None and not any(_distance(point, existing) <= tolerance for existing in points):
            points.append(point)
    return points


def _line_circle_intersections(line: dict[str, Any], circle: dict[str, Any], tolerance: float = 1e-9) -> list[dict[str, float]]:
    """Return finite segment/circle intersections in deterministic order."""
    start, end = _line_point(line, "start"), _line_point(line, "end")
    center = _point(circle.get("center"))
    radius = abs(float(circle.get("radius", 0)))
    dx, dy = end["x"] - start["x"], end["y"] - start["y"]
    fx, fy = start["x"] - center["x"], start["y"] - center["y"]
    aa = dx * dx + dy * dy
    if aa <= tolerance * tolerance:
        return []
    bb = 2.0 * (fx * dx + fy * dy)
    cc = fx * fx + fy * fy - radius * radius
    discriminant = bb * bb - 4.0 * aa * cc
    if discriminant < -tolerance:
        return []
    roots = [(-bb - math.sqrt(max(0.0, discriminant))) / (2.0 * aa)]
    if discriminant > tolerance:
        roots.append((-bb + math.sqrt(discriminant)) / (2.0 * aa))
    output = []
    for factor in sorted(roots):
        if -tolerance <= factor <= 1.0 + tolerance:
            factor = min(1.0, max(0.0, factor))
            point = {"x": start["x"] + factor * dx, "y": start["y"] + factor * dy,
                     "z": start["z"] + factor * (end["z"] - start["z"])}
            if not output or _distance(output[-1], point) > tolerance:
                output.append(point)
    return output


def _angle(point: dict[str, Any], center: dict[str, Any]) -> float:
    return math.degrees(math.atan2(float(point.get("y", 0)) - float(center.get("y", 0)),
                                   float(point.get("x", 0)) - float(center.get("x", 0)))) % 360.0


def _angle_on_sweep(value: float, start: float, end: float, tolerance: float = 1e-7) -> bool:
    """Check a counter-clockwise DXF arc sweep, including wrapped angles."""
    value, start, end = value % 360.0, start % 360.0, end % 360.0
    sweep = (end - start) % 360.0
    distance = (value - start) % 360.0
    return distance <= sweep + tolerance


def _filter_arc_intersections(points: list[dict[str, float]], arc: dict[str, Any], tolerance: float = 1e-7) -> list[dict[str, float]]:
    center = _point(arc.get("center"))
    start, end = float(arc.get("start_angle", 0)), float(arc.get("end_angle", 360))
    return [point for point in points if _angle_on_sweep(_angle(point, center), start, end, tolerance)]


def _project_to_line(point: dict[str, Any], line: dict[str, Any]) -> dict[str, float]:
    start, end = _line_point(line, "start"), _line_point(line, "end")
    dx, dy, dz = end["x"] - start["x"], end["y"] - start["y"], end["z"] - start["z"]
    length_sq = dx * dx + dy * dy + dz * dz
    if length_sq <= 1e-18:
        raise ValueError("ZERO_LENGTH_LINE")
    value = _point(point)
    factor = ((value["x"] - start["x"]) * dx + (value["y"] - start["y"]) * dy + (value["z"] - start["z"]) * dz) / length_sq
    factor = max(0.0, min(1.0, factor))
    return {"x": start["x"] + factor * dx, "y": start["y"] + factor * dy, "z": start["z"] + factor * dz}


def _shared_corner(first: dict[str, Any], second: dict[str, Any], tolerance: float) -> tuple[dict[str, float], dict[str, float], dict[str, float]] | None:
    endpoints = [(_line_point(first, "start"), _line_point(first, "end")), (_line_point(second, "start"), _line_point(second, "end"))]
    for first_corner, first_other in ((endpoints[0][0], endpoints[0][1]), (endpoints[0][1], endpoints[0][0])):
        for second_corner, second_other in ((endpoints[1][0], endpoints[1][1]), (endpoints[1][1], endpoints[1][0])):
            if _distance(first_corner, second_corner) <= tolerance:
                return first_corner, first_other, second_other
    return None


def _corner_geometry(first: dict[str, Any], second: dict[str, Any], tolerance: float):
    """Resolve an XY corner on the original supporting lines, before any trim."""
    if first.get("sheet", "Model") != second.get("sheet", "Model"):
        raise ValueError("FILLET_CHAMFER_DIFFERENT_LAYOUTS")
    points = [_line_point(line, key) for line in (first, second) for key in ("start", "end")]
    if not all(math.isfinite(value) for point in points for value in point.values()):
        raise ValueError("FILLET_CHAMFER_NON_FINITE_GEOMETRY")
    if max(p["z"] for p in points) - min(p["z"] for p in points) > tolerance:
        raise ValueError("FILLET_CHAMFER_REQUIRES_XY_PLANE")
    if any(float(line.get("thickness", 0)) != 0 for line in (first, second)):
        raise ValueError("FILLET_CHAMFER_THICKNESS_UNSUPPORTED")
    corner_data = _shared_corner(first, second, tolerance)
    if corner_data is None:
        raise ValueError("FILLET_CHAMFER_REQUIRES_CONNECTED_LINES")
    corner, first_other, second_other = corner_data
    keys = ["end" if _distance(_line_point(line, "end"), corner) <= tolerance else "start" for line in (first, second)]
    a, b = (_line_point(line, key) for line, key in zip((first, second), keys))
    directions = [(other["x"] - p["x"], other["y"] - p["y"]) for p, other in ((a, first_other), (b, second_other))]
    lengths = [math.hypot(*v) for v in directions]
    if min(lengths) <= tolerance:
        raise ValueError("FILLET_CHAMFER_ZERO_LENGTH_LINE")
    u, v = [(x / length, y / length) for (x, y), length in zip(directions, lengths)]
    cross = u[0] * v[1] - u[1] * v[0]
    if abs(cross) <= 1e-12:
        raise ValueError("FILLET_CHAMFER_LINES_COLLINEAR")
    # Snapped endpoints can differ slightly. Intersect their supporting lines
    # instead of bending both lines through the first endpoint.
    offset = ((b["x"] - a["x"]) * v[1] - (b["y"] - a["y"]) * v[0]) / cross
    corner = {"x": a["x"] + offset * u[0], "y": a["y"] + offset * u[1], "z": a["z"]}
    if max(_distance(corner, a), _distance(corner, b)) > tolerance:
        raise ValueError("FILLET_CHAMFER_REQUIRES_CONNECTED_LINES")
    return corner, first_other, second_other, keys


def _fillet_geometry(first: dict[str, Any], second: dict[str, Any], radius: float, tolerance: float) -> tuple[dict[str, float], dict[str, float], dict[str, float], float, float]:
    corner, first_other, second_other, _ = _corner_geometry(first, second, tolerance)
    u1 = {"x": first_other["x"] - corner["x"], "y": first_other["y"] - corner["y"], "z": first_other["z"] - corner["z"]}
    u2 = {"x": second_other["x"] - corner["x"], "y": second_other["y"] - corner["y"], "z": second_other["z"] - corner["z"]}
    l1, l2 = math.hypot(u1["x"], u1["y"]), math.hypot(u2["x"], u2["y"])
    if l1 <= tolerance or l2 <= tolerance or radius <= 0:
        raise ValueError("INVALID_FILLET_GEOMETRY")
    u1["x"], u1["y"] = u1["x"] / l1, u1["y"] / l1
    u2["x"], u2["y"] = u2["x"] / l2, u2["y"] / l2
    dot = u1["x"] * u2["x"] + u1["y"] * u2["y"]
    theta = math.atan2(abs(u1["x"] * u2["y"] - u1["y"] * u2["x"]), dot)
    tangent_distance = radius / math.tan(theta / 2)
    if tangent_distance >= min(l1, l2) - tolerance:
        raise ValueError("FILLET_RADIUS_TOO_LARGE")
    tangent1 = {"x": corner["x"] + u1["x"] * tangent_distance, "y": corner["y"] + u1["y"] * tangent_distance, "z": corner["z"]}
    tangent2 = {"x": corner["x"] + u2["x"] * tangent_distance, "y": corner["y"] + u2["y"] * tangent_distance, "z": corner["z"]}
    bisector = {"x": u1["x"] + u2["x"], "y": u1["y"] + u2["y"]}
    bisector_length = math.hypot(bisector["x"], bisector["y"])
    if bisector_length <= 1e-12:
        raise ValueError("FILLET_LINES_OPPOSITE")
    center_distance = radius / math.sin(theta / 2)
    center = {"x": corner["x"] + bisector["x"] / bisector_length * center_distance, "y": corner["y"] + bisector["y"] / bisector_length * center_distance, "z": corner["z"]}
    start_angle = math.degrees(math.atan2(tangent1["y"] - center["y"], tangent1["x"] - center["x"])) % 360
    end_angle = math.degrees(math.atan2(tangent2["y"] - center["y"], tangent2["x"] - center["x"])) % 360
    # DXF ARC always runs counter-clockwise around +Z. Selection order must
    # not turn the interior fillet into the complementary major arc.
    if (end_angle - start_angle) % 360 > 180:
        start_angle, end_angle = end_angle, start_angle
    return tangent1, tangent2, center, start_angle, end_angle


def _positive_corner_number(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(code)
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(code) from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(code)
    return number


def suggest_repairs(document: CadDocument, validation: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn deterministic validator errors into a small, reviewable repair plan."""
    repairs: list[dict[str, Any]] = []
    for error in validation.get("errors", []):
        code, entity_id = error.get("code"), error.get("entity_id")
        if code == "ZERO_LENGTH_LINE" and entity_id:
            repairs.append({"operation": "cad.entity_delete", "entity_id": entity_id})
        elif code == "INVALID_RADIUS" and entity_id:
            repairs.append({"operation": "cad.entity_update", "entity_id": entity_id, "patch": {"radius": 1e-6}})
        elif code == "POLYLINE_TOO_SHORT" and entity_id:
            repairs.append({"operation": "cad.entity_delete", "entity_id": entity_id})
        elif code == "CLOSED_POLYLINE_TOO_SHORT" and entity_id:
            repairs.append({"operation": "cad.entity_update", "entity_id": entity_id, "patch": {"closed": False}})
        elif code == "HATCH_BOUNDARY_TOO_SHORT" and entity_id:
            repairs.append({"operation": "cad.entity_delete", "entity_id": entity_id})
        elif code == "MISSING_LAYER" and error.get("layer"):
            repairs.append({"operation": "cad.layer_create", "name": error["layer"]})
        elif code == "INSERT_WITHOUT_BLOCK" and entity_id:
            repairs.append({"operation": "cad.entity_delete", "entity_id": entity_id})
        elif code == "LEADER_TOO_SHORT" and entity_id:
            repairs.append({"operation": "cad.entity_delete", "entity_id": entity_id})
        elif code == "INVALID_ELLIPSE_RATIO" and entity_id:
            repairs.append({"operation": "cad.entity_update", "entity_id": entity_id, "patch": {"ratio": 0.5}})
    return repairs


class CadOperationEngine:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def open(self, input_file: str | Path) -> CadDocument:
        return CadDocument.open(input_file)

    def _apply_one(self, document: CadDocument, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        name = operation.removeprefix("cad.")
        if name == "standard_catalog":
            from .standards import catalog
            return catalog(arguments.get("standard"))
        if name == "standard_library":
            from .standards import standard_library
            return standard_library(arguments.get("category"), arguments.get("query"))
        if name in {"document_open", "document_inspect"}:
            return {"document": document.to_dict()}
        convenience = {
            "line_create": ("LINE", {"start": _point(arguments.get("start")), "end": _point(arguments.get("end"))}),
            "circle_create": ("CIRCLE", {"center": _point(arguments.get("center")), "radius": arguments.get("radius")}),
            "arc_create": ("ARC", {"center": _point(arguments.get("center")), "radius": arguments.get("radius"), "start_angle": arguments.get("start_angle", 0), "end_angle": arguments.get("end_angle", 360)}),
            "polyline_create": ("LWPOLYLINE", {"vertices": [_point(item) for item in arguments.get("vertices", [])], "closed": bool(arguments.get("closed", False))}),
            "spline_create": ("SPLINE", {"fit_points": [_point(item) for item in arguments.get("fit_points", arguments.get("points", []))], "degree": int(arguments.get("degree", 3))}),
            "point_create": ("POINT", {"point": arguments.get("point")}),
            "text_create": ("TEXT", {"insert": _point(arguments.get("insert")), "text": arguments.get("text", ""), "height": arguments.get("height", 1), "rotation": arguments.get("rotation", 0)}),
            "insert_create": ("INSERT", {"block_name": arguments.get("block_name", ""), "insert": _point(arguments.get("insert")), "scale": arguments.get("scale", {"x": 1, "y": 1, "z": 1}), "rotation": arguments.get("rotation", 0)}),
        }
        if name in convenience:
            entity_type, values = convenience[name]
            entity = values | {"id": document.next_id(), "type": entity_type, "layer": str(arguments.get("layer", "0"))}
            document.ensure_layer(entity["layer"])
            document.entities.append(entity)
            return {"entity": entity}
        if name == "entity_create":
            entity = copy.deepcopy(arguments.get("entity") or {})
            entity.setdefault("type", str(arguments.get("entity_type", "POINT")).upper())
            entity.setdefault("layer", str(arguments.get("layer", "0")))
            entity["id"] = str(entity.get("id") or document.next_id())
            document.ensure_layer(entity["layer"])
            document.entities.append(entity)
            return {"entity": entity}
        if name == "entity_update":
            entity = document.entity(str(arguments.get("entity_id")))
            patch = arguments.get("patch") or {}
            updated = _merge(entity, patch)
            updated["id"] = entity["id"]
            index = document.entities.index(entity)
            document.entities[index] = updated
            document.ensure_layer(str(updated.get("layer", "0")))
            return {"entity": updated}
        if name == "entity_delete":
            ids = set(_refs(arguments, document))
            if not ids:
                raise ValueError("ENTITY_SELECTION_REQUIRED")
            before = len(document.entities)
            document.entities = [item for item in document.entities if str(item.get("id")) not in ids]
            if len(document.entities) == before:
                raise ValueError("ENTITY_NOT_FOUND:" + ",".join(sorted(ids)))
            for selection in document.selection_sets.values():
                selection.refs = [ref for ref in selection.refs if ref not in ids]
            return {"deleted": sorted(ids)}
        if name in {"entity_move", "entity_transform", "entity_rotate", "entity_scale", "entity_mirror", "entity_copy"}:
            ids = _refs(arguments, document)
            if not ids:
                raise ValueError("ENTITY_SELECTION_REQUIRED")
            preserve_associations = name != "entity_copy" and bool(arguments.get("preserve_associations", True))
            if preserve_associations:
                ids = _expand_associative_refs(document, ids)
            angle = float(arguments.get("angle", arguments.get("rotate_deg", 0))) if name in {"entity_transform", "entity_rotate"} else 0.0
            scale = float(arguments.get("scale", 1.0)) if name in {"entity_transform", "entity_scale"} else 1.0
            if name in {"entity_move", "entity_copy"}:
                dx, dy, dz = (list(arguments.get("delta") or [arguments.get("dx", 0), arguments.get("dy", 0), arguments.get("dz", 0)]) + [0, 0, 0])[:3]
            else:
                dx, dy, dz = (list(arguments.get("translate") or [0, 0, 0]) + [0, 0, 0])[:3]
            mirror_axis = str(arguments.get("axis", "")) if name == "entity_mirror" else None
            center = _point(arguments.get("center") or arguments.get("base_point"))
            changed = []
            allow_associative = preserve_associations and any(
                str(document.entity(entity_id).get("type", "")).upper() == "HATCH"
                and document.entity(entity_id).get("associative") for entity_id in ids
            )
            for entity_id in ids:
                original = document.entity(entity_id)
                transformed = transform_entity(original, dx=float(dx), dy=float(dy), dz=float(dz), scale=scale,
                                                angle=angle, center=center, mirror_axis=mirror_axis,
                                                allow_associative=allow_associative)
                if name == "entity_copy":
                    transformed["id"] = document.next_id("copy")
                    document.entities.append(transformed)
                    changed.append(transformed["id"])
                else:
                    document.entities[document.entities.index(original)] = transformed
                    changed.append(entity_id)
            return {"entities": changed}
        if name in {"entity_array_rectangular", "entity_array_polar"}:
            ids = _refs(arguments, document)
            if not ids:
                raise ValueError("ENTITY_SELECTION_REQUIRED")
            originals = [copy.deepcopy(document.entity(entity_id)) for entity_id in ids]
            created: list[str] = []
            if name == "entity_array_rectangular":
                rows, columns = int(arguments.get("rows", 1)), int(arguments.get("columns", 1))
                if rows < 1 or columns < 1 or rows * columns > 10000:
                    raise ValueError("INVALID_ARRAY_SIZE")
                row_spacing = float(arguments.get("row_spacing", arguments.get("dy", 0)))
                column_spacing = float(arguments.get("column_spacing", arguments.get("dx", 0)))
                for row in range(rows):
                    for column in range(columns):
                        if row == 0 and column == 0 and bool(arguments.get("include_original", True)):
                            continue
                        for original in originals:
                            copied = _copy_with_transform(document, original, dx=column * column_spacing, dy=row * row_spacing)
                            document.entities.append(copied)
                            created.append(copied["id"])
            else:
                count = int(arguments.get("count", 1))
                if count < 1 or count > 10000:
                    raise ValueError("INVALID_ARRAY_SIZE")
                center = _point(arguments.get("center"))
                step = float(arguments.get("angle_step", arguments.get("total_angle", 360.0) / count))
                start_angle = float(arguments.get("start_angle", 0.0))
                for index in range(count):
                    if index == 0 and bool(arguments.get("include_original", True)):
                        continue
                    for original in originals:
                        copied = _copy_with_transform(document, original, angle=start_angle + index * step, center=center)
                        document.entities.append(copied)
                        created.append(copied["id"])
            return {"entities": created, "count": len(created)}
        if name == "entity_break":
            entity = document.entity(str(arguments.get("entity_id")))
            if str(entity.get("type", "")).upper() != "LINE":
                raise ValueError("BREAK_ONLY_SUPPORTS_LINE")
            start, end = _line_point(entity, "start"), _line_point(entity, "end")
            point = _point(arguments.get("point"))
            if _distance(start, point) <= 1e-9 or _distance(end, point) <= 1e-9:
                raise ValueError("BREAK_POINT_AT_ENDPOINT")
            total = _distance(start, end)
            if total <= 1e-12 or abs(_distance(start, point) + _distance(point, end) - total) > max(1e-6, total * 1e-5):
                raise ValueError("BREAK_POINT_OFF_LINE")
            first = copy.deepcopy(entity)
            first["end"] = point
            second = copy.deepcopy(entity)
            second["id"] = document.next_id("break")
            second["start"] = point
            index = document.entities.index(entity)
            document.entities[index] = first
            document.entities.insert(index + 1, second)
            return {"entities": [first["id"], second["id"]]}
        if name == "entity_join":
            ids = _refs(arguments, document)
            if len(ids) < 2:
                raise ValueError("JOIN_REQUIRES_TWO_LINES")
            lines = [document.entity(entity_id) for entity_id in ids]
            if any(str(item.get("type", "")).upper() != "LINE" for item in lines):
                raise ValueError("JOIN_ONLY_SUPPORTS_LINES")
            vertices = _chain_lines(lines, float(arguments.get("tolerance", 1e-6)))
            joined = {"id": lines[0]["id"], "type": "LWPOLYLINE", "layer": lines[0].get("layer", "0"), "vertices": vertices, "closed": _distance(vertices[0], vertices[-1]) <= float(arguments.get("tolerance", 1e-6))}
            document.entities = [item for item in document.entities if str(item.get("id")) not in set(ids)]
            document.entities.append(joined)
            for constraint in document.constraints:
                constraint.refs = [joined["id"] if ref in set(ids) else ref for ref in constraint.refs]
            for selection in document.selection_sets.values():
                selection.refs = [joined["id"] if ref in set(ids) else ref for ref in selection.refs]
            return {"entity": joined, "deleted": ids[1:]}
        if name == "entity_purge":
            from ezdxf.sections.blocks import is_special_block
            used_blocks = {str(item.get("block_name")) for item in document.entities if item.get("block_name")}
            used_blocks.update(key for key in document.blocks if is_special_block(key))
            pending = list(used_blocks)
            while pending:
                block = document.blocks.get(pending.pop())
                if block:
                    for item in block.entities:
                        child = item.get("block_name")
                        if child and child not in used_blocks:
                            used_blocks.add(child); pending.append(child)
            used_layers = {str(item.get("layer", "0")) for item in document.entities}
            used_layers.update(str(item.get("layer", "0")) for key, block in document.blocks.items() if key in used_blocks for item in block.entities)
            removed_layers = [key for key in list(document.layers) if key not in used_layers and key not in {"0", "default", "Defpoints"}]
            removed_blocks = [key for key in list(document.blocks) if key not in used_blocks]
            for key in removed_layers:
                del document.layers[key]
            for key in removed_blocks:
                del document.blocks[key]
            for name_key, selection in list(document.selection_sets.items()):
                selection.refs = [ref for ref in selection.refs if any(str(item.get("id")) == ref for item in document.entities)]
                if not selection.refs and bool(arguments.get("remove_empty_selections", True)):
                    del document.selection_sets[name_key]
            valid_ids = {str(item.get("id")) for item in document.entities}
            before_constraints = len(document.constraints)
            document.constraints = [constraint for constraint in document.constraints if all(ref in valid_ids for ref in constraint.refs)]
            return {"removed_layers": removed_layers, "removed_blocks": removed_blocks, "removed_constraints": before_constraints - len(document.constraints)}
        if name == "entity_reverse":
            ids = _refs(arguments, document)
            if not ids:
                raise ValueError("ENTITY_SELECTION_REQUIRED")
            changed = []
            for entity_id in ids:
                entity = document.entity(entity_id)
                kind = str(entity.get("type", "")).upper()
                updated = copy.deepcopy(entity)
                if kind == "LINE":
                    updated["start"], updated["end"] = updated.get("end", {}), updated.get("start", {})
                elif kind in {"LWPOLYLINE", "POLYLINE", "LEADER"}:
                    updated["vertices"] = list(reversed(updated.get("vertices", [])))
                elif kind == "ARC":
                    updated["start_angle"], updated["end_angle"] = updated.get("end_angle", 360), updated.get("start_angle", 0)
                elif kind == "SPLINE":
                    for key in ("fit_points", "control_points", "weights"):
                        if key in updated:
                            updated[key] = list(reversed(updated.get(key) or []))
                    knots = [float(value) for value in updated.get("knots", [])]
                    if knots:
                        low, high = knots[0], knots[-1]
                        updated["knots"] = [low + high - value for value in reversed(knots)]
                else:
                    if arguments.get("all_entities"):
                        continue
                    raise ValueError("REVERSE_UNSUPPORTED_ENTITY")
                document.entities[document.entities.index(entity)] = updated
                changed.append(entity_id)
            return {"entities": changed}
        if name == "entity_explode":
            entity = document.entity(str(arguments.get("entity_id")))
            if str(entity.get("type", "")).upper() != "INSERT":
                raise ValueError("EXPLODE_ONLY_SUPPORTS_INSERT")
            block = document.blocks.get(str(entity.get("block_name", "")))
            if not block:
                raise ValueError("BLOCK_NOT_FOUND:" + str(entity.get("block_name", "")))
            insert, scale, rotation = _point(entity.get("insert")), entity.get("scale", {}), float(entity.get("rotation", 0))
            created = []
            for original in block.entities:
                transformed = transform_entity(original, dx=insert["x"], dy=insert["y"], dz=insert["z"], scale=float(scale.get("x", 1)), angle=rotation, center=block.base_point)
                transformed["id"] = document.next_id("explode")
                transformed["layer"] = entity.get("layer", transformed.get("layer", "0"))
                document.entities.append(transformed)
                created.append(transformed["id"])
            document.entities.remove(entity)
            return {"entities": created, "deleted": [str(entity.get("id"))]}
        if name in {"coordinate_system_create", "coordinate_system_delete"}:
            if name == "coordinate_system_create":
                system = copy.deepcopy(arguments.get("coordinate_system") or {})
                system["name"] = str(system.get("name") or arguments.get("name") or "UCS")
                if any(str(item.get("name")) == system["name"] for item in document.coordinate_systems):
                    raise ValueError("COORDINATE_SYSTEM_EXISTS:" + system["name"])
                system.setdefault("origin", _point(arguments.get("origin")))
                system.setdefault("x_axis", _point(arguments.get("x_axis", {"x": 1, "y": 0, "z": 0})))
                system.setdefault("y_axis", _point(arguments.get("y_axis", {"x": 0, "y": 1, "z": 0})))
                document.coordinate_systems.append(system)
                return {"coordinate_system": system}
            name_value = str(arguments.get("name", ""))
            before = len(document.coordinate_systems)
            document.coordinate_systems = [item for item in document.coordinate_systems if str(item.get("name")) not in {name_value, "WCS"}]
            if len(document.coordinate_systems) == before:
                raise ValueError("COORDINATE_SYSTEM_NOT_FOUND:" + name_value)
            return {"deleted": name_value}
        if name == "sheet_prepare":
            from .drawing_sheets import prepare_sheet
            return prepare_sheet(document, arguments)
        if name in {"sheet_create", "sheet_update", "sheet_delete"}:
            sheet_name = str(arguments.get("name", ""))
            if not sheet_name:
                raise ValueError("SHEET_NAME_REQUIRED")
            if name == "sheet_create":
                if sheet_name in document.sheets:
                    raise ValueError("SHEET_EXISTS:" + sheet_name)
                sheet = {"name": sheet_name, "width": float(arguments.get("width", 420)), "height": float(arguments.get("height", 297)), "units": str(arguments.get("units", document.units)), "layout": str(arguments.get("layout", "landscape")), "title_block": None}
                document.sheets[sheet_name] = sheet
                return {"sheet": sheet}
            if sheet_name not in document.sheets:
                raise ValueError("SHEET_NOT_FOUND:" + sheet_name)
            if name == "sheet_update":
                patch = arguments.get("patch") or {}
                sheet = document.sheets[sheet_name]
                for key in ("width", "height", "units", "layout"):
                    if key in patch:
                        sheet[key] = float(patch[key]) if key in {"width", "height"} else str(patch[key])
                return {"sheet": copy.deepcopy(sheet)}
            removed_viewports = [key for key, viewport in document.viewports.items() if str(viewport.get("sheet")) == sheet_name]
            for key in removed_viewports:
                del document.viewports[key]
            for entity in document.entities:
                if entity.get("sheet") == sheet_name:
                    entity["_deleted_by_sheet"] = True
            document.entities = [entity for entity in document.entities if entity.get("sheet") != sheet_name]
            del document.sheets[sheet_name]
            return {"deleted": sheet_name, "viewports": removed_viewports}
        if name in {"viewport_create", "viewport_update", "viewport_delete"}:
            viewport_name = str(arguments.get("name", ""))
            if not viewport_name:
                raise ValueError("VIEWPORT_NAME_REQUIRED")
            if name == "viewport_create":
                sheet_name = str(arguments.get("sheet", ""))
                if sheet_name not in document.sheets:
                    raise ValueError("SHEET_NOT_FOUND:" + sheet_name)
                if viewport_name in document.viewports:
                    raise ValueError("VIEWPORT_EXISTS:" + viewport_name)
                viewport = {"name": viewport_name, "sheet": sheet_name, "center": _point(arguments.get("center")), "width": float(arguments.get("width", document.sheets[sheet_name]["width"])), "height": float(arguments.get("height", document.sheets[sheet_name]["height"])), "scale": float(arguments.get("scale", 1.0)), "locked": bool(arguments.get("locked", False))}
                document.viewports[viewport_name] = viewport
                return {"viewport": viewport}
            if viewport_name not in document.viewports:
                raise ValueError("VIEWPORT_NOT_FOUND:" + viewport_name)
            if name == "viewport_update":
                patch = arguments.get("patch") or {}
                viewport = document.viewports[viewport_name]
                for key in ("width", "height", "scale"):
                    if key in patch:
                        viewport[key] = float(patch[key])
                for key in ("sheet", "locked"):
                    if key in patch:
                        viewport[key] = bool(patch[key]) if key == "locked" else str(patch[key])
                if viewport["sheet"] not in document.sheets:
                    raise ValueError("SHEET_NOT_FOUND:" + str(viewport["sheet"]))
                if "center" in patch:
                    viewport["center"] = _point(patch["center"])
                return {"viewport": copy.deepcopy(viewport)}
            del document.viewports[viewport_name]
            return {"deleted": viewport_name}
        if name in {"title_block_create", "title_block_update"}:
            from .title_blocks import create_title, update_title
            return (create_title if name == "title_block_create" else update_title)(document, arguments)
        if name in {"standard_symbol_create", "fastener_create", "gdt_frame_create", "surface_texture_create", "pid_symbol_create", "process_line_create", "process_route_create", "pid_valve_cut_pipe", "bom_create", "bom_balloon_create"}:
            layer = str(arguments.get("layer", "SYMBOLS" if name in {"standard_symbol_create", "fastener_create", "pid_symbol_create"} else "ANNOTATION"))
            document.ensure_layer(layer)
            created: list[str] = []
            if name == "process_line_create":
                start, end = _point(arguments.get("start")), _point(arguments.get("end"))
                entity = {"id": document.next_id("process"), "type": "LINE", "layer": layer, "start": start, "end": end, "process_line": {"service": str(arguments.get("service", "")), "line_type": str(arguments.get("line_type", "CONTINUOUS"))}}
                document.entities.append(entity); created.append(entity["id"])
            elif name == "process_route_create":
                start, end = _point(arguments.get("start")), _point(arguments.get("end"))
                waypoints = [_point(value) for value in arguments.get("waypoints", [])]
                if not waypoints and abs(start["x"] - end["x"]) > 1e-9 and abs(start["y"] - end["y"]) > 1e-9:
                    corner = str(arguments.get("corner", "x_then_y")).lower()
                    waypoints = [{"x": end["x"], "y": start["y"], "z": start["z"]}] if corner == "x_then_y" else [{"x": start["x"], "y": end["y"], "z": start["z"]}]
                points = [start, *waypoints, end]
                compact = [points[0]]
                for point in points[1:]:
                    if _distance(point, compact[-1]) > 1e-9:
                        compact.append(point)
                if len(compact) < 2:
                    raise ValueError("PROCESS_ROUTE_TOO_SHORT")
                if not arguments.get("allow_diagonal", False):
                    for first, second in zip(compact, compact[1:]):
                        if abs(first["x"] - second["x"]) > 1e-9 and abs(first["y"] - second["y"]) > 1e-9:
                            raise ValueError("PROCESS_ROUTE_NOT_ORTHOGONAL")
                entity = {"id": document.next_id("process"), "type": "LWPOLYLINE", "layer": layer,
                          "vertices": compact, "closed": False,
                          "process_line": {"service": str(arguments.get("service", "")), "line_type": str(arguments.get("line_type", "CONTINUOUS"))},
                          "process_route": {"orthogonal": not bool(arguments.get("allow_diagonal", False)), "corner": str(arguments.get("corner", "x_then_y"))}}
                document.entities.append(entity); created.append(entity["id"])
            elif name == "pid_valve_cut_pipe":
                source = document.entity(str(arguments.get("entity_id")))
                source_kind = str(source.get("type", "")).upper()
                if source_kind not in {"LINE", "LWPOLYLINE", "POLYLINE"} or not source.get("process_line"):
                    raise ValueError("PID_VALVE_REQUIRES_PROCESS_LINE")
                route = ([_point(source.get("start")), _point(source.get("end"))]
                         if source_kind == "LINE" else [_point(value) for value in source.get("vertices", [])])
                if len(route) < 2:
                    raise ValueError("PID_VALVE_ROUTE_TOO_SHORT")
                segments = []
                for index, (first, second) in enumerate(zip(route, route[1:])):
                    dx, dy, dz = second["x"] - first["x"], second["y"] - first["y"], second["z"] - first["z"]
                    length = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if length > 1e-9:
                        segments.append((index, first, second, length))
                if not segments:
                    raise ValueError("PID_VALVE_ROUTE_TOO_SHORT")
                radius = float(arguments.get("radius", 3.0))
                if radius <= 0:
                    raise ValueError("PID_VALVE_RADIUS_INVALID")
                gap = float(arguments.get("gap", radius * 3.0))
                if gap < radius * 2.0:
                    raise ValueError("PID_VALVE_GAP_TOO_SMALL")
                target = arguments.get("point")
                selected = None
                if target is not None:
                    target = _point(target)
                    best = None
                    for index, first, second, length in segments:
                        dx, dy, dz = second["x"] - first["x"], second["y"] - first["y"], second["z"] - first["z"]
                        t = max(0.0, min(1.0, ((target["x"] - first["x"]) * dx + (target["y"] - first["y"]) * dy + (target["z"] - first["z"]) * dz) / (length * length)))
                        projection = {"x": first["x"] + t * dx, "y": first["y"] + t * dy, "z": first["z"] + t * dz}
                        distance = _distance(target, projection)
                        candidate = (distance, index, first, second, length, t, projection)
                        if best is None or candidate[0] < best[0]:
                            best = candidate
                    assert best is not None
                    tolerance = float(arguments.get("tolerance", 1e-6))
                    if best[0] > max(tolerance, gap * 1e-3):
                        raise ValueError("PID_VALVE_POINT_OFF_ROUTE")
                    selected = best
                else:
                    fraction = float(arguments.get("fraction", 0.5))
                    if not 0.0 < fraction < 1.0:
                        raise ValueError("PID_VALVE_FRACTION_INVALID")
                    total = sum(item[3] for item in segments)
                    station = float(arguments.get("distance", total * fraction))
                    if not 0.0 < station < total:
                        raise ValueError("PID_VALVE_DISTANCE_INVALID")
                    walked = 0.0
                    for index, first, second, length in segments:
                        if station <= walked + length:
                            t = (station - walked) / length
                            projection = {"x": first["x"] + t * (second["x"] - first["x"]), "y": first["y"] + t * (second["y"] - first["y"]), "z": first["z"] + t * (second["z"] - first["z"])}
                            selected = (0.0, index, first, second, length, t, projection)
                            break
                        walked += length
                assert selected is not None
                _distance_to_point, segment_index, first, second, length, t, center = selected
                half_gap = gap / 2.0
                margin = min(t * length, (1.0 - t) * length)
                if half_gap >= margin - 1e-9:
                    raise ValueError("PID_VALVE_GAP_TOO_LARGE")
                ux, uy, uz = (second["x"] - first["x"]) / length, (second["y"] - first["y"]) / length, (second["z"] - first["z"]) / length
                cut_start = {"x": center["x"] - ux * half_gap, "y": center["y"] - uy * half_gap, "z": center["z"] - uz * half_gap}
                cut_end = {"x": center["x"] + ux * half_gap, "y": center["y"] + uy * half_gap, "z": center["z"] + uz * half_gap}
                first_route = route[:segment_index + 1] + [cut_start]
                second_route = [cut_end] + route[segment_index + 1:]
                if source_kind == "LINE":
                    first_entity = copy.deepcopy(source)
                    first_entity["end"] = cut_start
                    second_entity = copy.deepcopy(source)
                    second_entity["id"] = document.next_id("process")
                    second_entity["start"] = cut_end
                else:
                    first_entity = copy.deepcopy(source)
                    first_entity["vertices"] = first_route
                    second_entity = copy.deepcopy(source)
                    second_entity["id"] = document.next_id("process")
                    second_entity["vertices"] = second_route
                source_index = document.entities.index(source)
                document.entities[source_index] = first_entity
                document.entities.insert(source_index + 1, second_entity)
                # Use distinct prefixes while the new entities are still being
                # assembled; ``next_id`` intentionally derives candidates
                # from the current document length, so separate prefixes keep
                # all six IDs unique before insertion.
                valve_id = document.next_id("pid")
                label_id = document.next_id("pid-label")
                inlet_id = document.next_id("process-in")
                outlet_id = document.next_id("process-out")
                service = str(source.get("process_line", {}).get("service", ""))
                line_type = str(source.get("process_line", {}).get("line_type", "CONTINUOUS"))
                process_tag = {"service": service, "line_type": line_type}
                for fragment in (first_entity, second_entity):
                    fragment["process_line"] = copy.deepcopy(process_tag)
                    fragment["process_route"] = _merge(fragment.get("process_route") or {}, {"split_by_valve": valve_id, "source_process_line_id": str(source.get("id"))})
                document.entities.insert(source_index + 2, {"id": inlet_id, "type": "LINE", "layer": source.get("layer", layer), "start": cut_start, "end": center, "process_line": copy.deepcopy(process_tag), "pid_connector": {"valve_id": valve_id, "port": "inlet", "source_process_line_id": str(source.get("id"))}})
                document.entities.insert(source_index + 3, {"id": outlet_id, "type": "LINE", "layer": source.get("layer", layer), "start": center, "end": cut_end, "process_line": copy.deepcopy(process_tag), "pid_connector": {"valve_id": valve_id, "port": "outlet", "source_process_line_id": str(source.get("id"))}})
                symbol = str(arguments.get("symbol", "VALVE"))
                if arguments.get("strict_standard", False):
                    from .standards import validate_pid_symbol
                    validate_pid_symbol(symbol)
                ports = [
                    {"name": "inlet", "direction": "in", "point": cut_start, "vector": {"x": ux, "y": uy, "z": uz}},
                    {"name": "outlet", "direction": "out", "point": cut_end, "vector": {"x": ux, "y": uy, "z": uz}},
                ]
                document.entities.insert(source_index + 4, {"id": valve_id, "type": "CIRCLE", "layer": str(arguments.get("symbol_layer", layer)), "center": center, "radius": radius, "pid_symbol": symbol, "pid_ports": ports,
                                                           "pid_valve": {"symbol": symbol, "service": service, "source_process_line_id": str(source.get("id")), "inlet_connector_id": inlet_id, "outlet_connector_id": outlet_id, "ports": ports}})
                document.entities.insert(source_index + 5, {"id": label_id, "type": "MTEXT", "layer": str(arguments.get("symbol_layer", layer)), "insert": {"x": center["x"] + radius + 2, "y": center["y"], "z": center["z"]}, "text": str(arguments.get("label", symbol)), "height": float(arguments.get("text_height", 2.5)), "width": 0, "rotation": 0, "pid_symbol": symbol, "pid_valve_label": valve_id})
                created.extend([first_entity["id"], second_entity["id"], inlet_id, outlet_id, valve_id, label_id])
                return {"entities": created, "count": len(created), "valve_id": valve_id,
                        "connector_ids": [inlet_id, outlet_id], "split_line_ids": [first_entity["id"], second_entity["id"]],
                        "center": center, "gap": gap, "service": service}
            elif name == "pid_symbol_create":
                if arguments.get("strict_standard", False):
                    from .standards import validate_pid_symbol
                    validate_pid_symbol(str(arguments.get("symbol", "VALVE")))
                center = _point(arguments.get("center")); radius = float(arguments.get("radius", 5))
                symbol_name = str(arguments.get("symbol", "VALVE"))
                ports = []
                for port in arguments.get("ports", []) or []:
                    if not isinstance(port, dict) or not port.get("name"):
                        raise ValueError("PID_PORT_INVALID")
                    item = {"name": str(port["name"]), "direction": str(port.get("direction", "bidirectional")), "point": _point(port.get("point", center))}
                    if "vector" in port:
                        item["vector"] = _point(port["vector"])
                    ports.append(item)
                circle = {"id": document.next_id("pid"), "type": "CIRCLE", "layer": layer, "center": center, "radius": radius, "pid_symbol": symbol_name}
                if ports:
                    circle["pid_ports"] = ports
                document.entities.append(circle); created.append(circle["id"])
                label = {"id": document.next_id("pid"), "type": "MTEXT", "layer": layer, "insert": {"x": center["x"] + radius + 2, "y": center["y"], "z": center["z"]}, "text": str(arguments.get("label", symbol_name)), "height": float(arguments.get("text_height", 2.5)), "width": 0, "rotation": 0, "pid_symbol": circle["pid_symbol"]}
                document.entities.append(label); created.append(label["id"])
            elif name == "fastener_create":
                from .standards import validate_fastener, validate_fastener_size
                center = _point(arguments.get("center")); radius = float(arguments.get("radius", 4)); sides = int(arguments.get("sides", 6))
                if sides < 3 or sides > 32 or radius <= 0:
                    raise ValueError("INVALID_FASTENER_GEOMETRY")
                if arguments.get("strict_standard", False):
                    validate_fastener(str(arguments.get("standard", "ISO")), str(arguments.get("kind", "bolt")))
                    validate_fastener_size(str(arguments.get("size", "M8")))
                vertices = [{"x": center["x"] + radius * math.cos(2 * math.pi * index / sides), "y": center["y"] + radius * math.sin(2 * math.pi * index / sides), "z": center["z"]} for index in range(sides)]
                entity = {"id": document.next_id("fastener"), "type": "LWPOLYLINE", "layer": layer, "vertices": vertices, "closed": True, "fastener": {"standard": str(arguments.get("standard", "ISO")), "size": str(arguments.get("size", "M8")), "kind": str(arguments.get("kind", "bolt"))}}
                document.entities.append(entity); created.append(entity["id"])
            elif name == "standard_symbol_create":
                from .standards import validate_symbol
                center = _point(arguments.get("center")); symbol = str(arguments.get("symbol", "WELD")); size = float(arguments.get("size", 5))
                if arguments.get("strict_standard", False):
                    validate_symbol(str(arguments.get("standard", "ISO")), symbol)
                entity = {"id": document.next_id("symbol"), "type": "MTEXT", "layer": layer, "insert": center, "text": str(arguments.get("label", symbol)), "height": size, "width": 0, "rotation": float(arguments.get("rotation", 0)), "standard_symbol": {"name": symbol, "standard": str(arguments.get("standard", "ISO"))}}
                document.entities.append(entity); created.append(entity["id"])
            elif name in {"gdt_frame_create", "surface_texture_create"}:
                insert = _point(arguments.get("insert"))
                if name == "gdt_frame_create":
                    if arguments.get("strict_standard", False):
                        from .standards import validate_gdt_characteristic
                        for characteristic in arguments.get("characteristics", []):
                            validate_gdt_characteristic(characteristic)
                    text = " | ".join(str(value) for value in arguments.get("frames", arguments.get("text", "⌀0.1")) if isinstance(arguments.get("frames", arguments.get("text", "⌀0.1")), list)) or str(arguments.get("text", "⌀0.1"))
                    marker = {"gdt": {"datum": arguments.get("datum"), "frames": arguments.get("frames", [text])}}
                else:
                    text = str(arguments.get("texture", "⌯ Ra 3.2")); marker = {"surface_texture": {"standard": str(arguments.get("standard", "ISO")), "value": text}}
                entity = {"id": document.next_id("annotation"), "type": "MTEXT", "layer": layer, "insert": insert, "text": text, "height": float(arguments.get("height", 2.5)), "width": float(arguments.get("width", 0)), "rotation": float(arguments.get("rotation", 0))} | marker
                document.entities.append(entity); created.append(entity["id"])
            elif name == "bom_balloon_create":
                center = _point(arguments.get("center")); radius = float(arguments.get("radius", 4)); label = str(arguments.get("label", "1"))
                circle = {"id": document.next_id("balloon"), "type": "CIRCLE", "layer": layer, "center": center, "radius": radius, "bom_balloon": {"label": label}}
                document.entities.append(circle); created.append(circle["id"])
                text = {"id": document.next_id("balloon"), "type": "MTEXT", "layer": layer, "insert": {"x": center["x"] - radius / 2, "y": center["y"] - 1, "z": center["z"]}, "text": label, "height": float(arguments.get("text_height", 2.5)), "width": 0, "rotation": 0, "bom_balloon": {"label": label}}
                document.entities.append(text); created.append(text["id"])
            else:
                rows = arguments.get("rows") or []
                if not rows:
                    raise ValueError("BOM_ROWS_REQUIRED")
                origin = _point(arguments.get("origin")); cell_width, cell_height = float(arguments.get("cell_width", 24)), float(arguments.get("cell_height", 8))
                columns = max(len(row) if isinstance(row, list) else 1 for row in rows)
                for row_index, row in enumerate(rows):
                    values = row if isinstance(row, list) else [row]
                    for column in range(columns):
                        entity = {"id": document.next_id("bom"), "type": "MTEXT", "layer": layer, "insert": {"x": origin["x"] + column * cell_width, "y": origin["y"] - row_index * cell_height, "z": origin["z"]}, "text": str(values[column]) if column < len(values) else "", "height": float(arguments.get("text_height", 2.5)), "width": cell_width, "rotation": 0, "bom": {"row": row_index, "column": column}}
                        document.entities.append(entity); created.append(entity["id"])
            return {"entities": created, "count": len(created)}
        if name == "table_create":
            rows = arguments.get("rows") or []
            if not rows or not isinstance(rows, list):
                raise ValueError("TABLE_ROWS_REQUIRED")
            origin = _point(arguments.get("origin"))
            cell_width, cell_height = float(arguments.get("cell_width", 20)), float(arguments.get("cell_height", 8))
            columns = max(len(row) if isinstance(row, list) else 1 for row in rows)
            layer = str(arguments.get("layer", "ANNOTATION"))
            document.ensure_layer(layer)
            created = []
            for row_index, row in enumerate(rows):
                values = row if isinstance(row, list) else [row]
                for col_index in range(columns + 1):
                    x = origin["x"] + col_index * cell_width
                    line = {"id": document.next_id("table"), "type": "LINE", "layer": layer, "start": {"x": x, "y": origin["y"] - row_index * cell_height, "z": origin["z"]}, "end": {"x": x, "y": origin["y"] - (row_index + 1) * cell_height, "z": origin["z"]}}
                    document.entities.append(line); created.append(line["id"])
                y = origin["y"] - row_index * cell_height
                line = {"id": document.next_id("table"), "type": "LINE", "layer": layer, "start": {"x": origin["x"], "y": y, "z": origin["z"]}, "end": {"x": origin["x"] + columns * cell_width, "y": y, "z": origin["z"]}}
                document.entities.append(line); created.append(line["id"])
                for col_index in range(columns):
                    text = str(values[col_index]) if col_index < len(values) else ""
                    item = {"id": document.next_id("table"), "type": "MTEXT", "layer": layer, "insert": {"x": origin["x"] + col_index * cell_width + 1, "y": origin["y"] - (row_index + 0.7) * cell_height, "z": origin["z"]}, "text": text, "height": float(arguments.get("text_height", 2.5)), "width": cell_width - 2, "rotation": 0}
                    document.entities.append(item); created.append(item["id"])
            y = origin["y"] - len(rows) * cell_height
            line = {"id": document.next_id("table"), "type": "LINE", "layer": layer, "start": {"x": origin["x"], "y": y, "z": origin["z"]}, "end": {"x": origin["x"] + columns * cell_width, "y": y, "z": origin["z"]}}
            document.entities.append(line); created.append(line["id"])
            return {"entities": created, "rows": len(rows), "columns": columns}
        if name in {"leader_create", "mtext_create", "ellipse_create"}:
            layer = str(arguments.get("layer", "0"))
            document.ensure_layer(layer)
            if name == "leader_create":
                vertices = [_point(item) for item in arguments.get("vertices", arguments.get("points", []))]
                if len(vertices) < 2:
                    raise ValueError("LEADER_REQUIRES_TWO_POINTS")
                entity = {"id": document.next_id(), "type": "LEADER", "layer": layer, "vertices": vertices, "text": str(arguments.get("text", "")), "arrowhead": bool(arguments.get("arrowhead", True))}
            elif name == "mtext_create":
                entity = {"id": document.next_id(), "type": "MTEXT", "layer": layer, "insert": _point(arguments.get("insert")), "text": str(arguments.get("text", "")), "height": float(arguments.get("height", 1.0)), "width": float(arguments.get("width", 0.0)), "rotation": float(arguments.get("rotation", 0.0))}
            else:
                entity = {"id": document.next_id(), "type": "ELLIPSE", "layer": layer, "center": _point(arguments.get("center")), "major_axis": _point(arguments.get("major_axis", {"x": 1, "y": 0, "z": 0})), "ratio": float(arguments.get("ratio", 1.0)), "start_param": float(arguments.get("start_param", 0.0)), "end_param": float(arguments.get("end_param", 2 * math.pi))}
            document.entities.append(entity)
            return {"entity": entity}
        if name == "entity_trim":
            entity = document.entity(str(arguments.get("entity_id")))
            keep = str(arguments.get("keep", "end")).lower()
            boundary_id = arguments.get("boundary_entity_id") or arguments.get("cutting_entity_id")
            kind = str(entity.get("type", "")).upper()
            if kind == "LINE":
                if boundary_id:
                    boundary = document.entity(str(boundary_id))
                    boundary_kind = str(boundary.get("type", "")).upper()
                    if boundary_kind == "LINE":
                        point = _line_intersection(entity, boundary)
                        if point is None:
                            raise ValueError("TRIM_LINES_PARALLEL")
                    elif boundary_kind in {"CIRCLE", "ARC"}:
                        intersections = _line_circle_intersections(entity, boundary)
                        if boundary_kind == "ARC":
                            intersections = _filter_arc_intersections(intersections, boundary)
                        if not intersections:
                            raise ValueError("TRIM_NO_INTERSECTION")
                        anchor = _line_point(entity, "start" if keep == "start" else "end")
                        point = min(intersections, key=lambda candidate: _distance(anchor, candidate))
                    elif boundary_kind in {"LWPOLYLINE", "POLYLINE"}:
                        intersections = _line_polyline_intersections(entity, boundary)
                        if not intersections:
                            raise ValueError("TRIM_NO_INTERSECTION")
                        anchor = _line_point(entity, "start" if keep == "start" else "end")
                        point = min(intersections, key=lambda candidate: _distance(anchor, candidate))
                    else:
                        raise ValueError("TRIM_BOUNDARY_UNSUPPORTED")
                else:
                    point = _project_to_line(_point(arguments.get("point")), entity)
                updated = copy.deepcopy(entity)
                # ``keep`` names the endpoint that remains in the document;
                # the opposite endpoint is moved to the cutting point.
                updated["end" if keep == "start" else "start"] = point
            elif kind == "ARC":
                if not boundary_id:
                    raise ValueError("TRIM_ARC_REQUIRES_BOUNDARY")
                boundary = document.entity(str(boundary_id))
                boundary_kind = str(boundary.get("type", "")).upper()
                if boundary_kind != "LINE":
                    raise ValueError("TRIM_ARC_BOUNDARY_ONLY_SUPPORTS_LINE")
                circle = {"center": entity.get("center", {}), "radius": entity.get("radius", 0)}
                intersections = _filter_arc_intersections(_line_circle_intersections(boundary, circle), entity)
                if not intersections:
                    raise ValueError("TRIM_NO_INTERSECTION")
                center = _point(entity.get("center"))
                start_angle, end_angle = float(entity.get("start_angle", 0)), float(entity.get("end_angle", 360))
                if keep == "start":
                    selected = min(intersections, key=lambda candidate: (_angle(candidate, center) - start_angle) % 360.0)
                    updated = copy.deepcopy(entity)
                    updated["end_angle"] = _angle(selected, center)
                else:
                    selected = min(intersections, key=lambda candidate: (end_angle - _angle(candidate, center)) % 360.0)
                    updated = copy.deepcopy(entity)
                    updated["start_angle"] = _angle(selected, center)
            else:
                raise ValueError("TRIM_ONLY_SUPPORTS_LINE_OR_ARC")
            document.entities[document.entities.index(entity)] = updated
            return {"entity": updated}
        if name in {"entity_fillet", "entity_chamfer"}:
            ids = _refs(arguments, document)
            if len(ids) != 2 or len(set(ids)) != 2:
                raise ValueError("FILLET_CHAMFER_REQUIRES_TWO_DISTINCT_LINES")
            first, second = (document.entity(identifier) for identifier in ids)
            if any(str(line.get("type", "")).upper() != "LINE" for line in (first, second)):
                raise ValueError("FILLET_CHAMFER_ONLY_SUPPORTS_LINES")
            for line in (first, second):
                layer = document.layers.get(str(line.get("layer", "0")))
                if layer and layer.locked:
                    raise ValueError("CAD_LAYER_LOCKED:" + layer.name)
            tolerance = _positive_corner_number(arguments.get("tolerance", 1e-6), "INVALID_CORNER_TOLERANCE")
            if name == "entity_fillet":
                distance = _positive_corner_number(arguments.get("radius", arguments.get("distance")), "INVALID_FILLET_RADIUS")
                if "radius" in arguments and "distance" in arguments:
                    alias = _positive_corner_number(arguments["distance"], "INVALID_FILLET_RADIUS")
                    if alias != distance:
                        raise ValueError("FILLET_RADIUS_DISTANCE_CONFLICT")
            else:
                distance = _positive_corner_number(arguments.get("distance"), "INVALID_CHAMFER_DISTANCE")
            corner, first_other, second_other, keys = _corner_geometry(first, second, tolerance)
            if name == "entity_fillet":
                tangent1, tangent2, center, start_angle, end_angle = _fillet_geometry(first, second, distance, tolerance)
                connector = {"id": document.next_id("fillet"), "type": "ARC", "center": center, "radius": distance,
                             "start_angle": start_angle, "end_angle": end_angle}
            else:
                def setback(other):
                    dx, dy = other["x"] - corner["x"], other["y"] - corner["y"]
                    length = math.hypot(dx, dy)
                    if distance >= length - tolerance:
                        raise ValueError("CHAMFER_DISTANCE_TOO_LARGE")
                    return {"x": corner["x"] + dx / length * distance, "y": corner["y"] + dy / length * distance, "z": corner["z"]}
                tangent1, tangent2 = setback(first_other), setback(second_other)
                connector = {"id": document.next_id("chamfer"), "type": "LINE", "start": tangent1, "end": tangent2}
            connector["layer"] = first.get("layer", "0")
            if "sheet" in first:
                connector["sheet"] = first["sheet"]
            for attribute in ("color_index", "true_color", "linetype", "lineweight"):
                if attribute in first:
                    connector[attribute] = copy.deepcopy(first[attribute])
            first_updated, second_updated = copy.deepcopy(first), copy.deepcopy(second)
            first_updated[keys[0]], second_updated[keys[1]] = tangent1, tangent2
            document.entities[document.entities.index(first)] = first_updated
            document.entities[document.entities.index(second)] = second_updated
            document.entities.append(connector)
            check = {"kind": "fillet" if name == "entity_fillet" else "chamfer", "line_ids": ids,
                     "connector_id": connector["id"], "tolerance": tolerance,
                     "radius" if name == "entity_fillet" else "distance": distance}
            return {"entities": [*ids, connector["id"]], "corner_check": check}
        if name == "entity_offset":
            ids = _refs(arguments, document)
            if not ids:
                raise ValueError("ENTITY_SELECTION_REQUIRED")
            try:
                distance = float(arguments.get("distance", arguments.get("radius", 0)))
                side = float(arguments.get("side", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError("INVALID_OFFSET_DISTANCE") from exc
            if not math.isfinite(distance) or not math.isfinite(side) or abs(side) <= 1e-12:
                raise ValueError("INVALID_OFFSET_DISTANCE")
            signed_distance = distance * side
            changed = []
            for entity_id in ids:
                entity = document.entity(entity_id)
                kind = str(entity.get("type", "")).upper()
                if kind == "LINE":
                    updated = _line_offset(entity, signed_distance)
                elif kind in {"LWPOLYLINE", "POLYLINE"}:
                    updated = _polyline_offset(entity, signed_distance)
                elif kind in {"CIRCLE", "ARC"}:
                    updated = copy.deepcopy(entity)
                    radius = float(updated.get("radius", 0)) + signed_distance
                    if not math.isfinite(radius) or radius <= 1e-12:
                        raise ValueError("OFFSET_RADIUS_TOO_LARGE")
                    updated["radius"] = radius
                else:
                    raise ValueError("OFFSET_ENTITY_UNSUPPORTED:" + kind)
                document.entities[document.entities.index(entity)] = updated
                changed.append(entity_id)
            return {"entities": changed, "distance": signed_distance}
        if name == "layer_create":
            layer_name = str(arguments.get("name", ""))
            if not layer_name:
                raise ValueError("LAYER_NAME_REQUIRED")
            if layer_name in document.layers:
                raise ValueError("LAYER_EXISTS:" + layer_name)
            document.layers[layer_name] = Layer(layer_name, str(arguments.get("color", "#6b7280")), bool(arguments.get("visible", True)), bool(arguments.get("locked", False)), str(arguments.get("linetype", "CONTINUOUS")))
            return {"layer": document.layers[layer_name].to_dict()}
        if name == "layer_update":
            layer_name = str(arguments.get("name", ""))
            layer = document.layers.get(layer_name)
            if not layer:
                raise ValueError("LAYER_NOT_FOUND:" + layer_name)
            if "color" in arguments: layer.color = str(arguments["color"])
            if "visible" in arguments: layer.visible = bool(arguments["visible"])
            if "locked" in arguments: layer.locked = bool(arguments["locked"])
            if "linetype" in arguments: layer.linetype = str(arguments["linetype"])
            return {"layer": layer.to_dict()}
        if name == "layer_delete":
            layer_name = str(arguments.get("name", ""))
            if layer_name in {"0", "default"}:
                raise ValueError("DEFAULT_LAYER_CANNOT_BE_DELETED")
            if layer_name not in document.layers:
                raise ValueError("LAYER_NOT_FOUND:" + layer_name)
            replacement = str(arguments.get("replacement", "0"))
            document.ensure_layer(replacement)
            for entity in document.entities:
                if str(entity.get("layer", "0")) == layer_name:
                    entity["layer"] = replacement
            for block in document.blocks.values():
                for entity in block.entities:
                    if str(entity.get("layer", "0")) == layer_name:
                        entity["layer"] = replacement
            del document.layers[layer_name]
            return {"deleted": layer_name, "replacement": replacement}
        if name == "selection_create":
            selection_name = str(arguments.get("name", "selection"))
            refs = _refs(arguments, document)
            for ref in refs: document.entity(ref)
            document.selection_sets[selection_name] = SelectionSet(selection_name, refs, bool(arguments.get("active", True)))
            return {"selection_set": document.selection_sets[selection_name].to_dict()}
        if name == "selection_delete":
            selection_name = str(arguments.get("name", ""))
            if selection_name not in document.selection_sets:
                raise ValueError("SELECTION_NOT_FOUND:" + selection_name)
            del document.selection_sets[selection_name]
            return {"deleted": selection_name}
        if name == "block_create":
            block_name = str(arguments.get("name", ""))
            if not block_name: raise ValueError("BLOCK_NAME_REQUIRED")
            if block_name in document.blocks: raise ValueError("BLOCK_EXISTS:" + block_name)
            source = arguments.get("block") or {}
            document.blocks[block_name] = Block(block_name, _point(source.get("base_point", arguments.get("base_point"))), list(source.get("entities", arguments.get("entities", []))))
            return {"block": document.blocks[block_name].to_dict()}
        if name == "block_delete":
            block_name = str(arguments.get("name", ""))
            if block_name not in document.blocks: raise ValueError("BLOCK_NOT_FOUND:" + block_name)
            from ezdxf.sections.blocks import is_special_block
            if is_special_block(block_name): raise ValueError("SPECIAL_BLOCK_DELETE_UNSUPPORTED:" + block_name)
            references = document.entities + [e for block in document.blocks.values() for e in block.entities]
            if any(str(entity.get("block_name")) == block_name for entity in references):
                raise ValueError("BLOCK_IN_USE:" + block_name)
            del document.blocks[block_name]
            return {"deleted": block_name}
        if name == "block_update":
            block_name = str(arguments.get("name", ""))
            if block_name not in document.blocks:
                raise ValueError("BLOCK_NOT_FOUND:" + block_name)
            block = document.blocks[block_name]
            patch = arguments.get("patch") or {}
            if "base_point" in patch:
                block.base_point = _point(patch["base_point"])
            if "entities" in patch:
                block.entities = copy.deepcopy(patch["entities"])
            return {"block": block.to_dict()}
        if name == "hatch_create":
            boundary = [_point(point) for point in arguments.get("boundary", arguments.get("boundary_vertices", []))]
            source_ids = [str(value) for value in arguments.get("source_entity_ids", [])]
            source_handles = []
            for source_id in source_ids:
                source = document.entity(source_id)
                if not source.get("handle"):
                    raise ValueError("ASSOCIATIVE_HATCH_SOURCE_HANDLE_MISSING:" + source_id)
                source_handles.append(str(source["handle"]))
            associative = bool(arguments.get("associative", False) or source_handles)
            if associative and not source_handles:
                raise ValueError("ASSOCIATIVE_HATCH_SOURCE_REQUIRED")
            entity = {"id": document.next_id(), "type": "HATCH", "layer": str(arguments.get("layer", "0")), "pattern": str(arguments.get("pattern", "SOLID")), "solid": bool(arguments.get("solid", True)), "boundary_vertices": boundary}
            if associative:
                entity["associative"] = True
                entity["boundary_paths"] = [{"kind": "polyline", "flags": 2, "closed": True,
                                               "source_handles": source_handles,
                                               "vertices": [point | {"bulge": 0.0} for point in boundary]}]
            document.ensure_layer(entity["layer"]); document.entities.append(entity)
            return {"entity": entity}
        if name == "dimension_create":
            entity = {"id": document.next_id(), "type": "DIMENSION", "layer": str(arguments.get("layer", "ANNOTATION")), "definition": _point(arguments.get("definition")), "text": str(arguments.get("text", "")), "measurement": float(arguments.get("measurement", 0)), "block_name": arguments.get("block_name")}
            document.ensure_layer(entity["layer"]); document.entities.append(entity)
            return {"entity": entity}
        if name == "dimension_ordinate_create":
            layer = str(arguments.get("layer", "ANNOTATION"))
            document.ensure_layer(layer)
            point = _point(arguments.get("point"))
            axis = str(arguments.get("axis", "x")).lower()
            measurement = float(arguments.get("measurement", point["x"] if axis == "x" else point["y"]))
            entity = {"id": document.next_id(), "type": "DIMENSION", "dimension_type": "ordinate", "axis": axis, "layer": layer, "definition": _point(arguments.get("definition", point)), "start": point, "text": str(arguments.get("text", "")), "measurement": measurement, "block_name": arguments.get("block_name")}
            document.entities.append(entity)
            return {"entity": entity}
        if name in {"dimension_linear_create", "dimension_radial_create", "dimension_angular_create"}:
            layer = str(arguments.get("layer", "ANNOTATION"))
            document.ensure_layer(layer)
            if name == "dimension_linear_create":
                start, end = _point(arguments.get("start")), _point(arguments.get("end"))
                measurement = float(arguments.get("measurement", math.dist((start["x"], start["y"], start["z"]), (end["x"], end["y"], end["z"]))))
                dimension_type = "linear"
            elif name == "dimension_radial_create":
                center = _point(arguments.get("center")); point = _point(arguments.get("point"))
                start, end = center, point
                measurement = float(arguments.get("measurement", math.dist((center["x"], center["y"], center["z"]), (point["x"], point["y"], point["z"]))))
                dimension_type = "radial"
            else:
                center = _point(arguments.get("center")); start, end = _point(arguments.get("start")), _point(arguments.get("end"))
                measurement = float(arguments.get("measurement", math.degrees(math.atan2(end["y"] - center["y"], end["x"] - center["x"]) - math.atan2(start["y"] - center["y"], start["x"] - center["x"]))))
                dimension_type = "angular"
            entity = {"id": document.next_id(), "type": "DIMENSION", "dimension_type": dimension_type, "layer": layer, "definition": _point(arguments.get("definition", end)), "start": start, "end": end, "center": _point(arguments.get("center")), "text": str(arguments.get("text", "")), "measurement": measurement, "block_name": arguments.get("block_name")}
            document.entities.append(entity)
            return {"entity": entity}
        if name == "constraint_add":
            refs = [str(value) for value in arguments.get("refs", [])]
            for ref in refs: document.entity(ref)
            constraint = Constraint(str(arguments.get("kind", arguments.get("type", "equal"))), refs, arguments.get("value"), dict(arguments.get("parameters", {})))
            document.constraints.append(constraint)
            return {"constraint": constraint.to_dict()}
        if name == "constraint_delete":
            index = int(arguments.get("index", -1))
            if index < 0 or index >= len(document.constraints): raise ValueError("CONSTRAINT_NOT_FOUND")
            return {"deleted": document.constraints.pop(index).to_dict()}
        if name == "document_recompute":
            return {"validation": validate_cad_document(document)}
        raise ValueError("UNKNOWN_CAD_OPERATION:" + operation)

    def _write(self, transaction: RunTransaction, document: CadDocument, output_file: Path,
               requirements: dict[str, Any], operation: str) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
        staged_dxf = transaction.stage_dir / output_file.name
        staged_sidecar = Path(str(staged_dxf) + ".cad.json")
        legacy_opaque = document._source_path is None and any(e.get("raw_tags") for e in document.entities)
        if legacy_opaque:
            from .legacy_dxf import write_legacy_dxf
            if document.sheets or document.viewports:
                raise ValueError("LEGACY_DXF_LAYOUT_EDIT_UNSUPPORTED")
            write_legacy_dxf(staged_dxf, entities=document.entities,
                             blocks=[block.to_dict() for block in document.blocks.values()],
                             coordinate_systems=document.coordinate_systems)
        else:
            write_native(staged_dxf, entities=document.entities, blocks=[block.to_dict() for block in document.blocks.values()],
                         coordinate_systems=document.coordinate_systems, layers={name: layer.to_dict() for name, layer in document.layers.items()},
                         sheets=document.sheets, viewports=document.viewports, source=document._source_path, units=document.units)
        staged_sidecar.write_text(json.dumps(document.sidecar(staged_dxf), ensure_ascii=False, indent=2), encoding="utf-8")
        relative = str(output_file.relative_to(transaction.project_dir))
        files = {relative: str(staged_dxf), relative + ".cad.json": str(staged_sidecar)}
        history = CadHistory(transaction, output_file)
        history.prepare_edit(files, operation)
        persisted_validation = {}
        def validate(_files):
            persisted_validation.update(self._validate_persisted(document, output_file, requirements))
            if not persisted_validation["valid"]:
                raise ValueError("CAD_PERSISTED_VALIDATION_FAILED:" + json.dumps(persisted_validation.get("errors", []), ensure_ascii=False))
            transaction.record("cad_persisted_validation", persisted_validation)
        committed = transaction.commit(files, destination_root=transaction.project_dir, validate=validate)
        return committed, persisted_validation, history.summary()

    def _validate_persisted(self, expected: CadDocument, output_file: Path, requirements: dict[str, Any] | None = None) -> dict[str, Any]:
        """Re-open the committed DXF and validate what was actually persisted.

        The in-memory validator protects the staged document.  This second pass
        catches writer/parser loss, malformed DXF records and sidecar/entity ID
        drift after the transaction has copied files into the project.
        """
        persisted = self.open(output_file)
        validation = validate_cad_document(persisted, requirements)
        expected_ids = [str(item.get("id")) for item in expected.entities]
        persisted_ids = [str(item.get("id")) for item in persisted.entities]
        integrity = {
            "entity_count": len(expected.entities) == len(persisted.entities),
            "entity_ids": sorted(expected_ids) == sorted(persisted_ids),
            "entity_types": expected.to_dict().get("entity_types", {}) == persisted.to_dict().get("entity_types", {}),
            "layers": set(expected.layers) <= set(persisted.layers),
        }
        validation["persisted"] = True
        validation["integrity"] = integrity
        validation["valid"] = bool(validation.get("valid")) and all(integrity.values())
        if not all(integrity.values()):
            validation.setdefault("errors", []).append({"code": "PERSISTED_DOCUMENT_MISMATCH", "integrity": integrity})
        from .dxf_validation import inspect_native
        native = inspect_native(output_file, expected, corner_connections=(requirements or {}).get("corner_connections"))
        validation["native"] = native
        validation["errors"].extend(native["errors"])
        validation["warnings"].extend(native["warnings"])
        validation["valid"] = validation["valid"] and native["valid"]
        return validation

    def _history_operation(self, operation: str, project_root: Path, path: Path) -> dict[str, Any]:
        if operation == "cad.history":
            with RunTransaction.read_lock(project_root) as transaction:
                return {"history": CadHistory(transaction, path).summary()}
        with RunTransaction(project_root) as transaction:
            transaction.begin({"operation": operation, "path": str(path)})
            history = CadHistory(transaction, path)
            files, restored = history.prepare_restore(operation.removeprefix("cad."))
            validation: dict[str, Any] = {}
            restored_document = None
            def validate(_files):
                nonlocal restored_document
                history.validate_restored(restored)
                # A repair may have started from invalid geometry. Undo restores
                # those original bytes, reporting their validation honestly.
                if path.exists():
                    document = self.open(path)
                    restored_document = document.to_dict()
                    validation.update(validate_cad_document(document))
                else:
                    validation.update({"valid": True, "deleted": True})
                transaction.record("cad_restored_validation", validation)
            transaction.commit(files, destination_root=project_root, validate=validate)
            return {"operation": operation, "path": str(path), "run_id": transaction.run_id,
                    "restored_revision": restored["revision"], "history": history.summary(),
                    "document": restored_document, "persisted_validation": validation, "timeline": transaction.events()}

    def _script_apply(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply a project-local helper script as one validated CAD transaction."""
        configured = bool(self.config.get("allow_scripts")) or bool((self.config.get("cad") or {}).get("allow_scripts"))
        if not configured and not bool(arguments.get("allow_scripts", False)):
            raise ScriptPolicyError("CAD_SCRIPTS_DISABLED", "set allow_scripts=true in the agent policy or operation arguments")
        raw_input = arguments.get("input_file") or arguments.get("path")
        if not raw_input:
            raise ValueError("CAD_INPUT_FILE_REQUIRED")
        input_path = Path(raw_input).expanduser()
        project_root = Path(arguments.get("project_dir") or input_path.parent).expanduser().resolve()
        input_file = (project_root / input_path if arguments.get("project_dir") and not input_path.is_absolute() else input_path).resolve()
        output_path = Path(arguments.get("output_file") or input_file).expanduser()
        output_file = (project_root / output_path if arguments.get("project_dir") and not output_path.is_absolute() else output_path).resolve()
        if project_root not in input_file.parents or project_root not in output_file.parents:
            raise ValueError("PATH_OUTSIDE_PROJECT")
        if input_file.suffix.lower() != ".dxf" or output_file.suffix.lower() != ".dxf":
            raise ValueError("CAD_SCRIPT_REQUIRES_DXF")
        if not input_file.is_file():
            raise FileNotFoundError(input_file)
        script_file = arguments.get("script_file")
        if script_file:
            script_path = Path(str(script_file)).expanduser()
            script_path = (project_root / script_path if not script_path.is_absolute() else script_path).resolve()
            if project_root not in script_path.parents or not script_path.is_file():
                raise ValueError("CAD_SCRIPT_OUTSIDE_PROJECT")
            source = script_path.read_text(encoding="utf-8")
            script_label = str(script_path.relative_to(project_root))
        else:
            source = arguments.get("script")
            if not isinstance(source, str) or not source.strip():
                raise ValueError("CAD_SCRIPT_REQUIRED")
            script_label = "<inline>"
        with RunTransaction(project_root) as transaction:
            transaction.begin({"operation": "cad.script_apply", "input_file": str(input_file),
                               "output_file": str(output_file), "script": script_label})
            before = self.open(input_file)
            transaction.checkpoint("before-cad-script", {"entity_count": len(before.entities), "script": script_label})
            output_name = str(output_file.relative_to(project_root)).replace("\\", "/")
            details = run_controlled_script(
                transaction_dir=transaction.stage_dir,
                input_file=input_file,
                output_name=output_name,
                source=source,
                parameters=arguments.get("parameters") or {},
                timeout_seconds=int(arguments.get("timeout_seconds", self.config.get("cad", {}).get("script_timeout_seconds", 30))),
                max_output_bytes=int(arguments.get("max_output_bytes", self.config.get("cad", {}).get("script_max_output_bytes", 16 * 1024 * 1024))),
            )
            staged_dxf = Path(details["output_file"])
            after = self.open(staged_dxf)
            requirements = copy.deepcopy(arguments.get("requirements") or {})
            validation = validate_cad_document(after, requirements)
            if not validation["valid"]:
                transaction.rollback("CAD_SCRIPT_VALIDATION_FAILED")
                raise ValueError("CAD_SCRIPT_VALIDATION_FAILED:" + json.dumps(validation.get("errors", []), ensure_ascii=False))
            diff = before.diff(after)
            changes = _entity_change_ids(before, after)
            after.metadata.setdefault("operation_history", []).append({
                "operation": "cad.script_apply", "script": script_label, "change_count": diff["change_count"],
                "run_id": transaction.run_id, **changes})
            staged_sidecar = Path(str(staged_dxf) + ".cad.json")
            staged_sidecar.write_text(json.dumps(after.sidecar(staged_dxf), ensure_ascii=False, indent=2), encoding="utf-8")
            relative = str(output_file.relative_to(project_root))
            files = {relative: str(staged_dxf), relative + ".cad.json": str(staged_sidecar)}
            history = CadHistory(transaction, output_file)
            history.prepare_edit(files, "cad.script_apply")
            persisted_validation: dict[str, Any] = {}

            def validate(_files):
                persisted_validation.update(self._validate_persisted(after, output_file, requirements))
                if not persisted_validation["valid"]:
                    raise ValueError("CAD_SCRIPT_PERSISTED_VALIDATION_FAILED:" + json.dumps(persisted_validation.get("errors", []), ensure_ascii=False))
                transaction.record("cad_script_persisted_validation", persisted_validation)

            committed = transaction.commit(files, destination_root=project_root, validate=validate)
            return {
                "operation": "cad.script_apply", "path": committed[relative],
                "sidecar": committed[relative + ".cad.json"], "script": script_label,
                "script_execution": {key: value for key, value in details.items() if key not in {"workspace", "command"}},
                "document": after.to_dict(), "diff": diff, "validation": validation,
                "persisted_validation": persisted_validation, "run_id": transaction.run_id,
                "history": history.summary(), "timeline": transaction.events(),
            }

    def apply(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation == "cad.history_view":
            from .cad_replay import history_view
            raw_input = Path(arguments["input_file"]).expanduser()
            if not arguments.get("project_dir"):
                raw_input = raw_input.absolute()
            return history_view(arguments.get("project_dir") or raw_input.parent, raw_input,
                                arguments["revision_id"], arguments.get("side", "after"))
        if operation in {"cad.standard_catalog", "cad.standard_library"}:
            from .standards import catalog, standard_library
            return catalog(arguments.get("standard")) if operation == "cad.standard_catalog" else standard_library(arguments.get("category"), arguments.get("query"))
        if operation == "cad.script_apply":
            return self._script_apply(arguments)
        if operation == "cad.plot":
            from .plotting import plot_operation
            return plot_operation(arguments)
        raw_input = arguments.get("input_file") or arguments.get("path") or arguments.get("output_file")
        if not raw_input:
            raise ValueError("CAD_INPUT_FILE_REQUIRED")
        input_path = Path(raw_input).expanduser()
        project_root = Path(arguments.get("project_dir") or input_path.parent).expanduser().resolve()
        input_file = (project_root / input_path if arguments.get("project_dir") and not input_path.is_absolute() else input_path).resolve()
        if project_root not in input_file.parents:
            raise ValueError("PATH_OUTSIDE_PROJECT")
        if operation in {"cad.history", "cad.undo", "cad.redo"}:
            return self._history_operation(operation, project_root, input_file)
        if operation in {"cad.audit", "cad.document_diff", "cad.visual_diff", "cad.pid_audit", "cad.standard_parts_audit", "cad.document_open", "cad.document_inspect", "cad.document_recompute"}:
            with RunTransaction.read_lock(project_root):
                if not input_file.is_file():
                    raise FileNotFoundError(input_file)
                document = self.open(input_file)
                validation = validate_cad_document(document, arguments.get("requirements") or {})
                result = {"document": document.to_dict(), "validation": validation}
                if operation == "cad.audit":
                    from .dxf_validation import inspect_native
                    native = inspect_native(input_file, corner_connections=(arguments.get("requirements") or {}).get("corner_connections"))
                    validation["native"] = native
                    validation["errors"].extend(native["errors"])
                    validation["warnings"].extend(native["warnings"])
                    validation["valid"] = validation["valid"] and native["valid"]
                    payload = input_file.read_bytes()
                    result.update({"path": str(input_file), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(), "sidecar_present": Path(str(input_file) + ".cad.json").is_file()})
                elif operation == "cad.document_diff":
                    right_file = (project_root / Path(arguments.get("right_file", "")).expanduser()).resolve()
                    if project_root not in right_file.parents:
                        raise ValueError("PATH_OUTSIDE_PROJECT")
                    result = {"left": str(input_file), "right": str(right_file), "diff": document.diff(self.open(right_file))}
                elif operation == "cad.visual_diff":
                    right_file = (project_root / Path(arguments.get("right_file", "")).expanduser()).resolve()
                    if project_root not in right_file.parents:
                        raise ValueError("PATH_OUTSIDE_PROJECT")
                    right = self.open(right_file)
                    width = max(320, min(int(arguments.get("width", 900)), 2400))
                    height = max(240, min(int(arguments.get("height", 560)), 1600))
                    result = {"left": str(input_file), "right": str(right_file),
                              "diff": document.diff(right),
                              "visual_diff": visual_diff(document.to_dxf(), right.to_dxf(),
                                                          width=width, height=height)}
                elif operation == "cad.pid_audit":
                    result = {"path": str(input_file),
                              "pid": audit_pid(document.to_dxf(),
                                               tolerance=float(arguments.get("tolerance", 1e-6)),
                                               requirements=arguments.get("requirements") or {})}
                elif operation == "cad.standard_parts_audit":
                    from .standards import drawing_parts
                    result = {"path": str(input_file), "parts": drawing_parts(document.to_dxf())}
                return result
        output_path = Path(arguments.get("output_file") or input_file).expanduser()
        output_file = (project_root / output_path if arguments.get("project_dir") and not output_path.is_absolute() else output_path).resolve()
        if project_root not in output_file.parents:
            raise ValueError("PATH_OUTSIDE_PROJECT")
        operations = (arguments.get("operations") or []) if operation in {"cad.batch_apply", "cad.validate_and_repair"} else [{"operation": operation, **arguments}]
        if not isinstance(operations, list) or (not operations and operation != "cad.validate_and_repair"):
            raise ValueError("CAD_OPERATIONS_REQUIRED")
        with RunTransaction(project_root) as transaction:
            transaction.begin({"operation": operation, "input_file": str(input_file), "output_file": str(output_file), "operation_count": len(operations)})
            # Read under the same lock that covers writing and disk validation.
            document = self.open(input_file)
            before = document.clone()
            transaction.checkpoint("before-cad-edit", {"operation": operation, "entity_count": len(document.entities)})
            results = []
            replay_steps = []
            corner_postconditions = []
            for item in operations:
                item = dict(item)
                item_name = str(item.pop("operation", operation))
                step_before = document.clone()
                result = {"operation": item_name, **self._apply_one(document, item_name, item)}
                replay_steps.append({"operation": item_name, **_entity_change_ids(step_before, document)})
                if "corner_check" in result:
                    from .corner_validation import inspect_corner
                    check = result["corner_check"]
                    checked = inspect_corner(document.entities, check)
                    result["geometry_validation"] = checked
                    if not checked["valid"]:
                        raise ValueError("CAD_CORNER_VALIDATION_FAILED:" + json.dumps(checked["errors"]))
                    snapshot = {identifier: copy.deepcopy(document.entity(identifier))
                                for identifier in [*check["line_ids"], check["connector_id"]]}
                    corner_postconditions.append((check, snapshot, result))
                results.append(result)
            requirements = copy.deepcopy(arguments.get("requirements") or {})
            validation = validate_cad_document(document, requirements)
            repair_plan: list[dict[str, Any]] = []
            if not validation["valid"] and bool(arguments.get("auto_repair", True)):
                repair_plan = suggest_repairs(document, validation)
                for repair in repair_plan:
                    repair_payload = dict(repair)
                    repair_name = repair_payload.pop("operation")
                    step_before = document.clone()
                    results.append({"operation": repair_name, "replanned": True, **self._apply_one(document, repair_name, repair_payload)})
                    replay_steps.append({"operation": repair_name, "replanned": True,
                                         **_entity_change_ids(step_before, document)})
                validation = validate_cad_document(document, requirements)
            if not validation["valid"]:
                transaction.rollback("CAD_VALIDATION_FAILED")
                raise ValueError("CAD_VALIDATION_FAILED:" + json.dumps(validation["errors"], ensure_ascii=False))
            # A later edit in the batch may deliberately change or delete the
            # corner. It supersedes that creation postcondition, not any explicit
            # final requirements supplied by the caller. Each step was checked
            # above; unchanged triples also receive an independent disk check.
            final_entities = {str(entity["id"]): entity for entity in document.entities}
            for check, snapshot, result in corner_postconditions:
                unchanged = all(final_entities.get(identifier) == entity for identifier, entity in snapshot.items())
                result["corner_check_status"] = "persisted" if unchanged else "superseded_by_later_edit"
                if unchanged:
                    requirements.setdefault("corner_connections", []).append(check)
            diff = before.diff(document)
            changes = _entity_change_ids(before, document)
            document.metadata.setdefault("operation_history", []).append({
                "operation": operation, "change_count": diff["change_count"], "repair_count": len(repair_plan),
                "run_id": transaction.run_id, "steps": replay_steps, **changes})
            transaction.checkpoint("after-cad-validation", {"valid": True, "change_count": diff["change_count"], "repair_count": len(repair_plan)})
            transaction.record("cad_diff", {"change_count": diff["change_count"], "repair_count": len(repair_plan)})
            committed, persisted_validation, history = self._write(transaction, document, output_file, requirements, operation)
            return {"operation": operation, "path": committed[str(output_file.relative_to(project_root))], "sidecar": committed[str(output_file.relative_to(project_root)) + ".cad.json"], "document": document.to_dict(), "validation": validation, "persisted_validation": persisted_validation, "diff": diff, "results": results, "repair_plan": repair_plan, "replanned": bool(repair_plan), "replanned_plan": repair_plan, "run_id": transaction.run_id, "history": history, "timeline": transaction.events()}
