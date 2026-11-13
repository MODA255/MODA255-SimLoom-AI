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

from .cad_model import Block, CadDocument, Constraint, Layer, SelectionSet, entity_points, transform_entity, validate_cad_document
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


def _trace_boundary_entity(document: CadDocument, arguments: dict[str, Any]) -> dict[str, Any]:
    """Find one deterministic closed loop in connected straight linework.

    The operation deliberately leaves arcs and bulged segments out of this
    first kernel.  Silently flattening those curves would create a boundary
    that looks closed while changing its engineering meaning, so callers get
    an explicit unsupported/ambiguous error instead.
    """
    try:
        tolerance = float(arguments.get("tolerance", 1e-6))
    except (TypeError, ValueError) as exc:
        raise ValueError("TRACE_BOUNDARY_TOLERANCE_INVALID") from exc
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("TRACE_BOUNDARY_TOLERANCE_INVALID")
    selected_values = arguments.get("entity_ids")
    if isinstance(selected_values, str):
        selected_values = [selected_values]
    selected = {str(value) for value in (selected_values or [])}
    seed = str(arguments.get("seed_entity_id") or arguments.get("entity_id") or "")
    source_entities = []
    for entity in document.entities:
        entity_id = str(entity.get("id"))
        if selected and entity_id not in selected:
            continue
        kind = str(entity.get("type", "")).upper()
        if kind == "LINE":
            start, end = _point(entity.get("start")), _point(entity.get("end"))
            if _distance(start, end) <= tolerance:
                continue
            source_entities.append((entity_id, start, end, entity))
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            vertices = [_point(value) for value in entity.get("vertices", [])]
            bulges = entity.get("bulges") or []
            if any(abs(float(value)) > tolerance for value in bulges):
                if entity_id in selected:
                    raise ValueError("TRACE_BOUNDARY_BULGED_POLYLINE_UNSUPPORTED:" + entity_id)
                continue
            if len(vertices) < 2:
                continue
            count = len(vertices) if entity.get("closed") else len(vertices) - 1
            for index in range(count):
                start, end = vertices[index], vertices[(index + 1) % len(vertices)]
                if _distance(start, end) > tolerance:
                    source_entities.append((entity_id, start, end, entity))
    if not source_entities:
        raise ValueError("TRACE_BOUNDARY_NO_LINEAR_EDGES")

    # Snap endpoints into a stable node graph.  The first observed coordinate
    # is retained for output so the result does not accumulate rounding drift.
    nodes: list[dict[str, float]] = []
    buckets: dict[tuple[int, int, int], list[int]] = {}

    def node_for(point: dict[str, float]) -> int:
        key = tuple(round(float(point[axis]) / tolerance) for axis in ("x", "y", "z"))
        for index in buckets.get(key, []):
            if _distance(nodes[index], point) <= tolerance:
                return index
        index = len(nodes); nodes.append(copy.deepcopy(point)); buckets.setdefault(key, []).append(index)
        return index

    edges = []
    for entity_id, start, end, _entity in source_entities:
        first, second = node_for(start), node_for(end)
        if first == second:
            continue
        edges.append({"id": entity_id, "first": first, "second": second})
    if len(edges) < 3:
        raise ValueError("TRACE_BOUNDARY_CLOSED_LOOP_NOT_FOUND")
    adjacency: dict[int, list[int]] = {index: [] for index in range(len(nodes))}
    for index, edge in enumerate(edges):
        adjacency[edge["first"]].append(index); adjacency[edge["second"]].append(index)

    seed_edge = next((index for index, edge in enumerate(edges) if edge["id"] == seed), None)
    if seed_edge is None and seed:
        raise ValueError("TRACE_BOUNDARY_SEED_NOT_FOUND:" + seed)
    if arguments.get("point") is not None:
        target = _point(arguments["point"])
        candidates = sorted((_distance(target, node), index) for index, node in enumerate(nodes))
        if not candidates or candidates[0][0] > tolerance:
            raise ValueError("TRACE_BOUNDARY_POINT_NOT_ON_GRAPH")
        start_node = candidates[0][1]
    elif seed_edge is not None:
        start_node = edges[seed_edge]["first"]
    else:
        start_node = min((edge["first"] for edge in edges), key=lambda index: (nodes[index]["x"], nodes[index]["y"], nodes[index]["z"]))

    def other(edge_index: int, node: int) -> int:
        edge = edges[edge_index]
        return edge["second"] if edge["first"] == node else edge["first"]

    def angle(from_node: int, to_node: int) -> float:
        first, second = nodes[from_node], nodes[to_node]
        return math.atan2(second["y"] - first["y"], second["x"] - first["x"])

    def find_cycle(current: int, path_nodes: list[int], path_edges: list[int], used_edges: set[int]) -> tuple[list[int], list[int]] | None:
        if current == start_node and len(path_edges) >= 3:
            return path_nodes, path_edges
        if len(path_nodes) > len(edges) + 1:
            return None
        previous = path_nodes[-2] if len(path_nodes) > 1 else None
        candidates = []
        for edge_index in adjacency.get(current, []):
            if edge_index in used_edges:
                continue
            next_node = other(edge_index, current)
            if next_node != start_node and next_node in path_nodes:
                continue
            turn = angle(current, next_node)
            if previous is not None:
                turn = (turn - angle(previous, current)) % (2 * math.pi)
            candidates.append((turn, edges[edge_index]["id"], edge_index, next_node))
        for _turn, _source_id, edge_index, next_node in sorted(candidates):
            result = find_cycle(next_node, path_nodes + [next_node], path_edges + [edge_index], used_edges | {edge_index})
            if result:
                return result
        return None

    cycle = None
    if seed_edge is not None:
        seed_other = other(seed_edge, start_node)
        cycle = find_cycle(seed_other, [start_node, seed_other], [seed_edge], {seed_edge})
    else:
        cycle = find_cycle(start_node, [start_node], [], set())
    if not cycle:
        raise ValueError("TRACE_BOUNDARY_CLOSED_LOOP_NOT_FOUND")
    cycle_nodes_raw, cycle_edge_indices = cycle
    cycle_nodes = cycle_nodes_raw[:-1] if cycle_nodes_raw[-1] == start_node else cycle_nodes_raw
    if len(cycle_nodes) < 3:
        raise ValueError("TRACE_BOUNDARY_CLOSED_LOOP_TOO_SHORT")
    source_ids = list(dict.fromkeys(edges[index]["id"] for index in cycle_edge_indices))
    layer = str(arguments.get("layer") or (document.entity(source_ids[0]).get("layer", "0") if source_ids else "0"))
    document.ensure_layer(layer)
    entity = {"id": document.next_id("boundary"), "type": "LWPOLYLINE", "layer": layer,
              "vertices": [copy.deepcopy(nodes[index]) for index in cycle_nodes], "closed": True,
              "boundary_trace": {"source_ids": source_ids, "tolerance": tolerance,
                                  "seed_entity_id": seed or None, "point": copy.deepcopy(arguments.get("point"))}}
    document.entities.append(entity)
    if not bool(arguments.get("keep_source", True)):
        source_set = set(source_ids)
        document.entities = [item for item in document.entities if str(item.get("id")) not in source_set or str(item.get("id")) == entity["id"]]
    return {"entity": entity, "source_ids": source_ids, "closed": True}


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


def _duplicate_key(entity: dict[str, Any], tolerance: float) -> str:
    """Build a stable geometric key for duplicate cleanup.

    Entity IDs and DXF handles are deliberately excluded.  The key keeps layer
    and layout because deleting geometry from a different drawing context is
    surprising, while common line, circle, arc and polyline representations are
    normalized so reversed LINEs and cyclic closed polylines compare equal.
    """
    kind = str(entity.get("type", "")).upper()

    def number(value: Any) -> int | str:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value)
        if not math.isfinite(numeric):
            return str(numeric)
        return int(round(numeric / tolerance))

    def point(value: Any) -> tuple[int | str, int | str, int | str]:
        value = value or {}
        return number(value.get("x", 0)), number(value.get("y", 0)), number(value.get("z", 0))

    context = (kind, str(entity.get("layer", "0")), str(entity.get("sheet", entity.get("layout", "Model"))))
    if kind == "LINE":
        return repr(context + tuple(sorted((point(entity.get("start")), point(entity.get("end"))))))
    if kind in {"CIRCLE", "ARC"}:
        payload: tuple[Any, ...] = context + (point(entity.get("center")), number(entity.get("radius", 0)))
        if kind == "ARC":
            payload += (number(float(entity.get("start_angle", 0)) % 360.0),
                        number(float(entity.get("end_angle", 360)) % 360.0))
        return repr(payload)
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        vertices = [point(item) for item in entity.get("vertices", [])]
        bulges = [number(item) for item in entity.get("bulges", [])]
        forward = tuple(vertices), tuple(bulges), bool(entity.get("closed"))
        reverse = tuple(reversed(vertices)), tuple(-float(item) if isinstance(item, (int, float)) else item for item in reversed(bulges)), bool(entity.get("closed"))
        if entity.get("closed") and vertices:
            rotations = []
            for offset in range(len(vertices)):
                rotations.append((tuple(vertices[offset:] + vertices[:offset]), tuple(bulges[offset:] + bulges[:offset]), True))
            reversed_vertices = list(reversed(vertices))
            reversed_bulges = list(reversed(bulges))
            rotations.extend((tuple(reversed_vertices[offset:] + reversed_vertices[:offset]), tuple(reversed_bulges[offset:] + reversed_bulges[:offset]), True) for offset in range(len(vertices)))
            forward = min(rotations)
        else:
            forward = min(forward, reverse)
        return repr(context + (forward,))

    def normalize(value: Any, key: str = "") -> Any:
        if key in {"id", "handle", "raw_tags"}:
            return None
        if isinstance(value, dict):
            return {name: normalize(child, name) for name, child in sorted(value.items()) if name not in {"id", "handle", "raw_tags"}}
        if isinstance(value, list):
            return [normalize(child) for child in value]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return number(value)
        return value

    return repr(normalize(entity))


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


