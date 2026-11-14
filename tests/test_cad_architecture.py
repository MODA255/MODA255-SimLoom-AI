from __future__ import annotations

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.formats import write_dxf


def _spec(area=None):
    room = {"id": "r1", "number": "1", "name": "Living", "at": [5000, 2500]}
    if area is not None:
        room["area"] = area
    return {
        "walls": [{"id": "ext", "axis": [[0, 0], [10000, 0], [10000, 5000], [0, 5000]], "thickness": 200}],
        "openings": [{"id": "M1", "kind": "door", "wall": "ext", "offset": 1000, "width": 900}],
        "rooms": [room],
    }


def test_arch_plan_creates_measured_room_opening_and_dimensions(tmp_path):
    source = tmp_path / "empty.dxf"
    output = tmp_path / "plan.dxf"
    write_dxf(source, entities=[])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.draw_arch_plan", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output, "spec": _spec(),
    })
    plan = result["results"][0]
    assert result["persisted_validation"]["valid"] is True
    assert plan["rooms"][0]["area_m2"] == pytest.approx(47.04)
    assert plan["rooms"][0]["area_verified"] is True
    assert plan["openings"][0]["entity_ids"]
    assert output.is_file()
    document = result["document"]
    assert any(entity.get("arch_wall_boundary", {}).get("role") == "inner" for entity in document["entities"])
    assert any(entity.get("arch_dimension", {}).get("axis") == "x" for entity in document["entities"])
    assert any(entity.get("arch_room", {}).get("id") == "r1" for entity in document["entities"])


def test_arch_plan_rejects_a_room_area_that_disagrees_with_walls(tmp_path):
    source = tmp_path / "empty.dxf"
    write_dxf(source, entities=[])
    with pytest.raises(ValueError, match="ARCH_ROOM_AREA_MISMATCH"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.draw_arch_plan", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": tmp_path / "bad.dxf",
            "spec": _spec(area=12.0),
        })


def test_arch_plan_is_discoverable_with_architecture_output_kind():
    from engineering_agent.operations import OperationRegistry
    spec = OperationRegistry().get("cad.draw_arch_plan")
    assert "architecture_plan" in spec.output_kinds
    assert spec.parameters_schema["spec"]["properties"]["walls"]["minItems"] == 1
