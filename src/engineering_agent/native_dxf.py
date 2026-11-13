"""Native DXF I/O. Keep the source drawing and patch only changed entities."""
from __future__ import annotations

import copy
import io
import math
from collections import Counter
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf.colors import aci2rgb
from ezdxf.lldxf.tagwriter import TagWriter
from ezdxf.math import Matrix44

APPID = "SIMLOOM_AI"
UNIT_NAMES = ("unitless", "in", "ft", "mi", "mm", "cm", "m", "km", "microinch", "mil", "yd", "angstrom", "nm", "um", "dm", "dam", "hm", "gm", "au", "ly", "pc", "us-ft", "us-in", "us-yd", "us-mi")


def point(value) -> dict[str, float]:
    if isinstance(value, dict):
        return {a: float(value.get(a, 0)) for a in ("x", "y", "z")}
    values = tuple(value)
    return dict(zip(("x", "y", "z"), [float(values[i]) if i < len(values) else 0. for i in range(3)]))


def xyz(value) -> tuple[float, float, float]:
    return tuple(point(value or {}).values())


def entity_id(entity) -> str:
    if entity.has_xdata(APPID):
        tags = entity.get_xdata(APPID)
        if tags and tags[0].code == 1000:
            return str(tags[0].value)
    return str(entity.dxf.handle)


def stamp(entity, identifier: str):
    if APPID not in entity.doc.appids:
        entity.doc.appids.new(APPID)
    # Only our own application data is changed; other applications keep theirs.
    entity.set_xdata(APPID, [(1000, identifier)])


def _raw(entity):
    text = io.StringIO()
    entity.export_dxf(TagWriter(text, dxfversion=entity.doc.dxfversion))
    values = text.getvalue().splitlines()
    return [(values[i].strip(), values[i + 1]) for i in range(2, len(values) - 1, 2)]