def _infinite_line_segment_intersection(line: dict[str, Any], segment: dict[str, Any],
                                        tolerance: float = 1e-9) -> dict[str, float] | None:
    """Intersect an infinite target line with a finite boundary segment."""
    a, b = _line_point(line, "start"), _line_point(line, "end")
    c, d = _line_point(segment, "start"), _line_point(segment, "end")
    abx, aby = b["x"] - a["x"], b["y"] - a["y"]
    cdx, cdy = d["x"] - c["x"], d["y"] - c["y"]
    denominator = abx * cdy - aby * cdx
    if abs(denominator) <= tolerance:
        return None
    acx, acy = c["x"] - a["x"], c["y"] - a["y"]
    line_factor = (acx * cdy - acy * cdx) / denominator
    segment_factor = (acx * aby - acy * abx) / denominator
    if segment_factor < -tolerance or segment_factor > 1.0 + tolerance:
        return None
    segment_factor = min(1.0, max(0.0, segment_factor))
    return {"x": a["x"] + line_factor * abx, "y": a["y"] + line_factor * aby,
            "z": a["z"] + line_factor * (b["z"] - a["z"])}


def _line_polyline_intersections(line: dict[str, Any], boundary: dict[str, Any], tolerance: float = 1e-9,
                                 *, finite_line: bool = True) -> list[dict[str, float]]:
    vertices = [_point(value) for value in boundary.get("vertices", [])]
    if len(vertices) < 2:
        raise ValueError("TRIM_BOUNDARY_TOO_SHORT")
    bulges = boundary.get("bulges") or []
    count = len(vertices) if boundary.get("closed") else len(vertices) - 1
    points: list[dict[str, float]] = []
    for index in range(count):
        start, end = vertices[index], vertices[(index + 1) % len(vertices)]
        bulge = float(bulges[index]) if index < len(bulges) else 0.0
        if not math.isfinite(bulge):
            raise ValueError("TRIM_INVALID_BULGE")
        if abs(bulge) > tolerance:
            intersections = _line_bulge_segment_intersections(line, start, end, bulge, tolerance,
                                                               finite_line=finite_line)
        else:
            if finite_line:
                intersections = [_segment_intersection(line, {"start": start, "end": end}, tolerance)]
            else:
                intersections = [_infinite_line_segment_intersection(line, {"start": start, "end": end}, tolerance)]
        for point in intersections:
            if point is not None and not any(_distance(point, existing) <= tolerance for existing in points):
                points.append(point)
    return points


def _line_bulge_segment_intersections(line: dict[str, Any], start: dict[str, Any], end: dict[str, Any],
                                       bulge: float, tolerance: float = 1e-9,
                                       *, finite_line: bool = True) -> list[dict[str, float]]:
    """Intersect a finite line with one DXF bulge arc segment."""
    chord = math.hypot(end["x"] - start["x"], end["y"] - start["y"])
    if chord <= tolerance:
        raise ValueError("TRIM_INVALID_BULGE")
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    normal = {"x": -(end["y"] - start["y"]) / chord, "y": (end["x"] - start["x"]) / chord}
    offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
    midpoint = {"x": (start["x"] + end["x"]) / 2.0, "y": (start["y"] + end["y"]) / 2.0,
                "z": (start.get("z", 0.0) + end.get("z", 0.0)) / 2.0}
    center = {"x": midpoint["x"] + normal["x"] * offset, "y": midpoint["y"] + normal["y"] * offset, "z": midpoint["z"]}
    intersections = _line_circle_intersections(line, {"center": center, "radius": radius}, tolerance,
                                               finite_line=finite_line)
    start_angle = _angle(start, center)
    sweep = abs(math.degrees(4.0 * math.atan(bulge)))
    filtered: list[dict[str, float]] = []
    for point in intersections:
        value = _angle(point, center)
        travelled = (value - start_angle) % 360.0 if bulge > 0 else (start_angle - value) % 360.0
        if travelled <= sweep + 1e-7:
            filtered.append(point)
    return filtered


def _line_circle_intersections(line: dict[str, Any], circle: dict[str, Any], tolerance: float = 1e-9,
                               *, finite_line: bool = True) -> list[dict[str, float]]:
    """Return line/circle intersections in deterministic order.

    By default the target is a finite LINE segment, which is the behavior
    needed by TRIM. EXTEND passes ``finite_line=False`` so the same kernel can
    find intersections on the target's infinite supporting line.
    """
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
        if not finite_line or -tolerance <= factor <= 1.0 + tolerance:
            if finite_line:
                factor = min(1.0, max(0.0, factor))
            point = {"x": start["x"] + factor * dx, "y": start["y"] + factor * dy,
                     "z": start["z"] + factor * (end["z"] - start["z"])}
            if not output or _distance(output[-1], point) > tolerance:
                output.append(point)
    return output


def _line_ellipse_intersections(line: dict[str, Any], ellipse: dict[str, Any], tolerance: float = 1e-9,
                                 *, finite_line: bool = True) -> list[dict[str, float]]:
    """Return finite line/ellipse intersections in deterministic order.

    DXF stores the major-axis vector from the ellipse center, plus a positive
    minor-to-major ratio. Solving in that local orthonormal basis keeps this
    operation correct for rotated ellipses while retaining finite-segment
    semantics used by the other trim boundaries.
    """
    start, end = _line_point(line, "start"), _line_point(line, "end")
    center = _point(ellipse.get("center"))
    major = _point(ellipse.get("major_axis"))
    semi_major = math.hypot(major["x"], major["y"])
    ratio = float(ellipse.get("ratio", 0))
    if semi_major <= tolerance or not math.isfinite(ratio) or ratio <= tolerance:
        raise ValueError("TRIM_INVALID_ELLIPSE")
    semi_minor = semi_major * ratio
    ux, uy = major["x"] / semi_major, major["y"] / semi_major
    vx, vy = -uy, ux
    dx, dy = end["x"] - start["x"], end["y"] - start["y"]
    px, py = start["x"] - center["x"], start["y"] - center["y"]
    local_p = (px * ux + py * uy, px * vx + py * vy)
    local_d = (dx * ux + dy * uy, dx * vx + dy * vy)
    aa = (local_d[0] / semi_major) ** 2 + (local_d[1] / semi_minor) ** 2
    bb = 2.0 * ((local_p[0] * local_d[0]) / (semi_major * semi_major) +
                (local_p[1] * local_d[1]) / (semi_minor * semi_minor))
    cc = (local_p[0] / semi_major) ** 2 + (local_p[1] / semi_minor) ** 2 - 1.0
    if aa <= tolerance * tolerance:
        return []
    discriminant = bb * bb - 4.0 * aa * cc
    if discriminant < -tolerance:
        return []
    roots = [(-bb - math.sqrt(max(0.0, discriminant))) / (2.0 * aa)]
    if discriminant > tolerance:
        roots.append((-bb + math.sqrt(discriminant)) / (2.0 * aa))
    output: list[dict[str, float]] = []
    for factor in sorted(roots):
        if not finite_line or -tolerance <= factor <= 1.0 + tolerance:
            if finite_line:
                factor = min(1.0, max(0.0, factor))
            point = {"x": start["x"] + factor * dx, "y": start["y"] + factor * dy,
                     "z": start["z"] + factor * (end["z"] - start["z"])}
            if not output or _distance(output[-1], point) > tolerance:
                output.append(point)
    return output


def _filter_ellipse_intersections(points: list[dict[str, float]], ellipse: dict[str, Any], tolerance: float = 1e-7) -> list[dict[str, float]]:
    center = _point(ellipse.get("center"))
    major = _point(ellipse.get("major_axis"))
    semi_major = math.hypot(major["x"], major["y"])
    ratio = float(ellipse.get("ratio", 0))
    if semi_major <= tolerance or not math.isfinite(ratio) or ratio <= tolerance:
        raise ValueError("TRIM_INVALID_ELLIPSE")
    ux, uy = major["x"] / semi_major, major["y"] / semi_major
    vx, vy = -uy, ux
    start = math.degrees(float(ellipse.get("start_param", 0)))
    end = math.degrees(float(ellipse.get("end_param", 2 * math.pi)))
    full_sweep = abs(end - start) >= 360.0 - tolerance
    output = []
    for point in points:
        dx, dy = point["x"] - center["x"], point["y"] - center["y"]
        local_x, local_y = dx * ux + dy * uy, dx * vx + dy * vy
        parameter = math.degrees(math.atan2(local_y / (semi_major * ratio), local_x / semi_major))
        if full_sweep or _angle_on_sweep(parameter, start, end, tolerance):
            output.append(point)
    return output


def _line_spline_intersections(line: dict[str, Any], spline: dict[str, Any], tolerance: float = 1e-7,
                               *, finite_line: bool = True) -> list[dict[str, float]]:
    """Find finite line intersections with a DXF spline using adaptive chords.

    The spline is evaluated by ezdxf's B-spline implementation. Chord hits are
    refined in parameter space, and a conservative sampling limit keeps a
    malformed drawing from turning a trim request into an unbounded loop.
    """
    fit_points = spline.get("fit_points") or []
    control_points = spline.get("control_points") or []
    degree = int(spline.get("degree", 3))
    if len(fit_points) < 2 and len(control_points) < 2:
        raise ValueError("TRIM_INVALID_SPLINE")
    try:
        from ezdxf.math import BSpline
        def vector_values(items):
            return [(float(item.get("x", 0)), float(item.get("y", 0)), float(item.get("z", 0)))
                    if isinstance(item, dict) else tuple(item) for item in items]
        if control_points:
            knots = spline.get("knots") or None
            weights = spline.get("weights") or None
            curve = BSpline(vector_values(control_points), order=degree + 1, knots=knots, weights=weights)
        else:
            curve = BSpline.from_fit_points(vector_values(fit_points), degree=max(1, min(degree, len(fit_points) - 1)))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("TRIM_INVALID_SPLINE") from exc
    try:
        params = [float(value) for value in curve.approximation_params()]
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("TRIM_INVALID_SPLINE") from exc
    if len(params) < 2 or any(not math.isfinite(value) for value in params):
        raise ValueError("TRIM_INVALID_SPLINE")
    dense: list[float] = []
    subdivisions = 32
    for first, second in zip(params, params[1:]):
        if second <= first:
            continue
        dense.extend(first + (second - first) * index / subdivisions for index in range(subdivisions))
    dense.append(params[-1])
    try:
        points = list(curve.points(dense))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError("TRIM_INVALID_SPLINE") from exc
    intersections: list[dict[str, float]] = []
    for index, (first, second) in enumerate(zip(points, points[1:])):
        segment = {"start": {"x": float(first.x), "y": float(first.y), "z": float(first.z)},
                   "end": {"x": float(second.x), "y": float(second.y), "z": float(second.z)}}
        hit = (_segment_intersection(line, segment, tolerance) if finite_line
               else _infinite_line_segment_intersection(line, segment, tolerance))
        if hit is None:
            continue
        t0, t1 = dense[index], dense[index + 1]
        start, end = _line_point(line, "start"), _line_point(line, "end")
        dx, dy = end["x"] - start["x"], end["y"] - start["y"]
        def signed_distance(parameter: float) -> float:
            point = curve.point(parameter)
            return (float(point.x) - start["x"]) * dy - (float(point.y) - start["y"]) * dx
        f0, f1 = signed_distance(t0), signed_distance(t1)
        if abs(f0) <= tolerance:
            refined = t0
        elif abs(f1) <= tolerance:
            refined = t1
        elif f0 * f1 < 0:
            low, high = t0, t1
            for _ in range(48):
                middle = (low + high) / 2.0
                fm = signed_distance(middle)
                if abs(fm) <= tolerance:
                    low = high = middle
                    break
                if f0 * fm <= 0:
                    high, f1 = middle, fm
                else:
                    low, f0 = middle, fm
            refined = (low + high) / 2.0
        else:
            refined = (t0 + t1) / 2.0
        point = curve.point(refined)
        result = {"x": float(point.x), "y": float(point.y), "z": float(point.z)}
        if not any(_distance(result, existing) <= tolerance * 10 for existing in intersections):
            intersections.append(result)
    return intersections


