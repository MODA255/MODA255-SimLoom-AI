import json
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument, EntityRef, validate_cad_document
from engineering_agent.formats import write_dxf
from engineering_agent.dxf import read_dxf
from engineering_agent.viewer import asset_view


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.dxf"
    write_dxf(path, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0, "layer": "FRAME"}], circles=[{"x": 5, "y": 5, "radius": 2, "layer": "WHEEL"}])
    return path


def test_cad_document_has_stable_refs_layers_and_selection(tmp_path):
    source = _source(tmp_path)
    document = CadDocument.open(source)
    assert document.entities[0]["id"] == "e00001"
    assert EntityRef(document.document_id, "e00001").to_dict()["entity_id"] == "e00001"
    document.selection_sets["picked"] = __import__("engineering_agent.cad_model", fromlist=["SelectionSet"]).SelectionSet("picked", ["e00001"])
    assert document.to_dict()["selection_sets"]["picked"]["refs"] == ["e00001"]
    assert validate_cad_document(document)["valid"]


def test_cad_batch_is_transactional_and_emits_diff_and_sidecar(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "edited.dxf"
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("cad.batch_apply", {"project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output), "operations": [
        {"operation": "cad.layer_create", "name": "ANNOTATION", "color": "#b35c00"},
        {"operation": "cad.entity_move", "entity_ids": ["e00001"], "delta": [2, 3, 0]},
        {"operation": "cad.entity_create", "entity": {"type": "TEXT", "layer": "ANNOTATION", "insert": {"x": 1, "y": 1}, "text": "mount"}},
    ]})
    assert result["validation"]["valid"]
    assert result["diff"]["change_count"] >= 2
    assert output.is_file()
    sidecar = Path(str(output) + ".cad.json")
    assert sidecar.is_file()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["entity_ids"]
    assert CadDocument.open(output).entities[0]["start"]["x"] == 2


def test_cad_repair_replans_invalid_geometry(tmp_path):
    source = tmp_path / "invalid.dxf"
    output = tmp_path / "repaired.dxf"
    write_dxf(source, lines=[{"x1": 1, "y1": 1, "x2": 1, "y2": 1}])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.validate_and_repair", {"project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output)})
    assert result["validation"]["valid"]
    assert result["repair_plan"][0]["operation"] == "cad.entity_delete"
    assert result["results"][0]["replanned"] is True


def test_interactive_dxf_viewer_has_selection_layers_and_replay(tmp_path):
    source = _source(tmp_path)
    result = asset_view(tmp_path, source.name)
    from xml.etree import ElementTree as ET
    assert ET.fromstring(result["svg"]).find(".//*[@data-entity-id='e00001']") is not None
    assert "data-layer='FRAME'" in result["viewer"]
    assert "id='replay'" in result["viewer"]
    assert "data-layer='WHEEL'" in result["viewer"]


def test_interactive_viewer_exposes_transactional_edit_controls(tmp_path):
    source = _source(tmp_path)
    viewer = asset_view(tmp_path, source.name)["viewer"]
    assert "data-asset-path='source.dxf'" in viewer
    assert "id='move'" in viewer and "id='rotate'" in viewer
    assert "id='delete'" in viewer and "id='updateText'" in viewer
    assert "id='saveSelection'" in viewer and "cad.selection_create" in viewer
    assert "cad.entity_move" in viewer and "cad.entity_update" in viewer
    assert "payload.entity_id=ids[0]" in viewer
    assert "postMessage" in viewer


def test_viewer_edit_can_commit_in_place_and_reopen(tmp_path):
    source = _source(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    moved = agent.execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
        "entity_ids": ["e00001"], "delta": [3, 4, 0],
    })
    assert moved["persisted_validation"]["valid"]
    document = CadDocument.open(source)
    assert document.entities[0]["start"]["x"] == 3
    assert document.entities[0]["start"]["y"] == 4
    selected = agent.execute_operation("cad.selection_create", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
        "name": "picked", "entity_ids": ["e00001"],
    })
    assert selected["persisted_validation"]["valid"]
    assert CadDocument.open(source).selection_sets["picked"].refs == ["e00001"]