def entity_model(entity) -> dict[str, Any]:
    kind = entity.dxftype()
    dxf = entity.dxf
    item: dict[str, Any] = {"id": entity_id(entity), "type": kind, "handle": dxf.handle, "owner": dxf.owner,
                            "layer": dxf.get("layer", "0")}
    for attr, key in (("linetype", "linetype"), ("color", "color_index"), ("true_color", "true_color"),
                      ("lineweight", "lineweight"), ("thickness", "thickness")):
        if dxf.hasattr(attr):
            item[key] = dxf.get(attr)
    if dxf.hasattr("extrusion"):
        item["extrusion"] = point(dxf.extrusion)
    if kind == "LINE":
        item.update(start=point(dxf.start), end=point(dxf.end))
    elif kind in {"RAY", "XLINE"}:
        item.update(start=point(dxf.start), unit_vector=point(dxf.unit_vector))
    elif kind in {"CIRCLE", "ARC"}:
        item.update(center=point(dxf.center), radius=float(dxf.radius))
        if kind == "ARC":
            item.update(start_angle=float(dxf.start_angle), end_angle=float(dxf.end_angle))
    elif kind == "POINT":
        item["point"] = point(dxf.location)
    elif kind in {"TEXT", "MTEXT", "ATTDEF", "ATTRIB"}:
        item.update(insert=point(dxf.insert), text=entity.text if kind == "MTEXT" else dxf.text,
                    height=float(dxf.char_height if kind == "MTEXT" else dxf.height),
                    rotation=float(entity.get_rotation() if kind == "MTEXT" else dxf.rotation), style=dxf.style)
        if kind == "MTEXT":
            item.update(width=float(dxf.width), attachment_point=int(dxf.attachment_point))
        else:
            item.update(halign=int(dxf.halign), valign=int(dxf.valign))
            item.update(width_factor=float(dxf.width), oblique=float(dxf.oblique), text_generation_flag=int(dxf.text_generation_flag))
            if dxf.hasattr("align_point"):
                item["align_point"] = point(dxf.align_point)
        if kind in {"ATTDEF", "ATTRIB"}:
            item.update(tag=dxf.tag, flags=int(dxf.flags))
            if kind == "ATTDEF" and dxf.hasattr("prompt"):
                item["prompt"] = str(dxf.prompt)
    elif kind in {"LWPOLYLINE", "POLYLINE"}:
        if kind == "LWPOLYLINE":
            points = list(entity.get_points("xyseb"))
            item.update(vertices=[{"x": float(v[0]), "y": float(v[1]), "z": float(dxf.elevation)} for v in points],
                        bulges=[float(v[4]) for v in points], widths=[[float(v[2]), float(v[3])] for v in points],
                        closed=bool(entity.closed), elevation=float(dxf.elevation), const_width=float(dxf.const_width))
        else:
            item.update(vertices=[point(v.dxf.location) for v in entity.vertices], closed=bool(entity.is_closed),
                        bulges=[float(v.dxf.bulge) for v in entity.vertices], flags=int(dxf.flags), elevation=point(dxf.elevation),
                        widths=[[float(v.dxf.start_width), float(v.dxf.end_width)] for v in entity.vertices])
    elif kind == "ELLIPSE":
        item.update(center=point(dxf.center), major_axis=point(dxf.major_axis), ratio=float(dxf.ratio),
                    start_param=float(dxf.start_param), end_param=float(dxf.end_param))
    elif kind == "SPLINE":
        item.update(
            degree=int(dxf.get("degree", 3)),
            flags=int(dxf.get("flags", 0)),
            fit_points=[point(value) for value in entity.fit_points],
            control_points=[point(value) for value in entity.control_points],
            knots=[float(value) for value in entity.knots],
            weights=[float(value) for value in entity.weights],
        )
    elif kind == "INSERT":
        item.update(block_name=dxf.name, insert=point(dxf.insert), scale={"x": dxf.xscale, "y": dxf.yscale, "z": dxf.zscale},
                    rotation=float(dxf.rotation), attributes=[entity_model(attr) for attr in entity.attribs],
                    row_count=int(dxf.row_count), column_count=int(dxf.column_count), row_spacing=float(dxf.row_spacing), column_spacing=float(dxf.column_spacing))
        from .title_blocks import TITLE_APPID
        if entity.has_xdata(TITLE_APPID):
            values = entity.get_xdata(TITLE_APPID)
            if len(values) >= 2 and values[0].value == "v1":
                item["title_block_id"] = str(values[1].value)
                widths = {}
                for tag, width in zip(values[2::2], values[3::2]):
                    if tag.code == 1000 and width.code == 1040 and math.isfinite(width.value) and width.value > 0:
                        widths[str(tag.value)] = float(width.value)
                if widths:
                    item["title_field_widths"] = widths
    elif kind == "DIMENSION":
        dimtype = entity.dimtype
        item.update(dimension_type={0: "linear", 1: "linear", 2: "angular", 3: "diameter", 4: "radial", 5: "angular", 6: "ordinate"}.get(dimtype, "unsupported"),
                    dimension_code=int(dxf.dimtype), definition=point(dxf.defpoint), text=dxf.text,
                    block_name=dxf.get("geometry"), dimstyle=dxf.dimstyle)
        item["native_definition"] = {
            key: point(dxf.get(key)) for key in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5", "text_midpoint", "insert")
            if dxf.hasattr(key)
        }
        item["native_angles"] = {key: float(dxf.get(key)) for key in ("angle", "text_rotation", "horizontal_direction", "oblique_angle") if dxf.hasattr(key)}
        if dimtype in {0, 1}:
            item.update(start=point(dxf.defpoint2), end=point(dxf.defpoint3), angle=float(dxf.angle))
        elif dimtype == 4:
            item.update(center=point(dxf.defpoint), start=point(dxf.defpoint), end=point(dxf.defpoint4))
        elif dimtype == 3:
            a, b = point(dxf.defpoint), point(dxf.defpoint4)
            item.update(start=a, end=b, center={k: (a[k] + b[k]) / 2 for k in a})
        elif dimtype == 5:
            item.update(center=point(dxf.defpoint4), start=point(dxf.defpoint2), end=point(dxf.defpoint3))
        elif dimtype == 6:
            item.update(start=point(dxf.defpoint2), center=point(dxf.defpoint), axis="x" if dxf.dimtype & 64 else "y")
        try:
            measured = entity.get_measurement()
            item["measurement"] = float(getattr(measured, item.get("axis", "x"))) if dimtype == 6 else float(measured)
        except (ValueError, TypeError, ZeroDivisionError, AttributeError):
            item["measurement"] = None
    elif kind == "HATCH":
        paths = []
        for boundary in entity.paths:
            if hasattr(boundary, "vertices"):
                paths.append({"kind": "polyline", "flags": boundary.path_type_flags, "closed": boundary.is_closed,
                              "source_handles": list(boundary.source_boundary_objects),
                              "vertices": [{"x": float(v[0]), "y": float(v[1]), "z": 0., "bulge": float(v[2])} for v in boundary.vertices]})
            else:
                # Store all edge geometry for inspection. Unedited paths remain
                # attached to their original native HATCH object when saving.
                edges = []
                for edge in boundary.edges:
                    data = {"kind": type(edge).__name__}
                    for name in ("start", "end", "center", "major_axis", "radius", "ratio", "start_angle", "end_angle", "ccw", "degree", "rational", "periodic", "knot_values", "weights", "control_points", "fit_points", "start_tangent", "end_tangent"):
                        if hasattr(edge, name):
                            value = getattr(edge, name)
                            if value is None:
                                continue
                            if name in {"start", "end", "center", "major_axis", "start_tangent", "end_tangent"}:
                                value = point(value)
                            elif name in {"control_points", "fit_points"}:
                                value = [point(v) for v in value]
                            elif not isinstance(value, (int, float, bool, str)):
                                value = list(value)
                            data[name] = value
                    edges.append(data)
                paths.append({"kind": "edges", "flags": boundary.path_type_flags, "edges": edges,
                              "source_handles": list(boundary.source_boundary_objects)})
        first = paths[0] if paths else {}
        item.update(pattern=dxf.pattern_name, solid=bool(dxf.solid_fill), boundary_paths=paths,
                    boundary_vertices=[{a: p[a] for a in ("x", "y", "z")} for p in first.get("vertices", [])],
                    boundary_path_count=len(paths), hatch_style=int(dxf.hatch_style), pattern_type=int(dxf.pattern_type),
                    pattern_angle=float(dxf.pattern_angle), pattern_scale=float(dxf.pattern_scale), double=bool(dxf.pattern_double))
        item["elevation"] = point(dxf.elevation)
        item["associative"] = bool(dxf.associative)
        if entity.pattern:
            item["pattern_lines"] = [[line.angle, list(line.base_point), list(line.offset), list(line.dash_length_items)] for line in entity.pattern.lines]
    elif kind == "LEADER":
        item.update(vertices=[point(v) for v in entity.vertices], arrowhead=bool(dxf.has_arrowhead), dimstyle=dxf.dimstyle)
    else:
        item["raw_tags"] = _raw(entity)
    return item