def _line_parameter(point: dict[str, Any], line: dict[str, Any], tolerance: float = 1e-12) -> float:
    """Return the normalized parameter of a point on a LINE's XY support."""
    start, end = _line_point(line, "start"), _line_point(line, "end")
    dx, dy = end["x"] - start["x"], end["y"] - start["y"]
    denominator = dx * dx + dy * dy
    if denominator <= tolerance * tolerance:
        raise ValueError("EXTEND_ZERO_LENGTH_TARGET")
    return ((float(point.get("x", 0)) - start["x"]) * dx
            + (float(point.get("y", 0)) - start["y"]) * dy) / denominator


def _line_boundary_intersections(line: dict[str, Any], boundary: dict[str, Any],
                                 tolerance: float = 1e-9) -> list[dict[str, float]]:
    """Intersect an infinite LINE with a finite supported CAD boundary."""
    kind = str(boundary.get("type", "")).upper()
    if kind == "LINE":
        point = _infinite_line_segment_intersection(line, boundary, tolerance)
        return [point] if point is not None else []
    if kind in {"CIRCLE", "ARC"}:
        points = _line_circle_intersections(line, boundary, tolerance, finite_line=False)
        return _filter_arc_intersections(points, boundary, tolerance) if kind == "ARC" else points
    if kind == "ELLIPSE":
        points = _line_ellipse_intersections(line, boundary, tolerance, finite_line=False)
        return _filter_ellipse_intersections(points, boundary, tolerance)
    if kind == "SPLINE":
        return _line_spline_intersections(line, boundary, tolerance, finite_line=False)
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        return _line_polyline_intersections(line, boundary, tolerance, finite_line=False)
    raise ValueError("EXTEND_BOUNDARY_UNSUPPORTED:" + kind)


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
    if abs(end - start) >= 360.0 - tolerance:
        return list(points)
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


def _finite_cad_number(value: Any, code: str) -> float:
    if isinstance(value, bool):
        raise ValueError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(code) from exc
    if not math.isfinite(number):
        raise ValueError(code)
    return number


def _rectangle_vertices(arguments: dict[str, Any]) -> list[dict[str, float]]:
    width = _finite_cad_number(arguments.get("width"), "RECTANGLE_WIDTH_INVALID")
    height = _finite_cad_number(arguments.get("height"), "RECTANGLE_HEIGHT_INVALID")
    if width <= 0 or height <= 0:
        raise ValueError("RECTANGLE_SIZE_INVALID")
    origin = _point(arguments.get("origin", arguments.get("corner1")))
    angle = math.radians(_finite_cad_number(arguments.get("rotation", 0), "RECTANGLE_ROTATION_INVALID"))
    cosine, sine = math.cos(angle), math.sin(angle)
    local = ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
    return [{"x": origin["x"] + x * cosine - y * sine,
             "y": origin["y"] + x * sine + y * cosine,
             "z": origin["z"]} for x, y in local]