def test_dxf_styles_and_proxy_entities_survive_semantic_round_trip(tmp_path):
    styled = tmp_path / "styled.dxf"
    write_dxf(styled, entities=[{
        "type": "LINE", "layer": "FRAME", "linetype": "CENTER", "color_index": 3,
        "start": {"x": 0, "y": 0, "z": 0}, "end": {"x": 5, "y": 0, "z": 0},
    }])
    document = CadDocument.open(styled)
    assert document.entities[0]["linetype"] == "CENTER"
    assert document.entities[0]["color_index"] == 3

    proxy = tmp_path / "proxy.dxf"
    proxy.write_text("\n".join([
        "0", "SECTION", "2", "ENTITIES", "0", "ACAD_PROXY_ENTITY", "5", "AB12",
        "8", "FRAME", "100", "AcDbEntity", "90", "17", "100", "AcDbProxyEntity", "310", "AABB",
        "330", "A1", "100", "VendorSubclass", "310", "CCDD", "330", "A2",
        "0", "LINE", "5", "C1", "8", "FRAME", "10", "0", "20", "0", "11", "5", "21", "0",
        "0", "ENDSEC", "0", "EOF", "",
    ]), encoding="utf-8")
    output = tmp_path / "proxy-copy.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(proxy), "output_file": str(output),
        "entity_id": "C1", "delta": [1, 0, 0],
    })
    assert result["persisted_validation"]["valid"]
    persisted = read_dxf(output)["entities"][0]
    assert persisted["type"] == "ACAD_PROXY_ENTITY"
    assert persisted["handle"] == "AB12"
    assert persisted["raw_groups"]["310"] == ["AABB", "CCDD"]
    assert persisted["raw_tags"] == read_dxf(proxy)["entities"][0]["raw_tags"]
    before = output.read_bytes()
    with pytest.raises(ValueError, match="UNSUPPORTED_ENTITY_TRANSFORM"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.entity_move", {
            "project_dir": str(tmp_path), "input_file": str(output), "output_file": str(output),
            "entity_id": "AB12", "delta": [1, 0, 0],
        })
    assert output.read_bytes() == before


def test_lwpolyline_bulges_round_trip(tmp_path):
    source = tmp_path / "bulge.dxf"
    write_dxf(source, entities=[{
        "type": "LWPOLYLINE", "layer": "PROFILE", "closed": True,
        "vertices": [{"x": 0, "y": 0}, {"x": 10, "y": 0}], "bulges": [1.0, 0.0],
    }])
    output = tmp_path / "bulge-copy.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "entity_id": "e00001", "delta": [1, 0, 0],
    })
    assert result["persisted_validation"]["valid"]
    assert CadDocument.open(output).entities[0]["bulges"] == [1.0, 0.0]


def test_hatch_pattern_attributes_round_trip(tmp_path):
    source = tmp_path / "hatch.dxf"
    write_dxf(source, entities=[{
        "type": "HATCH", "layer": "SECTION", "pattern": "ANSI31", "solid": False,
        "boundary_path_count": 1, "hatch_style": 1, "pattern_type": 1,
        "pattern_angle": 45, "pattern_scale": 2.5, "double": True,
        "boundary_vertices": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 5}],
    }])
    output = tmp_path / "hatch-copy.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "entity_id": "e00001", "delta": [1, 0, 0],
    })
    assert result["persisted_validation"]["valid"]
    hatch = CadDocument.open(output).entities[0]
    assert hatch["pattern"] == "ANSI31"
    assert hatch["pattern_angle"] == 45
    assert hatch["pattern_scale"] == 2.5
    assert hatch["double"] is True