def read_native(path: str | Path, *, respect_redraw_order: bool = True) -> dict[str, Any]:
    drawing = ezdxf.readfile(path)
    entities, sheets, viewports = [], {}, {}
    for layout in drawing.layouts:
        paper = layout.name != "Model"
        if paper:
            properties = layout.dxf_layout.dxf
            unit = "in" if properties.plot_paper_units == 0 else "mm"
            factor = 25.4 if unit == "in" else 1.
            page_setup = {
                "margins": [float(properties.get("left_margin", 0)) / factor,
                            float(properties.get("bottom_margin", 0)) / factor,
                            float(properties.get("right_margin", 0)) / factor,
                            float(properties.get("top_margin", 0)) / factor],
                "offset": [float(properties.get("plot_origin_x_offset", 0)) / factor,
                           float(properties.get("plot_origin_y_offset", 0)) / factor],
                "rotation": int(properties.get("plot_rotation", 0)),
                "device": str(properties.get("plot_configuration_file", "")),
            }
            numerator, denominator = float(properties.get("scale_numerator", 1)), float(properties.get("scale_denominator", 1))
            if math.isfinite(numerator) and math.isfinite(denominator) and numerator > 0 and denominator > 0:
                page_setup["scale"] = [numerator, denominator]
            sheets[layout.name] = {"name": layout.name, "width": float(properties.paper_width) / factor, "height": float(properties.paper_height) / factor, "units": unit, "page_setup": page_setup}
        ordered_entities = layout.entities_in_redraw_order() if respect_redraw_order else layout
        for entity in ordered_entities:
            if entity.dxftype() == "VIEWPORT":
                if int(entity.dxf.id) == 1:
                    continue
                name = entity_id(entity)
                viewports[name] = {"name": name, "handle": entity.dxf.handle, "sheet": layout.name,
                    "center": point(entity.dxf.center), "width": float(entity.dxf.width), "height": float(entity.dxf.height),
                    "view_center": point(entity.dxf.view_center_point), "scale": float(entity.dxf.height / entity.dxf.view_height),
                    "locked": bool(entity.dxf.flags & 16384)}
                continue
            item = entity_model(entity)
            if paper:
                item["sheet"] = layout.name
            entities.append(item)
    blocks = {block.name: {"name": block.name, "base_point": point(block.block.dxf.base_point),
                           "entities": [entity_model(e) for e in block], "entity_count": len(block)}
              for block in drawing.blocks if not block.is_any_layout and not block.name.upper().startswith("*D")}
    styles = {}
    for layer in drawing.layers:
        styles[layer.dxf.name] = _layer_model(layer)
    text_styles = {}
    for style in drawing.styles:
        values = {key: value for key, value in style.dxfattribs().items() if key not in {"handle", "owner", "name"}}
        text_styles[style.dxf.name] = {"name": style.dxf.name, **values}
    for entity in entities:
        if entity.get("title_block_id") and entity.get("sheet") in sheets:
            sheets[entity["sheet"]]["title_block"] = {
                "id": entity["title_block_id"], "block_name": entity["block_name"], "entity_ids": [entity["id"]],
                "fields": {attr["tag"]: attr["text"] for attr in entity.get("attributes", [])},
            }
    systems = [{"name": "WCS", "origin": point((0, 0, 0)), "x_axis": point((1, 0, 0)), "y_axis": point((0, 1, 0))}]
    for ucs in drawing.ucs:
        systems.append({"name": ucs.dxf.name, "origin": point(ucs.dxf.origin), "x_axis": point(ucs.dxf.xaxis), "y_axis": point(ucs.dxf.yaxis)})
    from .dxf import bbox
    annotation_scale = drawing.header.get("$DIMSCALE", 1.0)
    try:
        annotation_scale = float(annotation_scale)
    except (TypeError, ValueError):
        annotation_scale = 1.0
    if not math.isfinite(annotation_scale) or annotation_scale <= 0:
        annotation_scale = 1.0
    return {"format": "DXF", "backend": "ezdxf", "dxf_version": drawing.dxfversion, "entities": entities,
            "entity_count": len(entities), "entity_types": dict(Counter(e["type"] for e in entities)),
            "layers": {name: sum(e["layer"] == name for e in entities) for name in styles}, "layer_styles": styles,
            "blocks": blocks, "bbox": bbox(entities), "coordinate_systems": systems, "sheets": sheets, "viewports": viewports,
            "text_styles": text_styles,
            "annotation_scale": annotation_scale,
            "units": UNIT_NAMES[drawing.units],
            "topology": {"nodes": [{"id": e["id"], "type": e["type"], "layer": e["layer"]} for e in entities],
                         "relations": [{"from": e["id"], "to": "block:" + e["block_name"], "kind": "inserts"} for e in entities if e["type"] == "INSERT"]}}


def _attributes(item: dict[str, Any]):
    attributes = {"layer": item.get("layer", "0")}
    for source, target in (("linetype", "linetype"), ("color_index", "color"), ("true_color", "true_color"),
                           ("lineweight", "lineweight"), ("thickness", "thickness"), ("extrusion", "extrusion")):
        if source in item:
            attributes[target] = xyz(item[source]) if source == "extrusion" else item[source]
    return attributes


def _layer_model(layer):
    rgb = layer.rgb or aci2rgb(layer.color if 0 < layer.color < 256 else 7)
    return {"name": layer.dxf.name, "color": '#%02x%02x%02x' % tuple(rgb),
            "visible": not layer.is_off() and not layer.is_frozen(), "locked": layer.is_locked(), "linetype": layer.dxf.linetype}


def _dimension(layout, item, attributes, *, render=True):
    if item.get("native_definition"):
        attrs = attributes | {key: xyz(value) for key, value in item["native_definition"].items()}
        attrs.update(item.get("native_angles", {}))
        attrs.update(dimtype=item["dimension_code"], text=item.get("text", "<>"), dimstyle=item.get("dimstyle", "Standard"))
        entity = layout.new_entity("DIMENSION", dxfattribs=attrs)
        if render:
            entity.render()
        return entity
    kind = item.get("dimension_type", "linear")
    text = item.get("text") or "<>"
    style = item.get("dimstyle", "Standard")
    common = dict(text=text, dimstyle=style, dxfattribs=attributes, override={"dimtxt": 2.5, "dimasz": 2.5})
    if kind == "linear":
        start = xyz(item.get("start", item.get("definition")))
        end = xyz(item.get("end", {"x": start[0] + float(item.get("measurement", 0)), "y": start[1]}))
        if math.dist(start, end) <= 1e-12:
            raise ValueError("DIMENSION_ZERO_LENGTH")
        anchor = xyz(item.get("definition"))
        dx, dy = end[0] - start[0], end[1] - start[1]
        offset = ((anchor[0] - start[0]) * -dy + (anchor[1] - start[1]) * dx) / math.hypot(dx, dy)
        result = layout.add_aligned_dim(start, end, distance=offset, **common)
    elif kind in {"radial", "diameter"}:
        create = layout.add_radius_dim if kind == "radial" else layout.add_diameter_dim
        result = create(center=xyz(item.get("center", item.get("start"))), mpoint=xyz(item.get("end")), **common)
    elif kind == "angular":
        result = layout.add_angular_dim_3p(base=xyz(item.get("definition")), center=xyz(item.get("center")),
                                         p1=xyz(item.get("start")), p2=xyz(item.get("end")), **common)
    elif kind == "ordinate":
        start, base = xyz(item.get("start")), xyz(item.get("definition"))
        offset = tuple(b - a for a, b in zip(start, base))
        if not any(offset):
            offset = (0., 5., 0.) if item.get("axis", "x") == "x" else (5., 0., 0.)
        create = layout.add_ordinate_x_dim if item.get("axis", "x") == "x" else layout.add_ordinate_y_dim
        result = create(feature_location=start, offset=offset, origin=xyz(item.get("center")), **common)
    else:
        raise ValueError("UNSUPPORTED_DIMENSION_TYPE:" + kind)
    if render:
        result.render()
    return result.dimension


