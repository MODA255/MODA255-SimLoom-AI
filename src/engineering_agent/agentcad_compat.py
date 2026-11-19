"""AgentCAD operation names accepted by the SimLoom operation gateway.

The compatibility layer only changes the public operation name and a small
set of well-known parameter spellings.  Execution still goes through the
canonical SimLoom CAD operation, transaction, validator and history path.
"""
from __future__ import annotations

import copy
from typing import Any


# AgentCAD's public operation catalog is intentionally flat.  SimLoom keeps
# its richer ``cad.*`` names so the same engine can also expose project,
# geometry, B-Rep and vendor operations without name collisions.
AGENTCAD_OPERATION_ALIASES: dict[str, str] = {
    "add_angular_dim": "cad.dimension_angular_create",
    "add_center_mark": "cad.center_mark_create",
    "add_diameter_dim": "cad.dimension_diameter_create",
    "add_leader": "cad.leader_create",
    "add_linear_dim": "cad.dimension_linear_create",
    "add_mtext": "cad.mtext_create",
    "add_ordinate_dim": "cad.dimension_ordinate_create",
    "add_radius_dim": "cad.dimension_radial_create",
    "add_table": "cad.table_create",
    "add_text": "cad.text_create",
    "add_title_block": "cad.title_block_create",
    "create_text_style": "cad.create_text_style",
    "set_annotation_scale": "cad.set_annotation_scale",
    "block_from_entities": "cad.block_from_entities",
    "define_block": "cad.block_create",
    "insert_block": "cad.insert_create",
    "set_attributes": "cad.block_attribute_create",
    "add_arc": "cad.arc_create",
    "add_circle": "cad.circle_create",
    "add_donut": "cad.donut_create",
    "add_ellipse": "cad.ellipse_create",
    "add_hatch": "cad.hatch_create",
    "add_line": "cad.line_create",
    "add_point": "cad.point_create",
    "add_polygon": "cad.polygon_create",
    "add_polyline": "cad.polyline_create",
    "add_ray": "cad.ray_create",
    "add_rectangle": "cad.rectangle_create",
    "add_spline": "cad.spline_create",
    "add_xline": "cad.xline_create",
    "trace_boundary": "cad.trace_boundary",
    "measure_entities": "cad.measure_entities",
    "array_polar": "cad.entity_array_polar",
    "array_rect": "cad.entity_array_rectangular",
    "break": "cad.entity_break",
    "chamfer": "cad.entity_chamfer",
    "copy": "cad.entity_copy",
    "delete": "cad.entity_delete",
    "draw_order": "cad.draw_order",
    "edit_hatch": "cad.edit_hatch",
    "explode": "cad.entity_explode",
    "extend": "cad.entity_extend",
    "fillet": "cad.entity_fillet",
    "join": "cad.entity_join",
    "mirror": "cad.entity_mirror",
    "modify": "cad.entity_update",
    "move": "cad.entity_move",
    "offset": "cad.entity_offset",
    "purge": "cad.entity_purge",
    "remove_duplicates": "cad.entity_remove_duplicates",
    "rotate": "cad.entity_rotate",
    "scale": "cad.entity_scale",
    "set_properties": "cad.entity_update",
    "stretch": "cad.entity_stretch",
    "trim": "cad.entity_trim",
    "draw_arch_plan": "cad.draw_arch_plan",
    "draw_mechanical_assembly": "cad.draw_mechanical_assembly",
    "draw_pid": "cad.draw_pid",
    "create_layer": "cad.layer_create",
    "delete_layer": "cad.layer_delete",
    "modify_layer": "cad.layer_update",
    "add_viewport": "cad.viewport_create",
    "change_space": "cad.change_space",
    "create_layout": "cad.sheet_create",
    "set_page_setup": "cad.set_page_setup",
    "add_datum_feature": "cad.standard_symbol_create",
    "add_feature_control_frame": "cad.gdt_frame_create",
    "add_pid_symbol": "cad.pid_symbol_create",
    "add_process_line": "cad.process_line_create",
    "add_standard_hole": "cad.hole_create",
    "add_standard_part": "cad.fastener_create",
    "add_surface_finish": "cad.surface_texture_create",
}


# AgentCAD uses concise geometry names.  These aliases preserve the original
# values when a caller also sends the SimLoom spelling; the canonical spelling
# always wins so mixed clients remain deterministic.
PARAMETER_ALIASES: dict[str, dict[str, str]] = {
    "add_angular_dim": {"p1": "start", "p2": "end", "text_height": "height"},
    "add_linear_dim": {"p1": "start", "p2": "end", "text_height": "height"},
    "add_ordinate_dim": {"text_height": "height"},
    "add_radius_dim": {"p": "point", "text_height": "height"},
    "add_diameter_dim": {"p": "point", "text_height": "height"},
    "add_text": {"point": "insert", "text_height": "height"},
    "add_mtext": {"point": "insert", "text_height": "height"},
    "add_leader": {"points": "vertices"},
    "add_hatch": {"boundary_points": "boundary", "pattern_name": "pattern"},
    "add_polyline": {"points": "vertices"},
    "add_spline": {"points": "fit_points"},
    "add_rectangle": {"corner1": "first_corner", "corner2": "second_corner"},
    "add_circle": {"point": "center"},
    "add_arc": {"point": "center"},
    "add_ellipse": {"point": "center"},
    "add_point": {"position": "point"},
    "add_ray": {"point": "start", "vector": "direction"},
    "add_xline": {"point": "start", "vector": "direction"},
    "add_line": {"p1": "start", "p2": "end"},
    "insert_block": {"name": "block_name", "point": "insert"},
    "move": {"translation": "delta", "vector": "delta"},
    "rotate": {"angle_deg": "angle"},
    "scale": {"factor": "scale"},
    "mirror": {"mirror_axis": "axis"},
    "modify": {"properties": "patch"},
    "set_properties": {"properties": "patch"},
    "create_layout": {"name": "sheet"},
    "modify_layer": {"layer": "name"},
    "delete_layer": {"layer": "name"},
}


def resolve_operation(name: str) -> str:
    """Return the canonical SimLoom operation for a public operation name."""
    return AGENTCAD_OPERATION_ALIASES.get(str(name), str(name))


def normalize_operation(name: str, arguments: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], bool]:
    """Resolve an alias and copy its compatible parameter spellings.

    Returns ``(canonical_name, normalized_arguments, was_alias)``.  No input
    dictionary is modified in place.
    """
    requested = str(name)
    canonical = resolve_operation(requested)
    values = copy.deepcopy(arguments or {})
    for source, destination in PARAMETER_ALIASES.get(requested, {}).items():
        if source in values and destination not in values:
            values[destination] = values[source]
    # AgentCAD calls the CAD document context ``document`` in a few clients;
    # SimLoom's file based contract uses input/output_file.
    if "document" in values and "input_file" not in values:
        values["input_file"] = values["document"]
    if "output" in values and "output_file" not in values:
        values["output_file"] = values["output"]
    return canonical, values, canonical != requested


def alias_names() -> tuple[str, ...]:
    return tuple(sorted(AGENTCAD_OPERATION_ALIASES))