def test_hatch_create_can_bind_source_entities_and_reopen_association(tmp_path):
    source = tmp_path / "assoc-source.dxf"
    output = tmp_path / "assoc-output.dxf"
    write_dxf(source, lines=[
        {"x1": 0, "y1": 0, "x2": 10, "y2": 0},
        {"x1": 10, "y1": 0, "x2": 10, "y2": 10},
        {"x1": 10, "y1": 10, "x2": 0, "y2": 10},
        {"x1": 0, "y1": 10, "x2": 0, "y2": 0},
    ])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.hatch_create", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "boundary": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}],
        "associative": True, "source_entity_ids": ["e00001", "e00002", "e00003", "e00004"],
    })
    assert result["persisted_validation"]["valid"]
    hatch = CadDocument.open(output).entities[-1]
    assert hatch["associative"] is True
    assert hatch["boundary_paths"][0]["source_handles"] == [
        entity["handle"] for entity in CadDocument.open(source).entities
    ]


def test_generic_dxf_artifact_write_uses_cad_validation_and_sidecar(tmp_path):
    path = tmp_path / "generated.dxf"
    written = EngineeringAgent({"tools": {}}).write_asset(str(path), "dxf", {"lines": [{"x1": 0, "y1": 0, "x2": 5, "y2": 0}]})
    assert Path(written).is_file()
    assert Path(str(path) + ".cad.json").is_file()
    assert CadDocument.open(path).entities[0]["id"] == "e00001"


def test_cad_extended_operations_and_post_commit_validation(tmp_path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "extended.dxf"
    write_dxf(source, lines=[
        {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "layer": "FRAME"},
        {"x1": 10, "y1": 0, "x2": 10, "y2": 10, "layer": "FRAME"},
    ])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "operations": [
            {"operation": "cad.entity_array_rectangular", "entity_ids": ["e00001"], "rows": 2, "columns": 2, "row_spacing": 20, "column_spacing": 20},
            {"operation": "cad.entity_break", "entity_id": "e00002", "point": {"x": 10, "y": 5}},
            {"operation": "cad.leader_create", "vertices": [{"x": 0, "y": 0}, {"x": -5, "y": 5}], "text": "mount"},
            {"operation": "cad.mtext_create", "insert": {"x": 2, "y": 2}, "text": "A\\nB", "height": 2},
            {"operation": "cad.ellipse_create", "center": {"x": 5, "y": 5}, "major_axis": {"x": 4, "y": 0}, "ratio": 0.5},
        ],
    })
    assert result["validation"]["valid"]
    assert result["persisted_validation"]["valid"]
    assert result["persisted_validation"]["integrity"]["entity_ids"]
    persisted = CadDocument.open(output)
    assert {item["type"] for item in persisted.entities} >= {"LEADER", "MTEXT", "ELLIPSE"}


def test_cad_join_and_purge(tmp_path):
    source = tmp_path / "join.dxf"
    output = tmp_path / "joined.dxf"
    write_dxf(source, lines=[
        {"x1": 0, "y1": 0, "x2": 5, "y2": 0, "layer": "USED"},
        {"x1": 5, "y1": 0, "x2": 5, "y2": 5, "layer": "USED"},
    ])
    document = CadDocument.open(source)
    document.layers["UNUSED"] = __import__("engineering_agent.cad_model", fromlist=["Layer"]).Layer("UNUSED")
    sidecar = Path(str(source) + ".cad.json")
    sidecar.write_text(json.dumps(document.sidecar()), encoding="utf-8")
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "operations": [
            {"operation": "cad.entity_join", "entity_ids": ["e00001", "e00002"]},
            {"operation": "cad.entity_purge"},
        ],
    })
    assert result["validation"]["valid"]
    persisted = CadDocument.open(output)
    assert persisted.entities[0]["type"] == "LWPOLYLINE"
    assert "UNUSED" not in persisted.layers