def _create(layout, item, *, render_dimensions=True):
    kind, attrs = item["type"].upper(), _attributes(item)
    if kind == "LINE": return layout.add_line(xyz(item["start"]), xyz(item["end"]), dxfattribs=attrs)
    if kind == "RAY": return layout.add_ray(xyz(item["start"]), xyz(item["unit_vector"]), dxfattribs=attrs)
    if kind == "XLINE": return layout.add_xline(xyz(item["start"]), xyz(item["unit_vector"]), dxfattribs=attrs)
    if kind == "POINT": return layout.add_point(xyz(item["point"]), dxfattribs=attrs)
    if kind == "CIRCLE": return layout.add_circle(xyz(item["center"]), item["radius"], dxfattribs=attrs)
    if kind == "ARC": return layout.add_arc(xyz(item["center"]), item["radius"], item["start_angle"], item["end_angle"], dxfattribs=attrs)
    if kind in {"TEXT", "ATTRIB", "ATTDEF"}:
        attrs.update(insert=xyz(item.get("insert")), height=item.get("height", 1), rotation=item.get("rotation", 0), style=item.get("style", "Standard"))
        for key in ("halign", "valign", "flags", "oblique", "text_generation_flag", "prompt"):
            if key in item: attrs[key] = item[key]
        if "width_factor" in item: attrs["width"] = item["width_factor"]
        if "align_point" in item: attrs["align_point"] = xyz(item["align_point"])
        if kind == "ATTDEF": return layout.add_attdef(item["tag"], text=item.get("text", ""), dxfattribs=attrs)
        if kind == "ATTRIB": raise ValueError("ATTRIB_REQUIRES_INSERT")
        return layout.add_text(item.get("text", ""), dxfattribs=attrs)
    if kind == "MTEXT":
        return layout.add_mtext(item.get("text", ""), dxfattribs=attrs | {"insert": xyz(item.get("insert")), "char_height": item.get("height", 1),
            "width": item.get("width", 0), "rotation": item.get("rotation", 0), "style": item.get("style", "Standard"), "attachment_point": item.get("attachment_point", 1)})
    if kind == "LWPOLYLINE":
        vertices, bulges, widths = item.get("vertices", []), item.get("bulges", []), item.get("widths", [])
        points = [(v["x"], v["y"], *(widths[i] if i < len(widths) else (0, 0)), bulges[i] if i < len(bulges) else 0) for i, v in enumerate(vertices)]
        return layout.add_lwpolyline(points, close=bool(item.get("closed")), dxfattribs=attrs | {"elevation": item.get("elevation", vertices[0].get("z", 0) if vertices else 0), "const_width": item.get("const_width", 0)})
    if kind == "POLYLINE":
        flags = item.get("flags", 8)
        if flags & (16 | 64): raise ValueError("POLYMESH_POLYFACE_EDIT_UNSUPPORTED")
        create = layout.add_polyline3d if flags & 8 else layout.add_polyline2d
        entity = create([xyz(v) for v in item.get("vertices", [])], close=bool(item.get("closed")), dxfattribs=attrs)
        entity.dxf.elevation = xyz(item.get("elevation"))
        for index, vertex in enumerate(entity.vertices):
            vertex.dxf.bulge = item.get("bulges", [0] * len(entity.vertices))[index]
            widths = item.get("widths", [[0, 0]] * len(entity.vertices))[index]
            vertex.dxf.start_width, vertex.dxf.end_width = widths
        return entity
    if kind == "ELLIPSE": return layout.add_ellipse(xyz(item["center"]), xyz(item["major_axis"]), item["ratio"], item.get("start_param", 0), item.get("end_param", math.tau), dxfattribs=attrs)
    if kind == "SPLINE":
        fit_points = item.get("fit_points") or item.get("vertices") or []
        entity = layout.add_spline([xyz(value) for value in fit_points] if fit_points else None,
                                   degree=int(item.get("degree", 3)), dxfattribs=attrs)
        if item.get("control_points"):
            entity.control_points = [xyz(value) for value in item["control_points"]]
        if item.get("knots"):
            entity.knots = list(item["knots"])
        if item.get("weights"):
            entity.weights = list(item["weights"])
        if "flags" in item:
            entity.dxf.flags = int(item["flags"])
        return entity
    if kind == "INSERT":
        scale = item.get("scale", {})
        entity = layout.add_blockref(item["block_name"], xyz(item.get("insert")), dxfattribs=attrs | {"xscale": scale.get("x", 1), "yscale": scale.get("y", 1), "zscale": scale.get("z", 1), "rotation": item.get("rotation", 0)} | {key: item[key] for key in ("row_count", "column_count", "row_spacing", "column_spacing") if key in item})
        for attr in item.get("attributes", []):
            attr_values = _attributes(attr) | {key: attr[key] for key in ("height", "rotation", "style", "halign", "valign", "flags", "oblique", "text_generation_flag") if key in attr}
            if "width_factor" in attr: attr_values["width"] = attr["width_factor"]
            if "align_point" in attr: attr_values["align_point"] = xyz(attr["align_point"])
            entity.add_attrib(attr["tag"], attr.get("text", ""), xyz(attr.get("insert")), dxfattribs=attr_values)
        return entity
    if kind == "DIMENSION": return _dimension(layout, item, attrs, render=render_dimensions)
    if kind == "LEADER": return layout.add_leader([xyz(v) for v in item.get("vertices", [])], dimstyle=item.get("dimstyle", "Standard"), dxfattribs=attrs | {"has_arrowhead": int(item.get("arrowhead", True))})
    if kind == "HATCH":
        entity = layout.add_hatch(dxfattribs=attrs)
        if "associative" in item:
            entity.dxf.associative = int(bool(item.get("associative")))
        if item.get("solid", True): entity.set_solid_fill(style=item.get("hatch_style", 0))
        else: entity.set_pattern_fill(item.get("pattern", "ANSI31"), angle=item.get("pattern_angle", 0), scale=item.get("pattern_scale", 1), double=int(item.get("double", False)), style=item.get("hatch_style", 0), pattern_type=item.get("pattern_type", 1))
        _set_hatch_paths(entity, item)
        entity.dxf.elevation = xyz(item.get("elevation"))
        if item.get("pattern_lines"):
            entity.set_pattern_definition(item["pattern_lines"])
        return entity
    raise ValueError("UNSUPPORTED_NATIVE_DXF_ENTITY:" + kind)


