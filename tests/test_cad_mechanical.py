from __future__ import annotations

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf
from engineering_agent.operations import OperationRegistry


def _spec():
    return {
        "origin": {"x": 40, "y": 160},
        "view_gap": 24,
        "sheet": {"name": "A3", "paper": "A3", "orientation": "landscape"},
        "parts": [
            {"id": "SHAFT-01", "kind": "shaft", "length": 80, "outer_diameter": 40,
             "bore_diameter": 20, "material": "42CrMo", "quantity": 1,
             "holes": [{"count": 4, "diameter": 8, "pcd": 60, "orientation_deg": 45}]},
            {"id": "HUB-01", "kind": "hub", "length": 30,
             "segments": [{"length": 10, "outer_diameter": 50}, {"length": 20, "outer_diameter": 36}],
             "bore_diameter": 20, "material": "Al7075", "quantity": 2},
        ],
    }


def test_draw_mechanical_assembly_creates_views_holes_dimensions_bom_and_readback(tmp_path):
    source = tmp_path / "empty.dxf"
    output = tmp_path / "assembly.dxf"
    write_dxf(source, entities=[])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.draw_mechanical_assembly", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output, "spec": _spec(),
    })
    assembly = result["results"][0]
    assert assembly["schema_version"] == "simloom-ai/mechanical-assembly/v1"
    assert assembly["layout"] == "A3"
    assert len(assembly["parts"]) == 2
    assert len(assembly["holes"]) == 4
    assert len(assembly["dimensions"]) == 6
    assert assembly["bom_rows"][0] == ["ITEM", "PART", "TYPE", "QTY", "MATERIAL"]
    assert result["persisted_validation"]["mechanical_assembly"]["valid"] is True
    assert output.is_file()
    persisted = CadDocument.open(output)
    assert "A3" in persisted.sheets
    assert persisted.metadata["mechanical_assembly"]["schema_version"] == "simloom-ai/mechanical-assembly/v1"
    assert sum(1 for entity in persisted.entities if entity.get("mechanical_view") == "front") == 2
    assert sum(1 for entity in persisted.entities if entity.get("mechanical_view") == "end") >= 6
    assert sum(1 for entity in persisted.entities if entity.get("bom")) >= 15


def test_draw_mechanical_assembly_rejects_inconsistent_segments_and_invalid_holes(tmp_path):
    source = tmp_path / "empty.dxf"
    write_dxf(source, entities=[])
    agent = EngineeringAgent({"tools": {}})
    bad_segments = _spec()
    bad_segments["parts"][1]["segments"][0]["length"] = 9
    with pytest.raises(ValueError, match="MECH_SEGMENT_LENGTH_MISMATCH"):
        agent.execute_operation("cad.draw_mechanical_assembly", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": tmp_path / "bad-segments.dxf", "spec": bad_segments,
        })
    bad_hole = _spec()
    bad_hole["parts"][0]["holes"][0]["pcd"] = 8
    with pytest.raises(ValueError, match="MECH_HOLE_PCD_INVALID"):
        agent.execute_operation("cad.draw_mechanical_assembly", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": tmp_path / "bad-hole.dxf", "spec": bad_hole,
        })


def test_draw_mechanical_assembly_is_discoverable_with_workflow_outputs():
    spec = OperationRegistry().get("cad.draw_mechanical_assembly")
    assert "mechanical_assembly" in spec.output_kinds
    assert spec.parameters_schema["spec"]["required"] == ["parts"]