def test_trim_fillet_and_chamfer_use_line_geometry(tmp_path):
    source = tmp_path / "corners.dxf"
    output = tmp_path / "corner-edits.dxf"
    write_dxf(source, lines=[
        {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "layer": "FRAME"},
        {"x1": 10, "y1": 0, "x2": 10, "y2": 10, "layer": "FRAME"},
    ])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "operations": [
            {"operation": "cad.entity_fillet", "entity_ids": ["e00001", "e00002"], "radius": 2},
        ],
    })
    assert result["validation"]["valid"] and result["persisted_validation"]["valid"]
    assert any(item["type"] == "ARC" for item in CadDocument.open(output).entities)

    trim_output = tmp_path / "trimmed.dxf"
    trim = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_trim", {
        "project_dir": str(tmp_path), "input_file": output, "output_file": trim_output,
        "entity_id": "e00001", "boundary_entity_id": "e00002", "keep": "start",
    })
    assert trim["persisted_validation"]["valid"]


def test_trim_supports_line_circle_and_arc_line_intersections(tmp_path):
    source = tmp_path / "intersection.dxf"
    write_dxf(source,
              lines=[{"x1": -5, "y1": 0, "x2": 5, "y2": 0}, {"x1": 0, "y1": -10, "x2": 0, "y2": 10}],
              circles=[{"x": 0, "y": 0, "radius": 2}],
              arcs=[{"x": 0, "y": 0, "radius": 5, "start_angle": 0, "end_angle": 180}])
    line_output = tmp_path / "line-trim.dxf"
    line_result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_trim", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(line_output),
        "entity_id": "e00001", "boundary_entity_id": "e00003", "keep": "start",
    })
    assert line_result["persisted_validation"]["valid"]
    line = CadDocument.open(line_output).entities[0]
    assert line["start"]["x"] == -5 and line["end"]["x"] == -2

    arc_output = tmp_path / "arc-trim.dxf"
    arc_result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_trim", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(arc_output),
        "entity_id": "e00004", "boundary_entity_id": "e00002", "keep": "start",
    })
    assert arc_result["persisted_validation"]["valid"]
    arc = next(item for item in CadDocument.open(arc_output).entities if item["type"] == "ARC")
    assert arc["end_angle"] == 90


def test_trim_supports_finite_polyline_cutting_boundaries(tmp_path):
    source = tmp_path / "polyline-boundary.dxf"
    write_dxf(source, entities=[
        {"type": "LINE", "start": {"x": -5, "y": 0}, "end": {"x": 15, "y": 0}},
        {"type": "LWPOLYLINE", "vertices": [{"x": 4, "y": -2}, {"x": 4, "y": 2}], "closed": False},
    ])
    output = tmp_path / "polyline-trim.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_trim", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "entity_id": "e00001", "boundary_entity_id": "e00002", "keep": "start",
    })
    assert result["persisted_validation"]["valid"]
    line = CadDocument.open(output).entities[0]
    assert line["start"]["x"] == -5 and line["end"]["x"] == 4

    bulged = tmp_path / "bulged.dxf"
    write_dxf(bulged, entities=[
        {"type": "LINE", "start": {"x": -5, "y": 0}, "end": {"x": 15, "y": 0}},
        {"type": "LWPOLYLINE", "vertices": [{"x": 4, "y": -2}, {"x": 4, "y": 2}], "bulges": [1], "closed": False},
    ])
    with pytest.raises(ValueError, match="TRIM_BULGED_BOUNDARY_UNSUPPORTED"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.entity_trim", {
            "project_dir": str(tmp_path), "input_file": bulged, "output_file": tmp_path / "rejected.dxf",
            "entity_id": "e00001", "boundary_entity_id": "e00002", "keep": "start",
        })