def _set_hatch_paths(entity, item):
    from ezdxf.entities.boundary_paths import BoundaryPaths
    entity.paths = BoundaryPaths()
    paths = item.get("boundary_paths") or [{"kind": "polyline", "vertices": item.get("boundary_vertices", []), "closed": True, "flags": 1}]
    for data in paths:
        if data["kind"] == "polyline":
            path = entity.paths.add_polyline_path([(v["x"], v["y"], v.get("bulge", 0)) for v in data["vertices"]], is_closed=data.get("closed", True), flags=data.get("flags", 1))
        else:
            path = entity.paths.add_edge_path(flags=data.get("flags", 1))
            for edge in data["edges"]:
                kind = edge["kind"]
                if kind == "LineEdge":
                    path.add_line(xyz(edge["start"])[:2], xyz(edge["end"])[:2])
                elif kind == "ArcEdge":
                    path.add_arc(xyz(edge["center"])[:2], edge["radius"], edge["start_angle"], edge["end_angle"], ccw=edge["ccw"])
                elif kind == "EllipseEdge":
                    path.add_ellipse(xyz(edge["center"])[:2], xyz(edge["major_axis"])[:2], edge["ratio"], edge["start_angle"], edge["end_angle"], ccw=edge["ccw"])
                elif kind == "SplineEdge":
                    path.add_spline(**{key: ([xyz(v)[:2] for v in value] if key in {"control_points", "fit_points"} else xyz(value)[:2] if key.endswith("tangent") else value)
                                      for key, value in edge.items() if key not in {"kind", "rational"}})
                else:
                    raise ValueError("UNSUPPORTED_HATCH_EDGE:" + kind)
        path.source_boundary_objects = list(data.get("source_handles", []))


def transform_model(item, *, dx=0, dy=0, dz=0, scale=1., angle=0., center=None, mirror_axis=None,
                    allow_associative=False):
    """Use native OCS/WCS transforms, including linked insert attributes.

    This temporary drawing computes semantic geometry only. Saving patches the
    original drawing, so untouched extension dictionaries and resources survive.
    """
    if item["type"] not in {"LINE", "RAY", "XLINE", "POINT", "CIRCLE", "ARC", "TEXT", "MTEXT", "POLYLINE", "LWPOLYLINE", "ELLIPSE", "SPLINE", "HATCH", "INSERT", "DIMENSION", "LEADER"}:
        raise ValueError("UNSUPPORTED_ENTITY_TRANSFORM:" + item["type"])
    if item["type"] == "HATCH" and item.get("associative") and not allow_associative:
        raise ValueError("ASSOCIATIVE_HATCH_TRANSFORM_UNSUPPORTED")
    if not math.isfinite(scale) or scale == 0:
        raise ValueError("INVALID_TRANSFORM_SCALE")
    cx, cy, cz = xyz(center)
    matrices = [Matrix44.translate(-cx, -cy, -cz), Matrix44.scale(scale), Matrix44.z_rotate(math.radians(angle)), Matrix44.translate(cx + dx, cy + dy, cz + dz)]
    if mirror_axis:
        if mirror_axis not in {"x", "y"}:
            raise ValueError("INVALID_MIRROR_AXIS")
        matrices += [Matrix44.translate(-cx, -cy, -cz), Matrix44.scale(-1 if mirror_axis == "y" else 1, -1 if mirror_axis == "x" else 1, 1), Matrix44.translate(cx, cy, cz)]
    drawing = ezdxf.new("R2013")
    entity = _create(drawing.modelspace(), item, render_dimensions=False)
    entity.transform(Matrix44.chain(*matrices))
    model = entity_model(entity)
    for key in ("id", "handle", "owner"):
        model.pop(key, None)
    if item["type"] == "INSERT":
        for old, new in zip(item.get("attributes", []), model.get("attributes", [])):
            for key in ("id", "handle", "owner"):
                if key in old: new[key] = old[key]
    return copy.deepcopy(item) | model


