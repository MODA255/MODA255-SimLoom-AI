from __future__ import annotations

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf


def _drawing(tmp_path):
    path = tmp_path / "selectors.dxf"
    write_dxf(path, entities=[
        {"type": "LINE", "layer": "FRAME", "start": {"x": 0, "y": 0}, "end": {"x": 10, "y": 0}, "tag": "frame.main"},
        {"type": "CIRCLE", "layer": "HOLES", "center": {"x": 5, "y": 5}, "radius": 1, "tag": "hole.1"},
        {"type": "CIRCLE", "layer": "HOLES", "center": {"x": 15, "y": 5}, "radius": 2, "tag": "hole.2"},
    ])
    return path


def test_agentcad_filter_and_tag_selectors_move_multiple_entities(tmp_path):
    source = _drawing(tmp_path)
    output = tmp_path / "filtered.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "target": {"type": "CIRCLE", "layer": "HOLES", "tag_prefix": "hole."},
        "delta": [0, 3, 0],
    })
    assert result["persisted_validation"]["valid"]
    document = CadDocument.open(output)
    assert [entity["center"]["y"] for entity in document.entities if entity["type"] == "CIRCLE"] == [8.0, 8.0]

    tag_output = tmp_path / "tagged.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.entity_move", {
        "project_dir": str(tmp_path), "input_file": output, "output_file": tag_output,
        "target": "tag:hole.1", "delta": [2, 0, 0],
    })
    assert result["persisted_validation"]["valid"]
    moved = {entity["tag"]: entity for entity in CadDocument.open(tag_output).entities if entity["type"] == "CIRCLE"}
    assert moved["hole.1"]["center"]["x"] == 7.0
    assert moved["hole.2"]["center"]["x"] == 15.0


def test_batch_selector_references_entities_created_by_previous_operation(tmp_path):
    source = _drawing(tmp_path)
    output = tmp_path / "batch.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "operations": [
            {"operation": "cad.line_create", "start": {"x": 1, "y": 1}, "end": {"x": 4, "y": 1}, "tag": "batch.line"},
            {"operation": "cad.entity_move", "target": "$0", "delta": [0, 5, 0]},
        ],
    })
    assert result["persisted_validation"]["valid"]
    line = next(entity for entity in CadDocument.open(output).entities if entity.get("tag") == "batch.line")
    assert line["start"]["y"] == 6.0 and line["end"]["y"] == 6.0
    assert result["results"][1]["step_index"] == 1


def test_tag_is_persistent_and_idempotent_for_create_operations(tmp_path):
    source = _drawing(tmp_path)
    output = tmp_path / "tagged-create.dxf"
    agent = EngineeringAgent({"tools": {}})
    first = agent.execute_operation("cad.line_create", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "start": {"x": 20, "y": 0}, "end": {"x": 30, "y": 0}, "tag": "idempotent", "color": 3,
    })
    assert first["persisted_validation"]["valid"]
    persisted = CadDocument.open(output)
    created = next(entity for entity in persisted.entities if entity.get("tag") == "idempotent")
    assert created["color_index"] == 3

    skipped = agent.execute_operation("cad.line_create", {
        "project_dir": str(tmp_path), "input_file": output, "output_file": output,
        "start": {"x": 100, "y": 0}, "end": {"x": 110, "y": 0}, "tag": "idempotent", "if_tag_exists": "skip",
    })
    assert skipped["results"][0]["skipped"] is True
    assert len([entity for entity in CadDocument.open(output).entities if entity.get("tag") == "idempotent"]) == 1


def test_common_drawing_attributes_apply_to_specialized_creators(tmp_path):
    source = _drawing(tmp_path)
    output = tmp_path / "specialized.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "operations": [
            {"operation": "cad.sheet_create", "name": "A3", "width": 420, "height": 297},
            {"operation": "cad.mtext_create", "insert": {"x": 10, "y": 10}, "text": "note",
             "layout": "A3", "layer": "NOTES", "color": 5, "linetype": "DASHED", "lineweight": 25,
             "tag": "note.main"},
        ],
    })
    assert result["persisted_validation"]["valid"]
    note = next(entity for entity in CadDocument.open(output).entities if entity.get("tag") == "note.main")
    assert note["sheet"] == "A3" and note["layout"] == "A3"
    assert note["layer"] == "NOTES" and note["color_index"] == 5
    assert note["linetype"] == "DASHED" and note["lineweight"] == 25


def test_selector_schema_is_published_for_edit_operations():
    from engineering_agent.operations import OperationRegistry

    schema = OperationRegistry().get("cad.entity_move").schema()["parameters_schema"]
    assert "target" in schema and "selector" in schema and "all" in schema
    assert "tag_prefix" in schema["target"]["description"]