def test_trim_supports_rotated_ellipse_cutting_boundaries(tmp_path):
    source = tmp_path / "ellipse-boundary.dxf"
    write_dxf(source, entities=[
        {"type": "LINE", "start": {"x": -5, "y": 0}, "end": {"x": 5, "y": 0}},
        {"type": "ELLIPSE", "center": {"x": 0, "y": 0}, "major_axis": {"x": 0, "y": 4}, "ratio": 0.5},
    ])
    output = tmp_path / "ellipse-trim.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_trim", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "entity_id": "e00001", "boundary_entity_id": "e00002", "keep": "start",
    })
    assert result["persisted_validation"]["valid"]
    line = CadDocument.open(output).entities[0]
    assert line["start"]["x"] == -5 and line["end"]["x"] == pytest.approx(-2)


def test_offset_uses_signed_side_and_preserves_polyline_connectivity(tmp_path):
    source = tmp_path / "offset.dxf"
    write_dxf(source, entities=[
        {"type": "LINE", "start": {"x": 0, "y": 0}, "end": {"x": 10, "y": 0}},
        {"type": "LWPOLYLINE", "vertices": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}], "closed": False},
        {"type": "CIRCLE", "center": {"x": 4, "y": 4}, "radius": 5},
    ])
    output = tmp_path / "offset-edited.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "operations": [
            {"operation": "cad.entity_offset", "entity_id": "e00001", "distance": 2},
            {"operation": "cad.entity_offset", "entity_id": "e00002", "distance": 1},
            {"operation": "cad.entity_offset", "entity_id": "e00003", "distance": -2},
        ],
    })
    assert result["persisted_validation"]["valid"]
    document = CadDocument.open(output)
    line, polyline, circle = document.entities[:3]
    assert line["start"]["y"] == 2 and line["end"]["y"] == 2
    assert polyline["vertices"] == [
        {"x": 0.0, "y": 1.0, "z": 0.0},
        {"x": 9.0, "y": 1.0, "z": 0.0},
        {"x": 9.0, "y": 10.0, "z": 0.0},
    ]
    assert circle["radius"] == 3


def test_offset_rejects_bulged_polylines_and_nonpositive_result_radius(tmp_path):
    source = tmp_path / "offset-invalid.dxf"
    write_dxf(source, entities=[
        {"type": "LWPOLYLINE", "vertices": [{"x": 0, "y": 0}, {"x": 5, "y": 0}], "bulges": [1, 0]},
        {"type": "CIRCLE", "center": {"x": 0, "y": 0}, "radius": 2},
    ])
    output = tmp_path / "offset-invalid-out.dxf"
    before = source.read_bytes()
    with pytest.raises(ValueError, match="OFFSET_BULGED_POLYLINE_UNSUPPORTED"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.entity_offset", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": output,
            "entity_id": "e00001", "distance": 1,
        })
    with pytest.raises(ValueError, match="OFFSET_RADIUS_TOO_LARGE"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.entity_offset", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": output,
            "entity_id": "e00002", "distance": -2,
        })
    assert source.read_bytes() == before and not output.exists()


def test_cad_explode_table_coordinate_system_and_reverse(tmp_path):
    source = tmp_path / "block.dxf"
    output = tmp_path / "expanded.dxf"
    write_dxf(source, lines=[{"x1": 0, "y1": 0, "x2": 4, "y2": 0}], blocks=[{"name": "B", "base_point": {"x": 0, "y": 0}, "entities": [{"type": "LINE", "start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 0}}]}], inserts=[{"block_name": "B", "x": 10, "y": 10}])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "operations": [
            {"operation": "cad.entity_explode", "entity_id": "e00002"},
            {"operation": "cad.coordinate_system_create", "name": "DETAIL", "origin": {"x": 1, "y": 2}},
            {"operation": "cad.table_create", "origin": {"x": 0, "y": 30}, "rows": [["A", "B"], ["1", "2"]]},
            {"operation": "cad.entity_reverse", "all_entities": True},
        ],
    })
    assert result["persisted_validation"]["valid"]
    persisted = CadDocument.open(output)
    assert any(item["type"] == "MTEXT" for item in persisted.entities)
    assert any(item.get("name") == "DETAIL" for item in persisted.coordinate_systems)


def test_dimension_variants_round_trip(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "dimensions.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "operations": [
            {"operation": "cad.dimension_linear_create", "start": {"x": 0, "y": 0}, "end": {"x": 3, "y": 4}},
            {"operation": "cad.dimension_radial_create", "center": {"x": 5, "y": 5}, "point": {"x": 7, "y": 5}},
            {"operation": "cad.dimension_angular_create", "center": {"x": 0, "y": 0}, "start": {"x": 1, "y": 0}, "end": {"x": 0, "y": 1}},
        ],
    })
    assert result["persisted_validation"]["valid"]
    kinds = [item.get("dimension_type") for item in CadDocument.open(output).entities if item["type"] == "DIMENSION"]
    assert kinds[-3:] == ["linear", "radial", "angular"]