def _patch(entity, item, before):
    """Update familiar attributes in-place, retaining handles and extensions."""
    kind = entity.dxftype()
    dxf = entity.dxf
    for key, value in _attributes(item).items():
        if dxf.is_supported(key): dxf.set(key, value)
    fields = {"LINE": {"start": "start", "end": "end"}, "RAY": {"start": "start", "unit_vector": "unit_vector"}, "XLINE": {"start": "start", "unit_vector": "unit_vector"}, "POINT": {"point": "location"},
              "CIRCLE": {"center": "center", "radius": "radius"}, "ARC": {"center": "center", "radius": "radius", "start_angle": "start_angle", "end_angle": "end_angle"},
              "TEXT": {"insert": "insert", "height": "height", "text": "text", "rotation": "rotation", "style": "style", "halign": "halign", "valign": "valign", "align_point": "align_point", "width_factor": "width", "oblique": "oblique", "text_generation_flag": "text_generation_flag"},
              "ELLIPSE": {"center": "center", "major_axis": "major_axis", "ratio": "ratio", "start_param": "start_param", "end_param": "end_param"}}
    fields["ATTRIB"] = fields["TEXT"] | {"tag": "tag", "flags": "flags"}
    fields["ATTDEF"] = fields["TEXT"] | {"tag": "tag", "flags": "flags", "prompt": "prompt"}
    if kind in fields:
        for source, dest in fields[kind].items():
            if source in item: dxf.set(dest, xyz(item[source]) if isinstance(item[source], dict) else item[source])
        return
    if kind == "MTEXT":
        entity.text = item.get("text", "")
        dxf.update({"insert": xyz(item.get("insert")), "char_height": item.get("height", 1), "width": item.get("width", 0), "style": item.get("style", "Standard"), "attachment_point": item.get("attachment_point", 1)})
        entity.set_rotation(item.get("rotation", 0))
        return
    if kind == "LWPOLYLINE":
        bulges, widths = item.get("bulges", []), item.get("widths", [])
        entity.set_points([(v["x"], v["y"], *(widths[i] if i < len(widths) else (0, 0)), bulges[i] if i < len(bulges) else 0) for i, v in enumerate(item["vertices"])])
        entity.closed = bool(item.get("closed"))
        dxf.elevation = item.get("elevation", item["vertices"][0].get("z", 0) if item["vertices"] else 0)
        dxf.const_width = item.get("const_width", 0)
        return
    if kind == "HATCH":
        if before.get("associative"):
            before_refs = [str(handle) for path in before.get("boundary_paths", []) for handle in path.get("source_handles", [])]
            item_refs = [str(handle) for path in item.get("boundary_paths", []) for handle in path.get("source_handles", [])]
            if item.get("associative", before.get("associative")) and item_refs != before_refs:
                raise ValueError("ASSOCIATIVE_HATCH_SOURCE_EDIT_UNSUPPORTED")
        _set_hatch_paths(entity, item)
        dxf.elevation = xyz(item.get("elevation"))
        if "associative" in item:
            dxf.associative = int(bool(item.get("associative")))
        solid_changed = item.get("solid") != before.get("solid")
        pattern_changed = item.get("pattern") != before.get("pattern")
        style_changed = item.get("hatch_style") != before.get("hatch_style")
        scale_changed = item.get("pattern_scale") != before.get("pattern_scale")
        angle_changed = item.get("pattern_angle") != before.get("pattern_angle")
        double_changed = item.get("double") != before.get("double")
        if item.get("solid", before.get("solid", True)):
            if solid_changed or style_changed:
                entity.set_solid_fill(style=item.get("hatch_style", before.get("hatch_style", 0)))
        elif pattern_changed or style_changed or double_changed:
            entity.set_pattern_fill(item.get("pattern", before.get("pattern", "ANSI31")),
                                    scale=item.get("pattern_scale", before.get("pattern_scale", 1)),
                                    angle=item.get("pattern_angle", before.get("pattern_angle", 0)),
                                    double=int(item.get("double", before.get("double", False))),
                                    style=item.get("hatch_style", before.get("hatch_style", 0)),
                                    pattern_type=item.get("pattern_type", before.get("pattern_type", 1)))
        else:
            if scale_changed:
                entity.set_pattern_scale(float(item.get("pattern_scale", 1)))
            if angle_changed:
                entity.set_pattern_angle(float(item.get("pattern_angle", 0)))
        for key, dest in (("hatch_style", "hatch_style"), ("pattern_type", "pattern_type"), ("pattern_scale", "pattern_scale"), ("pattern_angle", "pattern_angle"), ("double", "pattern_double")):
            if key in item: dxf.set(dest, item[key])
        if item.get("pattern_lines"):
            entity.set_pattern_definition(item["pattern_lines"])
        return
    if kind == "INSERT":
        dxf.update({"insert": xyz(item["insert"]), "name": item["block_name"], "rotation": item.get("rotation", 0),
                    **{axis + "scale": item.get("scale", {}).get(axis, 1) for axis in ("x", "y", "z")}})
        dxf.update({key: item[key] for key in ("row_count", "column_count", "row_spacing", "column_spacing") if key in item})
        attrs = item.get("attributes", [])
        wanted_tags = [str(a.get("tag", "")) for a in attrs]
        existing_tags = [str(a.dxf.tag) for a in entity.attribs]
        for tag in existing_tags:
            if tag not in wanted_tags:
                entity.delete_attrib(tag, ignore=True)
        existing_by_tag = {str(a.dxf.tag): a for a in entity.attribs}
        for model in attrs:
            tag = str(model.get("tag", ""))
            native = existing_by_tag.get(tag)
            if native is None:
                native = entity.add_attrib(tag, model.get("text", ""), xyz(model.get("insert")), dxfattribs=_attributes(model) | {key: model[key] for key in ("height", "rotation", "style", "halign", "valign", "flags", "oblique", "text_generation_flag") if key in model})
            _patch(native, model, entity_model(native))
        return
    if kind == "DIMENSION":
        dxf.text = item.get("text", "<>")
        dxf.dimstyle = item.get("dimstyle", "Standard")
        if item.get("native_definition"):
            dxf.update({key: xyz(value) for key, value in item["native_definition"].items()})
            dxf.update(item.get("native_angles", {}))
        # Explicit semantic edits override the corresponding native points.
        mapping = {"definition": "defpoint"}
        if entity.dimtype in {0, 1}: mapping |= {"start": "defpoint2", "end": "defpoint3"}
        elif entity.dimtype == 4: mapping |= {"center": "defpoint", "end": "defpoint4"}
        elif entity.dimtype == 5: mapping |= {"center": "defpoint4", "start": "defpoint2", "end": "defpoint3"}
        elif entity.dimtype == 6: mapping |= {"center": "defpoint", "start": "defpoint2"}
        for key, dest in mapping.items():
            if key in item and item[key] != before.get(key): dxf.set(dest, xyz(item[key]))
        if item.get("angle") != before.get("angle") and "angle" in item: dxf.angle = item["angle"]
        entity.render()
        return
    if kind == "LEADER":
        entity.set_vertices([xyz(v) for v in item["vertices"]])
        dxf.has_arrowhead = int(item.get("arrowhead", True))
        dxf.dimstyle = item.get("dimstyle", "Standard")
        return
    if kind == "POLYLINE":
        if dxf.flags & (16 | 64) or len(entity.vertices) != len(item["vertices"]):
            raise ValueError("POLYLINE_STRUCTURE_EDIT_UNSUPPORTED")
        for index, (native, vertex) in enumerate(zip(entity.vertices, item["vertices"])):
            native.dxf.location = xyz(vertex)
            native.dxf.bulge = item.get("bulges", [0] * len(entity.vertices))[index]
            native.dxf.start_width, native.dxf.end_width = item.get("widths", [[0, 0]] * len(entity.vertices))[index]
        entity.close(bool(item.get("closed")))
        dxf.elevation = xyz(item.get("elevation"))
        return
    if kind == "SPLINE":
        # ezdxf exposes spline point data through typed sequence properties;
        # assigning the complete sequence preserves the native entity handle
        # while allowing transforms and intentional control-point edits.
        if "degree" in item and int(item["degree"]) != int(before.get("degree", dxf.get("degree", 3))):
            dxf.degree = int(item["degree"])
        if "flags" in item and int(item["flags"]) != int(before.get("flags", dxf.get("flags", 0))):
            dxf.flags = int(item["flags"])
        for key, converter in (("fit_points", lambda values: [xyz(value) for value in values]),
                               ("control_points", lambda values: [xyz(value) for value in values]),
                               ("knots", lambda values: [float(value) for value in values]),
                               ("weights", lambda values: [float(value) for value in values])):
            if key in item and item.get(key) != before.get(key):
                setattr(entity, key, converter(item.get(key) or []))
        return
    # Changes to structures with linked objects require dedicated handlers;
    # leave them untouched when only application metadata changed.
    native_keys = set(before) - {"id", "handle", "owner"}
    if any(item.get(key) != before.get(key) for key in native_keys):
        raise ValueError("UNSUPPORTED_NATIVE_ENTITY_EDIT:" + kind)