def _regular_polygon_vertices(arguments: dict[str, Any]) -> list[dict[str, float]]:
    center = _point(arguments.get("center"))
    radius = _finite_cad_number(arguments.get("radius"), "POLYGON_RADIUS_INVALID")
    if radius <= 0:
        raise ValueError("POLYGON_RADIUS_INVALID")
    sides_value = arguments.get("sides")
    if isinstance(sides_value, bool):
        raise ValueError("POLYGON_SIDES_INVALID")
    try:
        sides = int(sides_value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("POLYGON_SIDES_INVALID") from exc
    if sides < 3 or float(sides) != float(sides_value):
        raise ValueError("POLYGON_SIDES_INVALID")
    start = math.radians(_finite_cad_number(arguments.get("rotation", 0), "POLYGON_ROTATION_INVALID"))
    return [{"x": center["x"] + radius * math.cos(start + 2 * math.pi * index / sides),
             "y": center["y"] + radius * math.sin(start + 2 * math.pi * index / sides),
             "z": center["z"]} for index in range(sides)]


def _unit_vector(value: Any) -> dict[str, float]:
    vector = _point(value)
    length = math.sqrt(vector["x"] ** 2 + vector["y"] ** 2 + vector["z"] ** 2)
    if not math.isfinite(length) or length <= 1e-12:
        raise ValueError("CONSTRUCTION_DIRECTION_INVALID")
    return {axis: vector[axis] / length for axis in ("x", "y", "z")}


def _block_attribute_world_point(block: Block, insert: dict[str, Any], local: dict[str, Any]) -> dict[str, float]:
    base = _point(block.base_point)
    origin = _point(insert.get("insert"))
    scale = insert.get("scale") or {}
    sx, sy, sz = (float(scale.get(axis, 1)) for axis in ("x", "y", "z"))
    angle = math.radians(float(insert.get("rotation", 0)))
    value = _point(local)
    x, y = (value["x"] - base["x"]) * sx, (value["y"] - base["y"]) * sy
    return {"x": origin["x"] + x * math.cos(angle) - y * math.sin(angle),
            "y": origin["y"] + x * math.sin(angle) + y * math.cos(angle),
            "z": origin["z"] + (value["z"] - base["z"]) * sz}


def _inside_window(point: dict[str, Any], first: dict[str, Any], second: dict[str, Any], tolerance: float = 1e-9) -> bool:
    return (min(first["x"], second["x"]) - tolerance <= float(point.get("x", 0)) <= max(first["x"], second["x"]) + tolerance
            and min(first["y"], second["y"]) - tolerance <= float(point.get("y", 0)) <= max(first["y"], second["y"]) + tolerance)


def _shift_point(point: dict[str, Any], delta: dict[str, float]) -> dict[str, float]:
    value = _point(point)
    return {"x": value["x"] + delta["x"], "y": value["y"] + delta["y"], "z": value["z"] + delta["z"]}


def _dimension_remeasure(entity: dict[str, Any]) -> None:
    if str(entity.get("type", "")).upper() != "DIMENSION":
        return
    kind = str(entity.get("dimension_type", "linear")).lower()
    if kind in {"linear", "aligned"} and entity.get("start") is not None and entity.get("end") is not None:
        start, end = _point(entity.get("start")), _point(entity.get("end"))
        entity["measurement"] = math.dist((start["x"], start["y"], start["z"]), (end["x"], end["y"], end["z"]))
    elif kind in {"radial", "diameter"} and entity.get("center") is not None:
        center = _point(entity.get("center")); point = _point(entity.get("end") or entity.get("start"))
        entity["measurement"] = math.dist((center["x"], center["y"], center["z"]), (point["x"], point["y"], point["z"]))
    elif kind == "ordinate" and entity.get("start") is not None:
        point = _point(entity.get("start"))
        entity["measurement"] = point["x"] if str(entity.get("axis", "x")).lower() == "x" else point["y"]


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
        if name == "create_text_style":
            style_name = str(arguments.get("name") or arguments.get("style_name") or "").strip()
            if not style_name:
                raise ValueError("TEXT_STYLE_NAME_REQUIRED")
            if len(style_name) > 255:
                raise ValueError("TEXT_STYLE_NAME_TOO_LONG")
            if any(char in style_name for char in "\\/*?:;\",="):
                raise ValueError("TEXT_STYLE_NAME_INVALID")
            mode = str(arguments.get("if_exists", "error")).lower()
            if mode not in {"error", "update", "replace"}:
                raise ValueError("TEXT_STYLE_IF_EXISTS_INVALID")
            existing = document.text_styles.get(style_name)
            if existing is not None and mode == "error":
                raise ValueError("TEXT_STYLE_EXISTS:" + style_name)
            values = copy.deepcopy(existing or {"name": style_name})
            for key in ("font", "bigfont"):
                if key in arguments:
                    value = str(arguments[key]).strip()
                    if not value:
                        raise ValueError("TEXT_STYLE_FONT_INVALID")
                    values[key] = value
            for key in ("height", "width", "oblique"):
                if key in arguments:
                    try:
                        value = float(arguments[key])
                    except (TypeError, ValueError) as exc:
                        raise ValueError("TEXT_STYLE_" + key.upper() + "_INVALID") from exc
                    if not math.isfinite(value) or (key == "height" and value < 0) or (key == "width" and value <= 0):
                        raise ValueError("TEXT_STYLE_" + key.upper() + "_INVALID")
                    values[key] = value
            if "flags" in arguments:
                try:
                    flags = int(arguments["flags"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("TEXT_STYLE_FLAGS_INVALID") from exc
                if flags < 0:
                    raise ValueError("TEXT_STYLE_FLAGS_INVALID")
                values["flags"] = flags
            values.setdefault("font", "txt")
            values.setdefault("height", 0.0)
            values.setdefault("width", 1.0)
            values.setdefault("oblique", 0.0)
            values.setdefault("flags", 0)
            values["name"] = style_name
            document.text_styles[style_name] = values
            return {"text_style": copy.deepcopy(values), "changed": existing != values, "if_exists": mode}
        if name == "set_annotation_scale":
            raw_scale = arguments.get("scale")
            if isinstance(raw_scale, bool):
                raise ValueError("ANNOTATION_SCALE_INVALID")
            try:
                scale = float(raw_scale)
            except (TypeError, ValueError) as exc:
                raise ValueError("ANNOTATION_SCALE_INVALID") from exc
            if not math.isfinite(scale) or scale <= 0:
                raise ValueError("ANNOTATION_SCALE_INVALID")
            raw_previous = document.metadata.get("annotation_scale", 1.0)
            try:
                previous = float(raw_previous)
            except (TypeError, ValueError) as exc:
                raise ValueError("ANNOTATION_SCALE_INVALID") from exc
            if not math.isfinite(previous) or previous <= 0:
                raise ValueError("ANNOTATION_SCALE_INVALID")
            factor = scale / previous
            selected = _refs(arguments, document)
            if selected:
                selected_set = set(selected)
                entities = [entity for entity in document.entities if str(entity.get("id")) in selected_set]
                if len(entities) != len(selected_set):
                    missing = sorted(selected_set - {str(entity.get("id")) for entity in entities})
                    raise ValueError("ENTITY_NOT_FOUND:" + ",".join(missing))
            else:
                entities = list(document.entities)
            requested_layout = str(arguments.get("layout", "")).strip()
            include_dimensions = bool(arguments.get("include_dimensions", True))
            include_leaders = bool(arguments.get("include_leaders", True))
            annotation_kinds = {"TEXT", "MTEXT", "ATTDEF", "ATTRIB"}
            if include_dimensions:
                annotation_kinds.add("DIMENSION")
            if include_leaders:
                annotation_kinds.add("LEADER")
            changed: list[str] = []
            for entity in entities:
                kind = str(entity.get("type", "")).upper()
                if kind not in annotation_kinds and not any(key in entity for key in ("standard_symbol", "gdt", "surface_texture", "bom", "bom_balloon")):
                    if selected:
                        raise ValueError("ANNOTATION_ENTITY_UNSUPPORTED:" + kind)
                    continue
                entity_layout = str(entity.get("sheet", entity.get("layout", "Model")))
                if requested_layout and requested_layout.lower() not in {"auto", entity_layout.lower()}:
                    continue
                layer = document.layers.get(str(entity.get("layer", "0")))
                if layer and layer.locked:
                    raise ValueError("CAD_LAYER_LOCKED:" + layer.name)
                updated = copy.deepcopy(entity)
                if kind in {"TEXT", "MTEXT", "ATTDEF", "ATTRIB"}:
                    if "height" in updated:
                        updated["height"] = float(updated.get("height", 0)) * factor
                    if kind == "MTEXT" and "width" in updated and float(updated.get("width", 0)) > 0:
                        updated["width"] = float(updated.get("width", 0)) * factor
                if kind in {"DIMENSION", "LEADER"} or any(key in updated for key in ("standard_symbol", "gdt", "surface_texture", "bom", "bom_balloon")):
                    updated["annotation_scale"] = scale
                if updated != entity:
                    document.entities[document.entities.index(entity)] = updated
                    changed.append(str(updated.get("id")))
            document.metadata["annotation_scale"] = scale
            document.metadata["annotation_scale_updated_by"] = "cad.set_annotation_scale"
            return {"scale": scale, "previous_scale": previous, "factor": factor,
                    "entities": changed, "changed": bool(changed) or not math.isclose(scale, previous)}
        if name in {"document_open", "document_inspect"}:
            return {"document": document.to_dict()}
        if name == "donut_create":
            center = _point(arguments.get("center"))
            inner = _finite_cad_number(arguments.get("inner_radius"), "DONUT_INNER_RADIUS_INVALID")
            outer = _finite_cad_number(arguments.get("outer_radius"), "DONUT_OUTER_RADIUS_INVALID")
            if inner <= 0 or outer <= 0 or outer <= inner:
                raise ValueError("DONUT_RADII_INVALID")
            layer_name = str(arguments.get("layer", "0"))
            layer = document.ensure_layer(layer_name)
            if layer.locked:
                raise ValueError("CAD_LAYER_LOCKED:" + layer_name)
            donut_id = document.next_id("donut")
            outer_entity = {"id": document.next_id("donut-outer"), "type": "CIRCLE", "layer": layer_name,
                            "center": center, "radius": outer, "donut_id": donut_id, "donut_role": "outer"}
            inner_entity = {"id": document.next_id("donut-inner"), "type": "CIRCLE", "layer": layer_name,
                            "center": center, "radius": inner, "donut_id": donut_id, "donut_role": "inner"}
            document.entities.extend((outer_entity, inner_entity))
            return {"entities": [outer_entity, inner_entity], "donut_id": donut_id,
                    "inner_radius": inner, "outer_radius": outer, "representation": "concentric_circles"}
        convenience = {
            "line_create": lambda: ("LINE", {"start": _point(arguments.get("start")), "end": _point(arguments.get("end"))}),
            "ray_create": lambda: ("RAY", {"start": _point(arguments.get("start")), "unit_vector": _unit_vector(arguments.get("direction"))}),
            "xline_create": lambda: ("XLINE", {"start": _point(arguments.get("start")), "unit_vector": _unit_vector(arguments.get("direction"))}),
            "circle_create": lambda: ("CIRCLE", {"center": _point(arguments.get("center")), "radius": arguments.get("radius")}),
            "arc_create": lambda: ("ARC", {"center": _point(arguments.get("center")), "radius": arguments.get("radius"), "start_angle": arguments.get("start_angle", 0), "end_angle": arguments.get("end_angle", 360)}),
            "polyline_create": lambda: ("LWPOLYLINE", {"vertices": [_point(item) for item in arguments.get("vertices", [])], "closed": bool(arguments.get("closed", False))}),
            "rectangle_create": lambda: ("LWPOLYLINE", {"vertices": _rectangle_vertices(arguments), "closed": True, "shape": "rectangle"}),
            "polygon_create": lambda: ("LWPOLYLINE", {"vertices": _regular_polygon_vertices(arguments), "closed": True, "shape": "regular_polygon", "sides": int(arguments.get("sides"))}),
            "spline_create": lambda: ("SPLINE", {"fit_points": [_point(item) for item in arguments.get("fit_points", arguments.get("points", []))], "degree": int(arguments.get("degree", 3))}),
            "point_create": lambda: ("POINT", {"point": arguments.get("point")}),
            "text_create": lambda: ("TEXT", {"insert": _point(arguments.get("insert")), "text": arguments.get("text", ""), "height": arguments.get("height", 1), "rotation": arguments.get("rotation", 0)}),
            "insert_create": lambda: ("INSERT", {"block_name": arguments.get("block_name", ""), "insert": _point(arguments.get("insert")), "scale": arguments.get("scale", {"x": 1, "y": 1, "z": 1}), "rotation": arguments.get("rotation", 0)}),
        }
        if name in convenience:
            entity_type, values = convenience[name]()
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
        if name == "draw_order":
            requested_ids = _refs(arguments, document)
            if not requested_ids:
                raise ValueError("DRAW_ORDER_SELECTION_REQUIRED")
            selected_ids = list(dict.fromkeys(requested_ids))
            selected = {str(identifier) for identifier in selected_ids}
            for identifier in selected_ids:
                document.entity(identifier)
            action = str(arguments.get("action", arguments.get("mode", ""))).strip().lower()
            aliases = {
                "bring_to_front": "front", "send_to_back": "back",
                "move_forward": "forward", "move_backward": "backward",
                "above_entity": "above", "below_entity": "below",
            }
            action = aliases.get(action, action)
            if action not in {"front", "back", "forward", "backward", "above", "below"}:
                raise ValueError("DRAW_ORDER_ACTION_INVALID")
            try:
                steps = int(arguments.get("steps", 1))
            except (TypeError, ValueError) as exc:
                raise ValueError("DRAW_ORDER_STEPS_INVALID") from exc
            if steps < 1:
                raise ValueError("DRAW_ORDER_STEPS_INVALID")
            layout_filter = str(arguments.get("layout", "")).strip()
            reference_id = arguments.get("relative_to") or arguments.get("reference_entity_id")
            reference = None
            if action in {"above", "below"}:
                if not reference_id:
                    raise ValueError("DRAW_ORDER_REFERENCE_REQUIRED")
                reference = document.entity(str(reference_id))
                if str(reference.get("id")) in selected:
                    raise ValueError("DRAW_ORDER_REFERENCE_SELECTED")
            if reference is not None:
                reference_layout = str(reference.get("sheet", reference.get("layout", "Model")))
                if layout_filter and layout_filter.lower() not in {"auto", reference_layout.lower()}:
                    raise ValueError("DRAW_ORDER_LAYOUT_MISMATCH")
            for identifier in selected_ids:
                entity = document.entity(identifier)
                layer = document.layers.get(str(entity.get("layer", "0")))
                if layer and layer.locked:
                    raise ValueError("CAD_LAYER_LOCKED:" + layer.name)
                entity_layout = str(entity.get("sheet", entity.get("layout", "Model")))
                if layout_filter and layout_filter.lower() not in {"auto", entity_layout.lower()}:
                    raise ValueError("DRAW_ORDER_LAYOUT_MISMATCH")
                if reference is not None and entity_layout.lower() != reference_layout.lower():
                    raise ValueError("DRAW_ORDER_LAYOUT_MISMATCH")

            def reorder(items: list[dict[str, Any]], *, target: dict[str, Any] | None = None) -> list[dict[str, Any]]:
                ids = [str(item.get("id")) for item in items]
                if not selected.intersection(ids):
                    return items
                chosen = {identifier for identifier in selected if identifier in ids}
                if action == "front":
                    return [item for item in items if str(item.get("id")) not in chosen] + [item for item in items if str(item.get("id")) in chosen]
                if action == "back":
                    return [item for item in items if str(item.get("id")) in chosen] + [item for item in items if str(item.get("id")) not in chosen]
                if action in {"forward", "backward"}:
                    output = list(items)
                    for _ in range(steps):
                        if action == "forward":
                            for index in range(len(output) - 2, -1, -1):
                                if str(output[index].get("id")) in chosen and str(output[index + 1].get("id")) not in chosen:
                                    output[index], output[index + 1] = output[index + 1], output[index]
                        else:
                            for index in range(1, len(output)):
                                if str(output[index].get("id")) in chosen and str(output[index - 1].get("id")) not in chosen:
                                    output[index], output[index - 1] = output[index - 1], output[index]
                    return output
                target_id = str(target.get("id")) if target else ""
                remaining = [item for item in items if str(item.get("id")) not in chosen]
                target_index = next((index for index, item in enumerate(remaining) if str(item.get("id")) == target_id), None)
                if target_index is None:
                    raise ValueError("DRAW_ORDER_REFERENCE_NOT_FOUND")
                insert_at = target_index + (1 if action == "above" else 0)
                return remaining[:insert_at] + [item for item in items if str(item.get("id")) in chosen] + remaining[insert_at:]

            before_order = [str(item.get("id")) for item in document.entities]
            layouts: dict[str, list[int]] = {}
            selected_layouts = {
                str(document.entity(identifier).get("sheet", document.entity(identifier).get("layout", "Model")))
                for identifier in selected_ids
            }
            for index, entity in enumerate(document.entities):
                layout = str(entity.get("sheet", entity.get("layout", "Model")))
                if layout_filter and layout_filter.lower() not in {"auto", layout.lower()}:
                    continue
                is_reference_layout = reference is not None and layout.lower() == reference_layout.lower()
                if layout in selected_layouts or (action in {"above", "below"} and is_reference_layout):
                    layouts.setdefault(layout, []).append(index)
            for layout, indices in layouts.items():
                segment = [document.entities[index] for index in indices]
                target = reference if reference is not None and layout.lower() == reference_layout.lower() else None
                reordered = reorder(segment, target=target)
                for index, entity in zip(indices, reordered):
                    document.entities[index] = entity
            after_order = [str(item.get("id")) for item in document.entities]
            affected_layouts = sorted({str(document.entity(identifier).get("sheet", document.entity(identifier).get("layout", "Model"))) for identifier in selected_ids})
            return {"action": action, "entity_ids": selected_ids, "layouts": affected_layouts,
                    "changed": before_order != after_order, "order_before": before_order,
                    "order_after": after_order}
        if name == "entity_stretch":
            window = arguments.get("window")
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                raise ValueError("STRETCH_WINDOW_REQUIRED")
            first, second = _point(window[0]), _point(window[1])
            delta_value = arguments.get("delta")
            if delta_value is None:
                raise ValueError("STRETCH_DELTA_REQUIRED")
            delta = _point(delta_value)
            layout = str(arguments.get("layout", "")).strip()
            changed: list[str] = []
            moved_points = 0
            for entity in list(document.entities):
                entity_layout = str(entity.get("sheet", entity.get("layout", "Model")))
                if layout and layout.lower() not in {"auto", entity_layout.lower()}:
                    continue
                kind = str(entity.get("type", "")).upper()
                points = entity_points(entity)
                updated = copy.deepcopy(entity)
                moved = 0
                if points and all(_inside_window(point, first, second) for point in points):
                    layer = document.layers.get(str(entity.get("layer", "0")))
                    if layer and layer.locked:
                        raise ValueError("CAD_LAYER_LOCKED:" + layer.name)
                    updated = transform_entity(entity, dx=delta["x"], dy=delta["y"], dz=delta["z"], allow_associative=True)
                    moved = len(points)
                elif kind == "LINE":
                    for key in ("start", "end"):
                        if _inside_window(entity.get(key) or {}, first, second):
                            updated[key] = _shift_point(entity.get(key), delta); moved += 1
                elif kind in {"LWPOLYLINE", "POLYLINE"}:
                    updated["vertices"] = []
                    for point in entity.get("vertices", []):
                        if _inside_window(point, first, second):
                            updated["vertices"].append(_shift_point(point, delta)); moved += 1
                        else:
                            updated["vertices"].append(copy.deepcopy(point))
                elif kind == "LEADER":
                    updated["vertices"] = []
                    for point in entity.get("vertices", []):
                        if _inside_window(point, first, second):
                            updated["vertices"].append(_shift_point(point, delta)); moved += 1
                        else:
                            updated["vertices"].append(copy.deepcopy(point))
                elif kind == "SPLINE":
                    for key in ("fit_points", "control_points"):
                        values = updated.get(key)
                        if values is None:
                            continue
                        for index, point in enumerate(values):
                            if _inside_window(point, first, second):
                                values[index] = _shift_point(point, delta); moved += 1
                elif kind == "HATCH":
                    if entity.get("associative") and any(_inside_window(point, first, second) for point in points):
                        raise ValueError("STRETCH_ASSOCIATIVE_HATCH_PARTIAL_UNSUPPORTED")
                    for key in ("boundary_vertices",):
                        values = updated.get(key)
                        if values is not None:
                            for index, point in enumerate(values):
                                if _inside_window(point, first, second):
                                    values[index] = _shift_point(point, delta); moved += 1
                    for path in updated.get("boundary_paths", []):
                        values = path.get("vertices") or []
                        for index, point in enumerate(values):
                            if _inside_window(point, first, second):
                                values[index] = _shift_point(point, delta); moved += 1
                elif kind == "DIMENSION":
                    for key in ("definition", "start", "end", "center"):
                        if key in updated and isinstance(updated.get(key), dict) and _inside_window(updated[key], first, second):
                            updated[key] = _shift_point(updated[key], delta); moved += 1
                    _dimension_remeasure(updated)
                else:
                    anchor = entity.get("center") or entity.get("insert") or entity.get("point") or entity.get("definition")
                    if isinstance(anchor, dict) and _inside_window(anchor, first, second):
                        layer = document.layers.get(str(entity.get("layer", "0")))
                        if layer and layer.locked:
                            raise ValueError("CAD_LAYER_LOCKED:" + layer.name)
                        updated = transform_entity(entity, dx=delta["x"], dy=delta["y"], dz=delta["z"], allow_associative=True)
                        moved = 1
                if moved:
                    layer = document.layers.get(str(entity.get("layer", "0")))
                    if layer and layer.locked:
                        raise ValueError("CAD_LAYER_LOCKED:" + layer.name)
                    document.entities[document.entities.index(entity)] = updated
                    changed.append(str(entity.get("id")))
                    moved_points += moved
            return {"entities": changed, "count": len(changed), "moved_points": moved_points,
                    "window": [first, second], "delta": delta}
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
        if name == "entity_remove_duplicates":
            tolerance_value = arguments.get("tolerance", 1e-7)
            if isinstance(tolerance_value, bool):
                raise ValueError("DUPLICATE_TOLERANCE_INVALID")
            try:
                tolerance = float(tolerance_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("DUPLICATE_TOLERANCE_INVALID") from exc
            if not math.isfinite(tolerance) or tolerance <= 0:
                raise ValueError("DUPLICATE_TOLERANCE_INVALID")
            ids = _refs(arguments, document)
            if not ids:
                ids = [str(item.get("id")) for item in document.entities]
            seen: dict[str, str] = {}
            duplicate_to_keep: dict[str, str] = {}
            groups: dict[str, list[str]] = {}
            for entity_id in ids:
                entity = document.entity(entity_id)
                key = _duplicate_key(entity, tolerance)
                groups.setdefault(key, []).append(entity_id)
                if key in seen:
                    duplicate_to_keep[entity_id] = seen[key]
                else:
                    seen[key] = entity_id
            if not duplicate_to_keep:
                return {"removed": [], "groups": [], "count": 0, "tolerance": tolerance}
            by_id = {str(item.get("id")): item for item in document.entities}
            handle_map: dict[str, str] = {}
            for duplicate_id, keep_id in duplicate_to_keep.items():
                duplicate, keep = by_id[duplicate_id], by_id[keep_id]
                if duplicate.get("handle") and keep.get("handle"):
                    handle_map[str(duplicate["handle"])] = str(keep["handle"])
            if handle_map:
                for entity in document.entities:
                    if str(entity.get("type", "")).upper() != "HATCH":
                        continue
                    for path in entity.get("boundary_paths", []):
                        path["source_handles"] = [handle_map.get(str(handle), str(handle)) for handle in path.get("source_handles", [])]
            removed_ids = set(duplicate_to_keep)
            document.entities = [item for item in document.entities if str(item.get("id")) not in removed_ids]
            for selection in document.selection_sets.values():
                selection.refs = [duplicate_to_keep.get(ref, ref) for ref in selection.refs]
                selection.refs = list(dict.fromkeys(selection.refs))
            for constraint in document.constraints:
                constraint.refs = [duplicate_to_keep.get(ref, ref) for ref in constraint.refs]
                constraint.refs = list(dict.fromkeys(constraint.refs))
            duplicate_groups = []
            for key, group in groups.items():
                if len(group) > 1:
                    duplicate_groups.append({"kept": group[0], "removed": group[1:]})
            return {"removed": sorted(removed_ids), "groups": duplicate_groups,
                    "count": len(removed_ids), "tolerance": tolerance}
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
        if name == "change_space":
            target_space = str(arguments.get("space", "")).lower()
            if target_space not in {"model", "paper"}:
                raise ValueError("CAD_SPACE_INVALID")
            selected_ids = _refs(arguments, document)
            if not selected_ids:
                raise ValueError("ENTITY_SELECTION_REQUIRED")
            selected = [document.entity(identifier) for identifier in selected_ids]
            sheet_name = None
            if target_space == "paper":
                sheet_name = str(arguments.get("sheet", "")).strip()
                if not sheet_name or sheet_name not in document.sheets:
                    raise ValueError("SHEET_REQUIRED")
            changed = []
            for entity in selected:
                old_space = "paper" if entity.get("sheet") else "model"
                if old_space == target_space and (target_space == "model" or entity.get("sheet") == sheet_name):
                    continue
                if target_space == "paper":
                    entity["sheet"] = sheet_name
                    entity.pop("layout", None)
                else:
                    entity.pop("sheet", None)
                    entity.pop("layout", None)
                changed.append(str(entity.get("id")))
            return {"space": target_space, "sheet": sheet_name, "entities": changed, "changed": bool(changed)}
        if name == "sheet_prepare":
            from .drawing_sheets import prepare_sheet
            return prepare_sheet(document, arguments)
        if name == "set_page_setup":
            sheet_name = str(arguments.get("sheet", "")).strip()
            if sheet_name not in document.sheets:
                raise ValueError("SHEET_NOT_FOUND:" + sheet_name)
            raw = arguments.get("setup")
            if not isinstance(raw, dict):
                raise ValueError("PAGE_SETUP_REQUIRED")
            setup = copy.deepcopy(raw)
            paper_sizes = {
                "A0": (841.0, 1189.0), "A1": (594.0, 841.0), "A2": (420.0, 594.0),
                "A3": (297.0, 420.0), "A4": (210.0, 297.0),
            }
            paper = str(setup.get("paper", "")).upper()
            orientation = str(setup.get("orientation", "landscape")).lower()
            if orientation not in {"landscape", "portrait"}:
                raise ValueError("PAGE_ORIENTATION_INVALID")
            if paper:
                if paper not in paper_sizes:
                    raise ValueError("PAGE_PAPER_INVALID")
                width, height = paper_sizes[paper]
                if orientation == "landscape":
                    width, height = max(width, height), min(width, height)
                else:
                    width, height = min(width, height), max(width, height)
                document.sheets[sheet_name]["width"], document.sheets[sheet_name]["height"] = width, height
                document.sheets[sheet_name]["units"] = "mm"
                setup["paper"] = paper
            else:
                width = _finite_cad_number(setup.get("width", document.sheets[sheet_name].get("width", 420)), "PAGE_WIDTH_INVALID")
                height = _finite_cad_number(setup.get("height", document.sheets[sheet_name].get("height", 297)), "PAGE_HEIGHT_INVALID")
                if width <= 0 or height <= 0:
                    raise ValueError("PAGE_SIZE_INVALID")
                document.sheets[sheet_name]["width"], document.sheets[sheet_name]["height"] = width, height
            margins = setup.get("margins", document.sheets[sheet_name].get("page_setup", {}).get("margins", [0, 0, 0, 0]))
            if isinstance(margins, dict):
                margins = [margins.get(key, 0) for key in ("left", "bottom", "right", "top")]
            if not isinstance(margins, (list, tuple)) or len(margins) != 4:
                raise ValueError("PAGE_MARGINS_INVALID")
            margins = [_finite_cad_number(value, "PAGE_MARGINS_INVALID") for value in margins]
            if any(value < 0 for value in margins):
                raise ValueError("PAGE_MARGINS_INVALID")
            setup["margins"] = margins
            setup["orientation"] = orientation
            if "offset" in setup:
                offset = setup["offset"]
                if not isinstance(offset, (list, tuple)) or len(offset) != 2:
                    raise ValueError("PAGE_OFFSET_INVALID")
                setup["offset"] = [_finite_cad_number(value, "PAGE_OFFSET_INVALID") for value in offset]
            if "scale" in setup:
                scale = setup["scale"]
                if isinstance(scale, (list, tuple)):
                    if len(scale) != 2:
                        raise ValueError("PAGE_SCALE_INVALID")
                    setup["scale"] = [_finite_cad_number(value, "PAGE_SCALE_INVALID") for value in scale]
                    if setup["scale"][0] <= 0 or setup["scale"][1] <= 0:
                        raise ValueError("PAGE_SCALE_INVALID")
                else:
                    setup["scale"] = _finite_cad_number(scale, "PAGE_SCALE_INVALID")
                    if setup["scale"] <= 0:
                        raise ValueError("PAGE_SCALE_INVALID")
            setup["managed"] = True
            document.sheets[sheet_name]["page_setup"] = setup
            return {"sheet": copy.deepcopy(document.sheets[sheet_name]), "page_setup": setup}
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
        if name == "trace_boundary":
            return _trace_boundary_entity(document, arguments)
        if name == "hole_create":
            center = _point(arguments.get("center"))
            kind = str(arguments.get("kind", "clearance")).lower()
            allowed_kinds = {"clearance", "tapped", "counterbored", "countersunk", "spotface"}
            if kind not in allowed_kinds:
                raise ValueError("HOLE_KIND_INVALID")
            try:
                diameter = float(arguments.get("diameter"))
            except (TypeError, ValueError) as exc:
                raise ValueError("HOLE_DIAMETER_REQUIRED") from exc
            if not math.isfinite(diameter) or diameter <= 0:
                raise ValueError("HOLE_DIAMETER_INVALID")
            outer_diameter = arguments.get("outer_diameter")
            if kind in {"counterbored", "countersunk", "spotface"}:
                if outer_diameter is None:
                    raise ValueError("HOLE_OUTER_DIAMETER_REQUIRED")
                try:
                    outer_diameter = float(outer_diameter)
                except (TypeError, ValueError) as exc:
                    raise ValueError("HOLE_OUTER_DIAMETER_INVALID") from exc
                if not math.isfinite(outer_diameter) or outer_diameter <= diameter:
                    raise ValueError("HOLE_OUTER_DIAMETER_INVALID")
            elif outer_diameter is not None:
                try:
                    outer_diameter = float(outer_diameter)
                except (TypeError, ValueError) as exc:
                    raise ValueError("HOLE_OUTER_DIAMETER_INVALID") from exc
                if not math.isfinite(outer_diameter) or outer_diameter <= diameter:
                    raise ValueError("HOLE_OUTER_DIAMETER_INVALID")
            minor_diameter = arguments.get("minor_diameter")
            if minor_diameter is not None:
                try:
                    minor_diameter = float(minor_diameter)
                except (TypeError, ValueError) as exc:
                    raise ValueError("HOLE_MINOR_DIAMETER_INVALID") from exc
                if not math.isfinite(minor_diameter) or minor_diameter <= 0 or minor_diameter >= diameter:
                    raise ValueError("HOLE_MINOR_DIAMETER_INVALID")
            countersink_angle = arguments.get("countersink_angle", 90.0)
            try:
                countersink_angle = float(countersink_angle)
            except (TypeError, ValueError) as exc:
                raise ValueError("HOLE_COUNTERSINK_ANGLE_INVALID") from exc
            if kind == "countersunk" and (not math.isfinite(countersink_angle) or not 0 < countersink_angle < 180):
                raise ValueError("HOLE_COUNTERSINK_ANGLE_INVALID")
            depth = arguments.get("depth")
            if depth is not None:
                try:
                    depth = float(depth)
                except (TypeError, ValueError) as exc:
                    raise ValueError("HOLE_DEPTH_INVALID") from exc
                if not math.isfinite(depth) or depth <= 0:
                    raise ValueError("HOLE_DEPTH_INVALID")
            layer = str(arguments.get("layer", "HOLES"))
            document.ensure_layer(layer)
            hole_id = document.next_id("hole")
            metadata = {
                "kind": kind, "diameter": diameter,
                "outer_diameter": outer_diameter, "minor_diameter": minor_diameter,
                "countersink_angle": countersink_angle if kind == "countersunk" else None,
                "depth": depth, "through": bool(arguments.get("through", depth is None)),
                "standard": str(arguments.get("standard", "")), "size": str(arguments.get("size", "")),
                "tag": str(arguments.get("tag", "")),
            }
            created = []
            def circle(role, radius, extra=None):
                entity = {"id": document.next_id("hole-circle"), "type": "CIRCLE", "layer": layer,
                          "center": copy.deepcopy(center), "radius": radius, "hole_id": hole_id,
                          "hole_role": role, "hole": copy.deepcopy(metadata)}
                if extra:
                    entity["hole"].update(extra)
                document.entities.append(entity); created.append(entity["id"])
            circle("through", diameter / 2)
            if minor_diameter is not None:
                circle("minor", minor_diameter / 2)
            if outer_diameter is not None:
                circle("counter", outer_diameter / 2)
            return {"entities": created, "hole_id": hole_id, "kind": kind, "diameter": diameter,
                    "outer_diameter": outer_diameter, "minor_diameter": minor_diameter,
                    "metadata": metadata}
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
                    elif boundary_kind in {"CIRCLE", "ARC", "ELLIPSE", "SPLINE"}:
                        if boundary_kind == "ELLIPSE":
                            intersections = _line_ellipse_intersections(entity, boundary)
                            intersections = _filter_ellipse_intersections(intersections, boundary)
                        elif boundary_kind == "SPLINE":
                            intersections = _line_spline_intersections(entity, boundary)
                        else:
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
        if name == "entity_extend":
            target_value = arguments.get("target") or arguments.get("target_entity_id") or arguments.get("entity_id")
            if isinstance(target_value, dict):
                target_value = target_value.get("entity_id") or target_value.get("id") or target_value.get("ref")
            target_id = str(target_value or "")
            if not target_id:
                raise ValueError("EXTEND_TARGET_REQUIRED")
            edge_values = arguments.get("edges")
            if edge_values is None:
                edge_values = arguments.get("boundary_entity_ids")
            if edge_values is None:
                edge_values = arguments.get("boundary_entity_id") or arguments.get("cutting_entity_id")
            if isinstance(edge_values, (str, dict)):
                edge_values = [edge_values]
            edge_ids = []
            for value in edge_values or []:
                if isinstance(value, dict):
                    value = value.get("entity_id") or value.get("id") or value.get("ref")
                if value:
                    edge_ids.append(str(value))
            if not edge_ids:
                raise ValueError("EXTEND_BOUNDARIES_REQUIRED")
            target = document.entity(target_id)
            if str(target.get("type", "")).upper() != "LINE":
                raise ValueError("EXTEND_ONLY_SUPPORTS_LINE")
            if target_id in edge_ids:
                raise ValueError("EXTEND_TARGET_CANNOT_BE_BOUNDARY")
            target_start, target_end = _line_point(target, "start"), _line_point(target, "end")
            if not all(math.isfinite(float(point[axis])) for point in (target_start, target_end) for axis in ("x", "y", "z")):
                raise ValueError("EXTEND_NON_FINITE_TARGET")
            target_length = math.hypot(target_end["x"] - target_start["x"], target_end["y"] - target_start["y"])
            if target_length <= 1e-12:
                raise ValueError("EXTEND_ZERO_LENGTH_TARGET")
            tolerance = arguments.get("tolerance", 1e-7)
            if isinstance(tolerance, bool):
                raise ValueError("EXTEND_INVALID_TOLERANCE")
            try:
                tolerance = float(tolerance)
            except (TypeError, ValueError) as exc:
                raise ValueError("EXTEND_INVALID_TOLERANCE") from exc
            if not math.isfinite(tolerance) or tolerance <= 0:
                raise ValueError("EXTEND_INVALID_TOLERANCE")
            target_layout = str(target.get("sheet", target.get("layout", "Model")))
            requested_layout = str(arguments.get("layout", "")).strip()
            if requested_layout and requested_layout.lower() not in {"auto", target_layout.lower()}:
                raise ValueError("EXTEND_LAYOUT_MISMATCH")
            target_layer = document.layers.get(str(target.get("layer", "0")))
            if target_layer and target_layer.locked:
                raise ValueError("CAD_LAYER_LOCKED:" + target_layer.name)
            hits: list[dict[str, Any]] = []
            for edge_id in edge_ids:
                edge = document.entity(edge_id)
                edge_layout = str(edge.get("sheet", edge.get("layout", "Model")))
                if edge_layout.lower() != target_layout.lower():
                    raise ValueError("EXTEND_DIFFERENT_LAYOUTS")
                for point in _line_boundary_intersections(target, edge, tolerance):
                    parameter = _line_parameter(point, target)
                    if not any(abs(parameter - item["parameter"]) <= tolerance
                               and _distance(point, item["point"]) <= tolerance for item in hits):
                        hits.append({"edge_id": edge_id, "point": point, "parameter": parameter})
            if not hits:
                raise ValueError("EXTEND_NO_INTERSECTION")
            end = str(arguments.get("end", arguments.get("side", "auto"))).lower()
            if end not in {"start", "end", "auto"}:
                raise ValueError("EXTEND_END_INVALID")
            outside = [item for item in hits
                       if (item["parameter"] < -tolerance if end == "start"
                           else item["parameter"] > 1.0 + tolerance if end == "end"
                           else item["parameter"] < -tolerance or item["parameter"] > 1.0 + tolerance)]
            if not outside:
                if any(abs(item["parameter"]) <= tolerance or abs(item["parameter"] - 1.0) <= tolerance for item in hits):
                    raise ValueError("EXTEND_ALREADY_AT_BOUNDARY")
                if any(-tolerance < item["parameter"] < 1.0 + tolerance for item in hits):
                    raise ValueError("EXTEND_INTERSECTION_INSIDE_SEGMENT")
                if end == "start":
                    raise ValueError("EXTEND_NO_START_INTERSECTION")
                if end == "end":
                    raise ValueError("EXTEND_NO_END_INTERSECTION")
                raise ValueError("EXTEND_NO_INTERSECTION")
            if end == "start":
                chosen = max(outside, key=lambda item: item["parameter"])
            elif end == "end":
                chosen = min(outside, key=lambda item: item["parameter"])
            else:
                chosen = min(outside, key=lambda item: (abs(item["parameter"]), 0 if item["parameter"] < 0 else 1))
            updated = copy.deepcopy(target)
            chosen_end = "start" if chosen["parameter"] < 0 else "end"
            updated[chosen_end] = chosen["point"]
            document.entities[document.entities.index(target)] = updated
            return {"entity": updated, "target_id": target_id, "edge_id": chosen["edge_id"],
                    "end": chosen_end, "parameter": chosen["parameter"],
                    "intersection": chosen["point"], "candidate_count": len(hits)}
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
        if name == "block_from_entities":
            block_name = str(arguments.get("name", "")).strip()
            if not block_name:
                raise ValueError("BLOCK_NAME_REQUIRED")
            if block_name in document.blocks:
                raise ValueError("BLOCK_EXISTS:" + block_name)
            source_ids = _refs(arguments, document)
            if not source_ids:
                raise ValueError("BLOCK_SOURCE_SELECTION_REQUIRED")
            base_point = _point(arguments.get("base_point"))
            selected_entities = [document.entity(identifier) for identifier in source_ids]
            for entity in selected_entities:
                layer = document.layers.get(str(entity.get("layer", "0")))
                if layer and layer.locked:
                    raise ValueError("CAD_LAYER_LOCKED:" + layer.name)
                if str(entity.get("type", "")).upper() == "HATCH" and entity.get("associative"):
                    raise ValueError("BLOCK_ASSOCIATIVE_HATCH_UNSUPPORTED")
            selected_handles = {str(entity.get("handle")) for entity in selected_entities if entity.get("handle")}
            for hatch in document.entities:
                if str(hatch.get("type", "")).upper() != "HATCH" or str(hatch.get("id")) in source_ids:
                    continue
                refs = {str(handle) for path in hatch.get("boundary_paths", []) for handle in path.get("source_handles", [])}
                if refs & selected_handles:
                    raise ValueError("BLOCK_SOURCE_REFERENCED_BY_HATCH")
            block_entities = []
            for index, original in enumerate(selected_entities, start=1):
                transformed = transform_entity(original, dx=-base_point["x"], dy=-base_point["y"], dz=-base_point["z"], allow_associative=False)
                transformed.pop("handle", None)
                transformed.pop("owner", None)
                transformed.pop("sheet", None)
                transformed["id"] = f"block:{block_name}:e{index:04d}"
                block_entities.append(transformed)
            document.blocks[block_name] = Block(block_name, base_point, block_entities)
            keep_originals = bool(arguments.get("keep_originals", True))
            inserted = None
            if not keep_originals:
                source_set = set(source_ids)
                document.entities = [entity for entity in document.entities if str(entity.get("id")) not in source_set]
                for selection in document.selection_sets.values():
                    selection.refs = [ref for ref in selection.refs if ref not in source_set]
                document.constraints = [constraint for constraint in document.constraints if not source_set.intersection(constraint.refs)]
            if bool(arguments.get("insert", False)):
                insert_point = _point(arguments.get("insert_point", base_point))
                insert_layer = str(arguments.get("insert_layer", selected_entities[0].get("layer", "0")))
                layer = document.ensure_layer(insert_layer)
                if layer.locked:
                    raise ValueError("CAD_LAYER_LOCKED:" + layer.name)
                scale = arguments.get("scale", {"x": 1, "y": 1, "z": 1})
                if isinstance(scale, (int, float)):
                    scale = {"x": float(scale), "y": float(scale), "z": float(scale)}
                scale = {axis: float(scale.get(axis, 1)) for axis in ("x", "y", "z")}
                if any(not math.isfinite(value) or value == 0 for value in scale.values()):
                    raise ValueError("BLOCK_INSERT_SCALE_INVALID")
                rotation = float(arguments.get("rotation", 0))
                if not math.isfinite(rotation):
                    raise ValueError("BLOCK_INSERT_ROTATION_INVALID")
                inserted = {"id": document.next_id("insert"), "type": "INSERT", "layer": insert_layer,
                            "block_name": block_name, "insert": insert_point, "scale": scale, "rotation": rotation}
                document.entities.append(inserted)
            return {"block": document.blocks[block_name].to_dict(), "source_ids": source_ids,
                    "insert": inserted, "kept_originals": keep_originals}
        if name == "block_attribute_create":
            block_name = str(arguments.get("block_name", arguments.get("name", ""))).strip()
            if block_name not in document.blocks:
                raise ValueError("BLOCK_NOT_FOUND:" + block_name)
            tag = str(arguments.get("tag", "")).strip()
            if not tag or not tag.replace("_", "a").replace("-", "a").isalnum():
                raise ValueError("BLOCK_ATTRIBUTE_TAG_INVALID")
            block = document.blocks[block_name]
            existing = next((item for item in block.entities
                             if str(item.get("type", "")).upper() == "ATTDEF" and str(item.get("tag", "")).casefold() == tag.casefold()), None)
            mode = str(arguments.get("if_exists", "error")).lower()
            if existing is not None and mode == "error":
                raise ValueError("BLOCK_ATTRIBUTE_EXISTS:" + tag)
            if mode not in {"error", "update"}:
                raise ValueError("BLOCK_ATTRIBUTE_IF_EXISTS_INVALID")
            layer_name = str(arguments.get("layer", existing.get("layer", "0") if existing else "0"))
            height = _finite_cad_number(arguments.get("height", existing.get("height", 1) if existing else 1), "BLOCK_ATTRIBUTE_HEIGHT_INVALID")
            if height <= 0:
                raise ValueError("BLOCK_ATTRIBUTE_HEIGHT_INVALID")
            insert = _point(arguments.get("insert", existing.get("insert") if existing else block.base_point))
            attribute = copy.deepcopy(existing) if existing else {"id": f"block:{block_name}:attdef:{tag}", "type": "ATTDEF"}
            attribute.update({"tag": tag, "layer": layer_name, "insert": insert,
                              "text": str(arguments.get("text", existing.get("text", "") if existing else "")),
                              "height": height, "rotation": _finite_cad_number(arguments.get("rotation", existing.get("rotation", 0) if existing else 0), "BLOCK_ATTRIBUTE_ROTATION_INVALID"),
                              "style": str(arguments.get("style", existing.get("style", "Standard") if existing else "Standard")),
                              "prompt": str(arguments.get("prompt", existing.get("prompt", "") if existing else "")),
                              "flags": int(arguments.get("flags", existing.get("flags", 0) if existing else 0))})
            if existing is None:
                block.entities.append(attribute)
            else:
                block.entities[block.entities.index(existing)] = attribute
            affected = []
            for entity in document.entities:
                if str(entity.get("type", "")).upper() != "INSERT" or str(entity.get("block_name", "")) != block_name:
                    continue
                values = copy.deepcopy(entity.get("attributes", []))
                current = next((item for item in values if str(item.get("tag", "")).casefold() == tag.casefold()), None)
                if current is None:
                    current = {"id": f"{entity.get('id')}:attr:{tag}", "type": "ATTRIB", "tag": tag,
                               "text": attribute["text"], "insert": _block_attribute_world_point(block, entity, insert),
                               "height": height, "rotation": attribute["rotation"], "style": attribute["style"],
                               "layer": entity.get("layer", layer_name), "flags": attribute.get("flags", 0)}
                    values.append(current)
                else:
                    current.update({"text": attribute["text"], "insert": _block_attribute_world_point(block, entity, insert),
                                    "height": height, "rotation": attribute["rotation"], "style": attribute["style"],
                                    "flags": attribute.get("flags", 0)})
                entity["attributes"] = values
                affected.append(str(entity.get("id")))
            document.ensure_layer(layer_name)
            return {"block": block.to_dict(), "attribute": attribute, "inserts": affected}
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
        if name == "edit_hatch":
            target_id = str(arguments.get("entity_id") or arguments.get("hatch_id") or "")
            if not target_id:
                raise ValueError("HATCH_ENTITY_REQUIRED")
            target = document.entity(target_id)
            if str(target.get("type", "")).upper() != "HATCH":
                raise ValueError("HATCH_ENTITY_REQUIRED")
            source_layer = document.layers.get(str(target.get("layer", "0")))
            if source_layer and source_layer.locked:
                raise ValueError("CAD_LAYER_LOCKED:" + source_layer.name)
            patch = copy.deepcopy(arguments.get("patch") or {})
            excluded = {"input_file", "output_file", "project_dir", "entity_id", "hatch_id", "patch"}
            editable = {"layer", "pattern", "solid", "pattern_angle", "pattern_scale", "pattern_type",
                        "hatch_style", "double", "boundary_vertices", "boundary_paths", "associative"}
            for key in editable:
                if key in arguments:
                    patch[key] = copy.deepcopy(arguments[key])
            updated = _merge(target, patch)
            updated["id"] = target["id"]
            if "boundary" in patch and "boundary_vertices" not in patch:
                updated["boundary_vertices"] = copy.deepcopy(patch["boundary"])
            layer_name = str(updated.get("layer", target.get("layer", "0")))
            destination_layer = document.layers.get(layer_name)
            if destination_layer and destination_layer.locked:
                raise ValueError("CAD_LAYER_LOCKED:" + destination_layer.name)
            document.ensure_layer(layer_name)
            if "pattern_scale" in updated:
                try:
                    scale = float(updated["pattern_scale"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("HATCH_PATTERN_SCALE_INVALID") from exc
                if not math.isfinite(scale) or scale <= 0:
                    raise ValueError("HATCH_PATTERN_SCALE_INVALID")
                updated["pattern_scale"] = scale
            if "pattern_angle" in updated:
                try:
                    angle = float(updated["pattern_angle"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("HATCH_PATTERN_ANGLE_INVALID") from exc
                if not math.isfinite(angle):
                    raise ValueError("HATCH_PATTERN_ANGLE_INVALID")
                updated["pattern_angle"] = angle
            if "pattern" in updated:
                updated["pattern"] = str(updated["pattern"] or "").strip()
                if not updated["pattern"] and not bool(updated.get("solid", target.get("solid", True))):
                    raise ValueError("HATCH_PATTERN_REQUIRED")
            if "solid" in updated:
                updated["solid"] = bool(updated["solid"])
            if "double" in updated:
                updated["double"] = bool(updated["double"])
            if "associative" in updated:
                updated["associative"] = bool(updated["associative"])
            before_associative = bool(target.get("associative"))
            boundary_changed = any(key in patch for key in ("boundary", "boundary_vertices", "boundary_paths"))
            if before_associative and boundary_changed:
                raise ValueError("ASSOCIATIVE_HATCH_BOUNDARY_EDIT_UNSUPPORTED")
            if "boundary_vertices" in updated:
                vertices = [_point(value) for value in (updated.get("boundary_vertices") or [])]
                if len(vertices) < 3:
                    raise ValueError("HATCH_BOUNDARY_TOO_SHORT")
                paths = updated.get("boundary_paths") or []
                if paths and str(paths[0].get("kind", "polyline")) != "polyline":
                    raise ValueError("HATCH_BOUNDARY_PATH_TYPE_MISMATCH")
                if paths:
                    paths = copy.deepcopy(paths)
                    paths[0]["vertices"] = [point | {"bulge": float(point.get("bulge", 0))} for point in vertices]
                    paths[0]["closed"] = True
                    updated["boundary_paths"] = paths
                else:
                    updated["boundary_paths"] = [{"kind": "polyline", "flags": 1, "closed": True,
                                                   "vertices": [point | {"bulge": 0.0} for point in vertices],
                                                   "source_handles": []}]
                updated["boundary_vertices"] = vertices
                updated["boundary_path_count"] = len(updated["boundary_paths"])
            if "boundary_paths" in updated:
                paths = updated.get("boundary_paths") or []
                if not paths:
                    raise ValueError("HATCH_BOUNDARY_PATHS_REQUIRED")
                for path in paths:
                    if str(path.get("kind", "polyline")) == "polyline" and (not path.get("closed", True) or len(path.get("vertices") or []) < 3):
                        raise ValueError("HATCH_BOUNDARY_TOO_SHORT")
                updated["boundary_path_count"] = len(paths)
                first = next((path for path in paths if str(path.get("kind", "")) == "polyline"), None)
                if first is not None:
                    updated["boundary_vertices"] = [{key: float(value.get(key, 0)) for key in ("x", "y", "z")} for value in first.get("vertices", [])]
            index = document.entities.index(target)
            document.entities[index] = updated
            changed_fields = sorted(key for key in set(target) | set(updated) if target.get(key) != updated.get(key))
            return {"entity": updated, "changed_fields": changed_fields, "associative": bool(updated.get("associative"))}
        if name == "center_mark_create":
            target_values = arguments.get("target")
            if target_values is None:
                target_values = arguments.get("entity_ids")
            if target_values is None and arguments.get("selection_set"):
                selection = document.selection_sets.get(str(arguments["selection_set"]))
                if selection is None:
                    raise ValueError("SELECTION_NOT_FOUND:" + str(arguments["selection_set"]))
                target_values = selection.refs
            if isinstance(target_values, (str, dict)):
                target_values = [target_values]
            target_ids = []
            for value in target_values or []:
                if isinstance(value, dict):
                    value = value.get("entity_id") or value.get("id") or value.get("ref")
                if value:
                    target_ids.append(str(value))
            if not target_ids:
                raise ValueError("CENTER_MARK_TARGET_REQUIRED")
            layer_name = str(arguments.get("layer", "CENTER"))
            output_layer = document.layers.get(layer_name)
            if output_layer and output_layer.locked:
                raise ValueError("CAD_LAYER_LOCKED:" + output_layer.name)
            document.ensure_layer(layer_name)
            extension_value = arguments.get("extension")
            if extension_value is None:
                extension = None
            elif isinstance(extension_value, bool):
                raise ValueError("CENTER_MARK_EXTENSION_INVALID")
            else:
                try:
                    extension = float(extension_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError("CENTER_MARK_EXTENSION_INVALID") from exc
                if not math.isfinite(extension) or extension < 0:
                    raise ValueError("CENTER_MARK_EXTENSION_INVALID")
            tag = str(arguments.get("tag", ""))
            tag_mode = str(arguments.get("if_tag_exists", "error")).lower()
            if tag_mode not in {"error", "skip", "replace"}:
                raise ValueError("CENTER_MARK_TAG_MODE_INVALID")
            existing = [item for item in document.entities
                        if tag and (str((item.get("center_mark") or {}).get("tag", "")) == tag
                                    or str((item.get("center_mark") or {}).get("tag", "")).startswith(tag + "."))]
            if existing and tag_mode == "error":
                raise ValueError("CENTER_MARK_TAG_EXISTS:" + tag)
            if existing and tag_mode == "skip":
                return {"entities": [str(item.get("id")) for item in existing], "count": len(existing),
                        "targets": sorted({str((item.get("center_mark") or {}).get("target_id")) for item in existing})}
            if existing and tag_mode == "replace":
                document.entities = [item for item in document.entities if item not in existing]
            linetype = str(arguments.get("linetype", "CENTER"))
            created: list[str] = []
            target_results: list[str] = []
            requested_layout = str(arguments.get("layout", "")).strip()
            for index, target_id in enumerate(target_ids, start=1):
                target = document.entity(target_id)
                kind = str(target.get("type", "")).upper()
                if kind not in {"CIRCLE", "ARC"}:
                    raise ValueError("CENTER_MARK_TARGET_UNSUPPORTED:" + kind)
                target_layout = str(target.get("sheet", target.get("layout", "Model")))
                if requested_layout and requested_layout.lower() not in {"auto", target_layout.lower()}:
                    raise ValueError("CENTER_MARK_LAYOUT_MISMATCH")
                center = _point(target.get("center"))
                try:
                    radius = float(target.get("radius", 0))
                except (TypeError, ValueError) as exc:
                    raise ValueError("CENTER_MARK_RADIUS_INVALID") from exc
                if not math.isfinite(radius) or radius <= 0:
                    raise ValueError("CENTER_MARK_RADIUS_INVALID")
                overshoot = max(2.0, radius / 2.0) if extension is None else extension
                half_length = radius + overshoot
                base_tag = f"{tag}.{index}" if tag and len(target_ids) > 1 else tag
                common = {"layer": layer_name, "linetype": linetype}
                if "color" in arguments:
                    common["color"] = int(arguments["color"])
                if "lineweight" in arguments:
                    common["lineweight"] = int(arguments["lineweight"])
                if target.get("sheet") is not None:
                    common["sheet"] = target["sheet"]
                horizontal = {"id": document.next_id("center"), "type": "LINE",
                              "start": {"x": center["x"] - half_length, "y": center["y"], "z": center["z"]},
                              "end": {"x": center["x"] + half_length, "y": center["y"], "z": center["z"]},
                              **copy.deepcopy(common),
                              "center_mark": {"target_id": target_id, "axis": "horizontal", "extension": overshoot, "tag": base_tag}}
                document.entities.append(horizontal)
                vertical = {"id": document.next_id("center"), "type": "LINE",
                            "start": {"x": center["x"], "y": center["y"] - half_length, "z": center["z"]},
                            "end": {"x": center["x"], "y": center["y"] + half_length, "z": center["z"]},
                            **copy.deepcopy(common),
                            "center_mark": {"target_id": target_id, "axis": "vertical", "extension": overshoot, "tag": base_tag}}
                document.entities.append(vertical)
                created.extend((horizontal["id"], vertical["id"]))
                target_results.append(target_id)
            return {"entities": created, "count": len(created), "targets": target_results}
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
                         sheets=document.sheets, viewports=document.viewports, text_styles=document.text_styles,
                         annotation_scale=document.metadata.get("annotation_scale"),
                         source=document._source_path, units=document.units)
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
        if "annotation_scale" in expected.metadata:
            integrity["annotation_scale"] = persisted.metadata.get("annotation_scale") == expected.metadata.get("annotation_scale")
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