def test_spline_create_round_trip_and_transform(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "spline.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.spline_create", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "fit_points": [{"x": 0, "y": 0}, {"x": 2, "y": 4}, {"x": 6, "y": 3}, {"x": 10, "y": 1}],
        "degree": 3, "layer": "CURVES",
    })
    assert result["persisted_validation"]["valid"]
    spline = next(item for item in CadDocument.open(output).entities if item["type"] == "SPLINE")
    assert spline["degree"] == 3 and len(spline["fit_points"]) == 4
    moved = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": output, "output_file": output,
        "entity_ids": [spline["id"]], "delta": [5, 0, 0],
    })
    assert moved["persisted_validation"]["valid"]
    translated = next(item for item in CadDocument.open(output).entities if item["id"] == spline["id"])
    assert translated["fit_points"][0]["x"] == pytest.approx(5)


def test_spline_reverse_reverses_fit_points_and_native_round_trip(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "spline-reverse.dxf"
    EngineeringAgent({"tools": {}}).execute_operation("cad.spline_create", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "fit_points": [{"x": 0, "y": 0}, {"x": 2, "y": 4}, {"x": 6, "y": 3}, {"x": 10, "y": 1}],
        "degree": 3,
    })
    spline_id = next(item["id"] for item in CadDocument.open(output).entities if item["type"] == "SPLINE")
    reversed_result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_reverse", {
        "project_dir": str(tmp_path), "input_file": output, "output_file": output, "entity_ids": [spline_id],
    })
    assert reversed_result["persisted_validation"]["valid"]
    spline = next(item for item in CadDocument.open(output).entities if item["id"] == spline_id)
    assert [point["x"] for point in spline["fit_points"]] == [10, 6, 2, 0]


def test_cad_audit_and_document_diff(tmp_path):
    source = _source(tmp_path)
    edited = tmp_path / "edited.dxf"
    EngineeringAgent({"tools": {}}).execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": edited,
        "entity_id": "e00001", "delta": [1, 0, 0],
    })
    agent = EngineeringAgent({"tools": {}})
    audit = agent.execute_operation("cad.audit", {"input_file": edited, "project_dir": str(tmp_path)})
    assert audit["validation"]["valid"] and audit["sidecar_present"]
    required = agent.execute_operation("cad.audit", {"input_file": edited, "project_dir": str(tmp_path), "requirements": {"required_layers": ["FRAME"], "required_entity_types": ["LINE"], "required_text": [], "min_entity_count": 1, "bbox_contains": {"x": {"min": 1, "max": 11}}}})
    assert required["validation"]["valid"]
    diff = agent.execute_operation("cad.document_diff", {"input_file": source, "right_file": edited, "project_dir": str(tmp_path)})
    assert diff["diff"]["change_count"] > 0