def write_native(path: str | Path, *, entities: list[dict[str, Any]], blocks=(), coordinate_systems=(), layers=None,
                 sheets=None, viewports=None, text_styles=None, annotation_scale=None, source: str | Path | None = None, units: str = "mm") -> str:
    drawing = build_native(entities=entities, blocks=blocks, coordinate_systems=coordinate_systems, layers=layers,
                           sheets=sheets, viewports=viewports, text_styles=text_styles, annotation_scale=annotation_scale, source=source, units=units)
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    drawing.saveas(target)
    return str(target)


def build_native(*, entities: list[dict[str, Any]], blocks=(), coordinate_systems=(), layers=None,
                 sheets=None, viewports=None, text_styles=None, annotation_scale=None, source: str | Path | None = None, units: str = "mm"):
    """Materialize the current editable model without writing a file.

    Synchronization assigns handles to the supplied models; callers that only
    inspect a draft must pass a clone.
    """
    drawing = ezdxf.readfile(source) if source is not None else ezdxf.new("R2013", setup=True)
    # Preserve the source version. Unsupported new entity types are rejected
    # by ezdxf instead of silently converting a supplier's drawing.
    if units not in UNIT_NAMES: raise ValueError("UNSUPPORTED_DRAWING_UNITS:" + units)
    drawing.units = UNIT_NAMES.index(units)
    if annotation_scale is not None:
        try:
            annotation_scale = float(annotation_scale)
        except (TypeError, ValueError) as exc:
            raise ValueError("ANNOTATION_SCALE_INVALID") from exc
        if not math.isfinite(annotation_scale) or annotation_scale <= 0:
            raise ValueError("ANNOTATION_SCALE_INVALID")
        drawing.header["$DIMSCALE"] = annotation_scale
    if text_styles is not None:
        for name, properties in text_styles.items():
            style_name = str(name).strip()
            if not style_name:
                raise ValueError("TEXT_STYLE_NAME_REQUIRED")
            style = drawing.styles.get(style_name) if drawing.styles.has_entry(style_name) else drawing.styles.new(style_name)
            values = {key: properties[key] for key in ("font", "bigfont", "height", "width", "oblique", "flags") if key in properties}
            if values:
                style.dxf.update(values)
    layer_names = set(layers or {}) | {e.get("layer", "0") for e in entities}
    for block in blocks:
        layer_names.update(e.get("layer", "0") for e in block.get("entities", []))
    for name in layer_names:
        created = name not in drawing.layers
        if created: drawing.layers.new(name)
        properties = (layers or {}).get(name)
        if properties:
            layer = drawing.layers.get(name)
            before = {} if created else _layer_model(layer)
            if properties.get("color") != before.get("color"):
                rgb = str(properties.get("color", "#ffffff")).lstrip("#")
                layer.rgb = tuple(int(rgb[i:i + 2], 16) for i in (0, 2, 4))
            if properties.get("visible", True) != before.get("visible"):
                if properties.get("visible", True):
                    layer.on(); layer.thaw()
                else: layer.off()
            if properties.get("locked", False) != before.get("locked"):
                layer.lock() if properties.get("locked") else layer.unlock()
            if properties.get("linetype", "CONTINUOUS") != before.get("linetype"):
                line_type = properties.get("linetype", "CONTINUOUS")
                if line_type not in drawing.linetypes:
                    raise ValueError("LINETYPE_NOT_FOUND:" + line_type)
                layer.dxf.linetype = line_type
    if source is not None and sheets is not None:
        for layout in list(drawing.layouts):
            if layout.name != "Model" and layout.name not in sheets:
                drawing.layouts.delete(layout.name)
    for name, properties in (sheets or {}).items():
        layout = drawing.layouts.get(name) if name in drawing.layouts else drawing.layouts.new(name)
        existing = layout.dxf_layout.dxf
        size = (float(properties["width"]), float(properties["height"]))
        factor = 25.4 if properties.get("units") == "in" else 1.
        setup = properties.get("page_setup") or {}
        margins = setup.get("margins", [0, 0, 0, 0])
        if isinstance(margins, dict):
            margins = [margins.get(key, 0) for key in ("left", "bottom", "right", "top")]
        # ezdxf.page_setup expects (top, right, bottom, left), while the
        # SimLoom document contract is (left, bottom, right, top).
        margins = tuple(float(value) for value in (margins[3], margins[2], margins[1], margins[0]))
        offset = tuple(float(value) for value in setup.get("offset", [0, 0]))
        scale = setup.get("scale", 1)
        if isinstance(scale, list):
            scale = tuple(float(value) for value in scale)
        rotation = int(setup.get("rotation", 1 if str(setup.get("orientation", "")).lower() == "portrait" else 0))
        device = str(setup.get("device", existing.get("plot_configuration_file", "ezdxf")))
        dimensions_changed = not all(math.isclose(a, b * factor) for a, b in zip((existing.paper_width, existing.paper_height), size))
        if setup.get("managed") or dimensions_changed:
            layout.page_setup(size=size, margins=margins, units="inch" if properties.get("units") == "in" else properties.get("units", "mm"),
                              offset=offset, rotation=rotation, scale=scale, device=device)
    for block in blocks:
        if block["name"] not in drawing.blocks:
            drawing.blocks.new(block["name"], base_point=xyz(block.get("base_point")))
    for block in blocks:
        layout = drawing.blocks.get(block["name"])
        layout.block.dxf.base_point = xyz(block.get("base_point"))
        _sync_entities(drawing, block.get("entities", []), {block["name"]: layout}, block["name"])
    layouts = {layout.name: layout for layout in drawing.layouts}
    _sync_entities(drawing, entities, layouts, "Model")
    _set_redraw_order(drawing, entities, layouts)
    if source is not None:
        keep_blocks = {b["name"] for b in blocks}
        for block in list(drawing.blocks):
            if not block.is_any_layout and not block.name.startswith("*") and block.name not in keep_blocks:
                drawing.blocks.delete_block(block.name, safe=True)
        if layers is not None:
            for layer in list(drawing.layers):
                name = layer.dxf.name
                if name not in layers and name not in {"0", "Defpoints"}:
                    if any(e.dxf.get("layer") == name for e in drawing.entitydb.values() if e.is_alive and e.dxf.is_supported("layer")):
                        raise ValueError("LAYER_STILL_REFERENCED:" + name)
                    drawing.layers.remove(name)
    if viewports is not None:
        keep_viewports = {p.get("handle") for p in viewports.values()}
        for layout in drawing.layouts:
            for entity in list(layout.query("VIEWPORT")):
                if entity.dxf.id != 1 and entity.dxf.handle not in keep_viewports:
                    layout.delete_entity(entity)
    for name, props in (viewports or {}).items():
        layout = drawing.layouts.get(props["sheet"])
        entity = drawing.entitydb.get(props.get("handle", ""))
        height, scale = float(props["height"]), float(props["scale"])
        if entity is None or not entity.is_alive:
            entity = layout.add_viewport(center=xyz(props["center"]), size=(props["width"], height), view_center_point=xyz(props.get("view_center", props["center"]))[:2], view_height=height / scale)
        else:
            if entity.get_layout() is not layout:
                entity.get_layout().move_to_layout(entity, layout)
            entity.dxf.update({"center": xyz(props["center"]), "width": props["width"], "height": height, "view_height": height / scale,
                               "view_center_point": xyz(props.get("view_center", props["center"]))[:2]})
        entity.dxf.flags = entity.dxf.flags | 16384 if props.get("locked") else entity.dxf.flags & ~16384
        if name != entity.dxf.handle or entity.has_xdata(APPID):
            stamp(entity, name)
        props["handle"] = entity.dxf.handle
    for system in coordinate_systems:
        name = system.get("name", "UCS")
        if name == "WCS": continue
        values = {"origin": xyz(system.get("origin")), "xaxis": xyz(system.get("x_axis", {"x": 1})), "yaxis": xyz(system.get("y_axis", {"y": 1}))}
        if name in drawing.ucs: drawing.ucs.get(name).dxf.update(values)
        else: drawing.ucs.new(name, dxfattribs=values)
    if source is not None:
        keep_systems = {s.get("name") for s in coordinate_systems}
        for system in list(drawing.ucs):
            if system.dxf.name not in keep_systems:
                drawing.ucs.remove(system.dxf.name)
    return drawing


