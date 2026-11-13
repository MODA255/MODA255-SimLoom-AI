"""Read-only native checks, independent of CadDocument and its sidecar.

Audit repairs are reported, never saved. Post-commit callers reject repairs as
well as errors so a damaged drawing cannot silently pass a healing parser.
"""
from __future__ import annotations

import io
import math
import re
from pathlib import Path

import ezdxf
from ezdxf.lldxf.tagwriter import TagWriter

from .curve_validation import find_self_intersections


def _identifier(entity):
    if entity.has_xdata("SIMLOOM_AI"):
        data = entity.get_xdata("SIMLOOM_AI")
        if data and data[0].code == 1000:
            return str(data[0].value)
    return str(entity.dxf.handle)


def _equal(expected, actual):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            actual = {"z": 0., **dict(zip(("x", "y", "z"), actual))}
        return all(key in actual and _equal(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, (list, tuple)):
        return len(expected) == len(actual) and all(_equal(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return math.isclose(float(expected), float(actual), rel_tol=1e-9, abs_tol=1e-7)
    return expected == actual


def _native_geometry_signature(entity):
    """Create a duplicate-geometry key from native DXF fields only."""
    kind = entity.dxftype()
    dxf = entity.dxf
    try:
        if kind == "LINE":
            value = (tuple(dxf.start), tuple(dxf.end))
        elif kind == "CIRCLE":
            value = (tuple(dxf.center), float(dxf.radius))
        elif kind == "ARC":
            value = (tuple(dxf.center), float(dxf.radius), float(dxf.start_angle), float(dxf.end_angle))
        elif kind == "POINT":
            value = (tuple(dxf.location),)
        elif kind == "LWPOLYLINE":
            value = (tuple(tuple(round(float(item), 9) for item in point) for point in entity.get_points("xyseb")), bool(entity.closed))
        elif kind == "POLYLINE":
            value = (tuple(tuple(v.dxf.location) for v in entity.vertices), bool(entity.is_closed))
        elif kind == "ELLIPSE":
            value = (tuple(dxf.center), tuple(dxf.major_axis), float(dxf.ratio), float(dxf.start_param), float(dxf.end_param))
        elif kind == "SPLINE":
            value = (int(dxf.get("degree", 3)), tuple(tuple(point) for point in entity.fit_points),
                     tuple(tuple(point) for point in entity.control_points), tuple(entity.knots), tuple(entity.weights))
        else:
            return None
        return (kind, str(dxf.layer), repr(value))
    except (AttributeError, TypeError, ValueError):
        return None


def inspect_native(path: str | Path, expected=None, *, corner_connections=None, reject_curve_topology=False):
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(16384)
    if b"$ACADVER" not in header and not header.startswith(b"AutoCAD Binary DXF"):
        errors = [{"code": "CORNER_NATIVE_AUDIT_UNAVAILABLE"}] if corner_connections else []
        return {"valid": not errors, "available": False, "errors": errors, "warnings": ["LEGACY_DXF_NATIVE_AUDIT_UNAVAILABLE"]}
    errors, warnings = [], []
    curve_topology = []
    drawing = ezdxf.readfile(path)
    # Check geometry before audit, as the native auditor may repair its own
    # in-memory drawing. Nothing in this function writes a file.
    entities = [entity for layout in drawing.layouts for entity in layout if entity.dxftype() != "VIEWPORT"]
    ids = [_identifier(e) for e in entities]
    if len(ids) != len(set(ids)):
        errors.append({"code": "NATIVE_DUPLICATE_ENTITY_ID"})
    by_id = dict(zip(ids, entities))
    native_geometry_keys = {}

    def check(entity, field, wanted, actual):
        try:
            matches = _equal(wanted, actual)
        except (ValueError, TypeError, KeyError):
            matches = False
        if not matches:
            errors.append({"code": "NATIVE_GEOMETRY_MISMATCH", "entity_id": _identifier(entity), "field": field})

    def compare(item, entity, *, space=True):
        kind, dxf = entity.dxftype(), entity.dxf
        check(entity, "type", item["type"], kind)
        check(entity, "layer", item.get("layer", "0"), dxf.layer)
        if space:
            check(entity, "layout", item.get("sheet", "Model"), entity.get_layout().name)
        attributes = {
            "LINE": {"start": "start", "end": "end"}, "POINT": {"point": "location"},
            "CIRCLE": {"center": "center", "radius": "radius"},
            "ARC": {"center": "center", "radius": "radius", "start_angle": "start_angle", "end_angle": "end_angle"},
            "ELLIPSE": {"center": "center", "major_axis": "major_axis", "ratio": "ratio", "start_param": "start_param", "end_param": "end_param"},
            "TEXT": {"text": "text", "insert": "insert", "height": "height", "rotation": "rotation", "align_point": "align_point"},
            "MTEXT": {"insert": "insert", "height": "char_height", "width": "width"},
            "INSERT": {"block_name": "name", "insert": "insert", "rotation": "rotation"},
        }.get(kind, {})
        attributes |= {key: dest for key, dest in (("color_index", "color"), ("true_color", "true_color"), ("linetype", "linetype"), ("extrusion", "extrusion")) if key in item}
        for key, dest in attributes.items():
            if key in item:
                check(entity, key, item[key], getattr(dxf, dest))
        if kind == "MTEXT":
            check(entity, "text", item.get("text", ""), entity.text)
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            if kind == "LWPOLYLINE":
                points = list(entity.get_points("xyseb"))
                actual = [{"x": v[0], "y": v[1], "z": dxf.elevation} for v in points]
                closed = bool(entity.closed)
            else:
                actual = [v.dxf.location for v in entity.vertices]
                points = [(0, 0, v.dxf.start_width, v.dxf.end_width, v.dxf.bulge) for v in entity.vertices]
                closed = bool(entity.is_closed)
            check(entity, "vertices", item.get("vertices", []), actual)
            check(entity, "closed", bool(item.get("closed")), closed)
            if "bulges" in item: check(entity, "bulges", item["bulges"], [v[4] for v in points])
            if "widths" in item: check(entity, "widths", item["widths"], [[v[2], v[3]] for v in points])
        elif kind == "INSERT":
            check(entity, "scale", item.get("scale", {"x": 1, "y": 1, "z": 1}), {a: getattr(dxf, a + "scale") for a in ("x", "y", "z")})
            attrs = item.get("attributes", [])
            check(entity, "attribute_count", len(attrs), len(entity.attribs))
            for wanted, actual in zip(attrs, entity.attribs):
                for key in ("tag", "text", "insert", "height", "rotation"):
                    if key in wanted: check(entity, "attribute." + key, wanted[key], getattr(actual.dxf, key))
        elif kind == "HATCH":
            paths = item.get("boundary_paths") or [{"kind": "polyline", "vertices": item.get("boundary_vertices", [])}]
            check(entity, "boundary_count", len(paths), len(entity.paths))
            if "associative" in item:
                check(entity, "associative", bool(item["associative"]), bool(dxf.associative))
            for wanted, actual in zip(paths, entity.paths):
                if "flags" in wanted:
                    check(entity, "boundary_flags", int(wanted["flags"]), int(actual.path_type_flags))
                if "source_handles" in wanted:
                    check(entity, "source_handles", list(wanted["source_handles"]), list(actual.source_boundary_objects))
                if wanted["kind"] == "polyline" and hasattr(actual, "vertices"):
                    vertices = [{"x": v[0], "y": v[1], "z": 0., "bulge": v[2]} for v in actual.vertices]
                    check(entity, "boundary_vertices", wanted["vertices"], vertices)
                elif wanted["kind"] == "edges" and hasattr(actual, "edges"):
                    check(entity, "edge_count", len(wanted["edges"]), len(actual.edges))
                    for edge, native in zip(wanted["edges"], actual.edges):
                        check(entity, "edge_kind", edge["kind"], type(native).__name__)
                        for key, value in edge.items():
                            if key != "kind": check(entity, "edge." + key, value, getattr(native, key, None))
                else:
                    errors.append({"code": "NATIVE_HATCH_PATH_MISMATCH", "entity_id": _identifier(entity)})
        elif kind == "DIMENSION" and item.get("measurement") is not None:
            measured = entity.get_measurement()
            if entity.dimtype == 6:
                measured = measured.x if dxf.dimtype & 64 else measured.y
            check(entity, "measurement", item["measurement"], measured)
            for key, value in item.get("native_definition", {}).items():
                if key in {"defpoint2", "defpoint3", "defpoint4"}:
                    check(entity, key, value, getattr(dxf, key))
        elif kind == "SPLINE":
            check(entity, "degree", int(item.get("degree", 3)), int(dxf.get("degree", 3)))
            check(entity, "fit_points", item.get("fit_points", []), [point for point in entity.fit_points])
            check(entity, "control_points", item.get("control_points", []), [point for point in entity.control_points])
            check(entity, "knots", item.get("knots", []), list(entity.knots))
            check(entity, "weights", item.get("weights", []), list(entity.weights))
        elif kind == "LEADER":
            check(entity, "vertices", item.get("vertices", []), list(entity.vertices))
        if item.get("raw_tags"):
            output = io.StringIO()
            entity.export_dxf(TagWriter(output, dxfversion=drawing.dxfversion))
            values = output.getvalue().splitlines()
            actual = [(values[index].strip(), values[index + 1]) for index in range(2, len(values) - 1, 2)]
            check(entity, "opaque_record", item["raw_tags"], actual)

    if expected is not None:
        check_ids = {str(e["id"]) for e in expected.entities}
        if check_ids != set(ids): errors.append({"code": "NATIVE_ENTITY_SET_MISMATCH"})
        for item in expected.entities:
            if str(item["id"]) in by_id:
                compare(item, by_id[str(item["id"])])
        for name, block in expected.blocks.items():
            native = drawing.blocks.get(name)
            if native is None:
                errors.append({"code": "NATIVE_BLOCK_MISSING", "block": name})
                continue
            if len(native) != len(block.entities): errors.append({"code": "NATIVE_BLOCK_ENTITY_COUNT", "block": name})
            for wanted, entity in zip(block.entities, native): compare(wanted, entity, space=False)
        native_blocks = {b.name for b in drawing.blocks if not b.is_any_layout and not b.name.upper().startswith("*D")}
        if not set(expected.blocks) <= native_blocks:
            errors.append({"code": "NATIVE_BLOCK_SET_MISMATCH"})
        for name in expected.sheets:
            if name not in drawing.layouts:
                errors.append({"code": "NATIVE_SHEET_MISSING", "sheet": name})
                continue
            properties = expected.sheets[name]
            dxf = drawing.layouts.get(name).dxf_layout.dxf
            factor = 25.4 if properties.get("units") == "in" else 1.
            if not _equal([properties["width"] * factor, properties["height"] * factor], [dxf.paper_width, dxf.paper_height]):
                errors.append({"code": "NATIVE_SHEET_SIZE_MISMATCH", "sheet": name})
        native_viewports = {_identifier(v): v for layout in drawing.layouts for v in layout.query("VIEWPORT") if v.dxf.id != 1}
        if set(native_viewports) != set(expected.viewports):
            errors.append({"code": "NATIVE_VIEWPORT_SET_MISMATCH"})
        for name, viewport in expected.viewports.items():
            if name in native_viewports:
                entity = native_viewports[name]
                for key in ("center", "width", "height"):
                    check(entity, key, viewport[key], getattr(entity.dxf, key))
                check(entity, "scale", viewport["scale"], entity.dxf.height / entity.dxf.view_height)

    for entity in entities:
        signature = _native_geometry_signature(entity)
        if signature is not None:
            native_geometry_keys.setdefault(signature, []).append(_identifier(entity))
        if entity.dxftype() in {"TEXT", "MTEXT"}:
            height = float(entity.dxf.height if entity.dxftype() == "TEXT" else entity.dxf.char_height)
            if not math.isfinite(height) or height <= 0:
                errors.append({"code": "NATIVE_INVALID_TEXT_HEIGHT", "entity_id": _identifier(entity), "height": height})
        if entity.dxftype() in {"LWPOLYLINE", "POLYLINE"}:
            if entity.dxftype() == "LWPOLYLINE":
                vertices = [{"x": point[0], "y": point[1]} for point in entity.get_points("xyseb")]
                closed = bool(entity.closed)
            else:
                vertices = [{"x": vertex.dxf.location.x, "y": vertex.dxf.location.y} for vertex in entity.vertices]
                closed = bool(entity.is_closed)
            if closed and len(vertices) >= 3:
                area = 0.5 * sum(vertices[index]["x"] * vertices[(index + 1) % len(vertices)]["y"]
                                  - vertices[(index + 1) % len(vertices)]["x"] * vertices[index]["y"]
                                  for index in range(len(vertices)))
                if abs(area) <= 1e-12:
                    errors.append({"code": "NATIVE_CLOSED_POLYLINE_DEGENERATE", "entity_id": _identifier(entity)})
        if entity.dxftype() == "SPLINE":
            degree = int(entity.dxf.get("degree", 3))
            if degree < 1 or degree > 11:
                errors.append({"code": "NATIVE_INVALID_SPLINE_DEGREE", "entity_id": _identifier(entity), "degree": degree})
            if len(entity.fit_points) + len(entity.control_points) < 2:
                errors.append({"code": "NATIVE_SPLINE_TOO_SHORT", "entity_id": _identifier(entity)})
        if entity.dxftype() == "DIMENSION":
            block = entity.get_geometry_block()
            if block is None or not len(block):
                errors.append({"code": "DIMENSION_GRAPHICS_MISSING", "entity_id": _identifier(entity)})
            text = str(entity.dxf.text or "").strip()
            if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
                measured = entity.get_measurement()
                if entity.dimtype == 6: measured = measured.x if entity.dxf.dimtype & 64 else measured.y
                if entity.dimtype not in {2, 5}:
                    measured *= abs(entity.override().get("dimlfac", 1.))
                decimals = len(text.rsplit(".", 1)[1]) if "." in text else 0
                if not math.isclose(float(text), measured, rel_tol=1e-9, abs_tol=0.5 * 10 ** -decimals):
                    errors.append({"code": "DIMENSION_TEXT_MISMATCH", "entity_id": _identifier(entity), "text": text, "measured": measured})
        curve_entity = None
        kind = entity.dxftype()
        if kind in {"LWPOLYLINE", "POLYLINE"}:
            if kind == "LWPOLYLINE":
                points = list(entity.get_points("xy"))
                curve_entity = {"type": kind, "vertices": [{"x": float(point[0]), "y": float(point[1])} for point in points],
                                "bulges": [float(point[4]) for point in entity.get_points("xyseb")], "closed": bool(entity.closed)}
            else:
                curve_entity = {"type": kind, "vertices": [{"x": float(vertex.dxf.location.x), "y": float(vertex.dxf.location.y)} for vertex in entity.vertices],
                                "bulges": [float(vertex.dxf.bulge) for vertex in entity.vertices], "closed": bool(entity.is_closed)}
        elif kind == "SPLINE":
            curve_entity = {"type": kind, "degree": int(entity.dxf.get("degree", 3)),
                            "fit_points": [{"x": float(point[0]), "y": float(point[1]), "z": float(point[2])} for point in entity.fit_points],
                            "control_points": [{"x": float(point[0]), "y": float(point[1]), "z": float(point[2])} for point in entity.control_points],
                            "knots": list(entity.knots), "weights": list(entity.weights)}
        if curve_entity is not None:
            findings = find_self_intersections(curve_entity, 1e-7)
            if findings is None:
                warnings.append("NATIVE_CURVE_TOPOLOGY_UNCHECKED:" + _identifier(entity))
            for finding in findings or []:
                item = {"code": "NATIVE_CURVE_SELF_" + str(finding["kind"]).upper(),
                        "entity_id": _identifier(entity), **finding}
                curve_topology.append(item)
                if reject_curve_topology:
                    errors.append(item)
                else:
                    warnings.append(item["code"] + ":" + _identifier(entity))
    for signature, entity_ids in native_geometry_keys.items():
        if len(entity_ids) > 1:
            warnings.append("NATIVE_DUPLICATE_GEOMETRY:" + ",".join(entity_ids))
    corner_checks = []
    if corner_connections:
        from .corner_validation import inspect_corner
        # Reconstruct only the needed geometric facts from native records.
        # Do not use CadDocument, its sidecar, or the construction routines.
        primitives = []
        for identifier, entity in by_id.items():
            item = {"id": identifier, "type": entity.dxftype(), "sheet": entity.get_layout().name}
            if entity.dxftype() == "LINE":
                item.update(start=tuple(entity.dxf.start), end=tuple(entity.dxf.end))
            elif entity.dxftype() == "ARC":
                item.update(center=tuple(entity.ocs().to_wcs(entity.dxf.center)), radius=entity.dxf.radius,
                            start_angle=entity.dxf.start_angle, end_angle=entity.dxf.end_angle,
                            _world_endpoints=[tuple(entity.start_point), tuple(entity.end_point)])
            primitives.append(item)
        for spec in corner_connections:
            checked = inspect_corner(primitives, spec)
            corner_checks.append(checked)
            errors.extend(checked["errors"])
    audit = drawing.audit()
    for finding in [*audit.errors, *audit.fixes]:
        errors.append({"code": "NATIVE_AUDIT_ERROR" if finding in audit.errors else "NATIVE_AUDIT_REPAIR_REQUIRED", "audit_code": int(finding.code), "message": finding.message})
    return {"valid": not errors, "available": True, "backend": "ezdxf", "errors": errors, "warnings": warnings,
            "entity_count": len(entities), "audit_error_count": len(audit.errors), "audit_repair_count": len(audit.fixes),
            "corner_checks": corner_checks, "curve_topology": curve_topology}