def test_sheet_viewport_title_block_and_ordinate_dimension_round_trip(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "layout.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "operations": [
            {"operation": "cad.sheet_create", "name": "A3", "width": 420, "height": 297},
            {"operation": "cad.viewport_create", "name": "MAIN", "sheet": "A3", "center": {"x": 5, "y": 5}, "width": 300, "height": 180, "scale": 2},
            {"operation": "cad.title_block_create", "sheet": "A3", "fields": {"TITLE": "Bracket", "REV": "A"}, "origin": {"x": 220, "y": 15}, "width": 180, "height": 35},
            {"operation": "cad.dimension_ordinate_create", "point": {"x": 12, "y": 8}, "axis": "x"},
        ],
    })
    assert result["persisted_validation"]["valid"]
    persisted = CadDocument.open(output)
    assert persisted.sheets["A3"]["title_block"]["fields"]["TITLE"] == "Bracket"
    assert persisted.viewports["MAIN"]["scale"] == 2
    assert any(item.get("dimension_type") == "ordinate" for item in persisted.entities)
    assert any(item.get("title_block_id") == "tb-A3" for item in persisted.entities)
    viewer = asset_view(tmp_path, output.name)["viewer"]
    assert "图纸：A3" in viewer
    # Paper-space viewports are rendered in the physical plot preview, not in
    # the model editor's coordinate system.
    assert "MAIN" not in viewer


def test_industry_annotations_round_trip(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "industry.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "operations": [
            {"operation": "cad.standard_symbol_create", "center": {"x": 1, "y": 1}, "symbol": "WELD"},
            {"operation": "cad.fastener_create", "center": {"x": 10, "y": 1}, "size": "M8"},
            {"operation": "cad.gdt_frame_create", "insert": {"x": 1, "y": 10}, "frames": ["⌀0.1", "A"]},
            {"operation": "cad.surface_texture_create", "insert": {"x": 1, "y": 15}, "texture": "Ra 3.2"},
            {"operation": "cad.pid_symbol_create", "center": {"x": 20, "y": 10}, "symbol": "VALVE", "label": "V-01"},
            {"operation": "cad.process_line_create", "start": {"x": 20, "y": 10}, "end": {"x": 30, "y": 10}, "service": "hydraulic"},
            {"operation": "cad.bom_create", "origin": {"x": 0, "y": -10}, "rows": [["1", "Bracket"], ["2", "Bolt"]]},
            {"operation": "cad.bom_balloon_create", "center": {"x": 30, "y": 20}, "label": "1"},
        ],
    })
    assert result["persisted_validation"]["valid"]
    persisted = CadDocument.open(output)
    assert any("fastener" in item for item in persisted.entities)
    assert any("gdt" in item for item in persisted.entities)
    assert any("surface_texture" in item for item in persisted.entities)
    assert any("pid_symbol" in item for item in persisted.entities)
    assert any("bom" in item for item in persisted.entities)


def test_standard_catalog_is_queryable_and_strict_annotations_are_validated(tmp_path):
    agent = EngineeringAgent({"tools": {}})
    catalog = agent.execute_operation("cad.standard_catalog", {"standard": "ISO"})
    assert catalog["schema_version"] == "simloom-ai/standards/v1"
    assert "WELD" in catalog["standards"]["ISO"]["symbols"]
    library = agent.execute_operation("cad.standard_library", {"category": "fasteners", "query": "M8"})
    assert library["categories"]["fasteners"] and library["categories"]["fasteners"][0]["sizes"]
    source = _source(tmp_path)
    output = tmp_path / "strict-standard.dxf"
    result = agent.execute_operation("cad.standard_symbol_create", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "center": {"x": 1, "y": 1}, "symbol": "WELD", "standard": "ISO", "strict_standard": True,
    })
    assert result["persisted_validation"]["valid"]
    parts = agent.execute_operation("cad.standard_parts_audit", {"project_dir": str(tmp_path), "input_file": output})
    assert parts["parts"]["counts"]["standard_symbol"] == 1
    with pytest.raises(ValueError, match="STANDARD_SYMBOL_NOT_FOUND"):
        agent.execute_operation("cad.standard_symbol_create", {
            "project_dir": str(tmp_path), "input_file": output, "output_file": output,
            "center": {"x": 1, "y": 1}, "symbol": "NOT_A_STANDARD_SYMBOL", "standard": "ISO", "strict_standard": True,
        })