def _set_redraw_order(drawing, models, layouts):
    """Persist canonical entity order through DXF SORTENTS tables.

    DXF entity storage order and visual redraw order are separate.  Keeping
    the canonical order in the layout's redraw table makes draw-order edits
    survive a native write/read cycle without rebuilding or changing entity
    handles.  The model list is already grouped by layout by ``_sync_entities``.
    """
    grouped = {}
    for item in models:
        grouped.setdefault(str(item.get("sheet", "Model")), []).append(item)
    for layout_name, items in grouped.items():
        layout = layouts.get(layout_name)
        if layout is None:
            raise ValueError("CAD_LAYOUT_NOT_FOUND:" + layout_name)
        handles = [str(item.get("handle", "")) for item in items if item.get("handle")]
        if not handles:
            continue
        # Sort handles provide deterministic numeric slots.  Assigning those
        # slots in the requested model order creates a valid SORTENTS table
        # even when the desired order differs from native handle order.
        slots = sorted(handles, key=lambda value: int(value, 16) if value else -1)
        layout.set_redraw_order([(handle, slot) for handle, slot in zip(handles, slots)])
        drawing.header["$SORTENTS"] = int(drawing.header.get("$SORTENTS", 0)) | 16


def _sync_entities(drawing, models, layouts, default):
    native = {e.dxf.handle: (layout, e) for layout in layouts.values() for e in layout if e.dxftype() != "VIEWPORT"}
    kept = set()
    for index, item in enumerate(models, 1):
        item.setdefault("id", item.get("handle") or f"e{index:05d}")
        name = item.get("sheet", default)
        if name not in layouts: raise ValueError("CAD_LAYOUT_NOT_FOUND:" + name)
        target = layouts[name]
        existing = native.get(item.get("handle"))
        if existing and entity_id(existing[1]) == item["id"] and existing[1].dxftype() == item["type"]:
            old_layout, entity = existing
            before = entity_model(entity)
            before.pop("sheet", None)
            comparable = {key: item.get(key) for key in before}
            added_attributes = set(_attributes(item)) - set(entity.dxf.all_existing_dxf_attribs())
            if comparable != before or added_attributes: _patch(entity, item, before)
            if old_layout is not target:
                old_layout.move_to_layout(entity, target)
            kept.add(entity.dxf.handle)
        else:
            entity = _create(target, item)
        if str(item["id"]) != entity.dxf.handle or entity.has_xdata(APPID):
            stamp(entity, str(item["id"]))
        if item.get("title_block_id") and entity.dxftype() == "INSERT":
            from .title_blocks import TITLE_APPID
            if TITLE_APPID not in drawing.appids:
                drawing.appids.new(TITLE_APPID)
            tags = [(1000, "v1"), (1000, item["title_block_id"])]
            for tag, width in item.get("title_field_widths", {}).items():
                tags.extend([(1000, tag), (1040, width)])
            entity.set_xdata(TITLE_APPID, tags)
        item["handle"] = entity.dxf.handle
        item["owner"] = entity.dxf.owner
    for handle, (layout, entity) in native.items():
        if handle not in kept:
            layout.delete_entity(entity)
