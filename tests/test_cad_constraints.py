"""AgentCAD-style geometric constraint checks and bounded enforcement."""
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "constraints.dxf"
    write_dxf(path, entities=[
        {"type": "LINE", "layer": "PROFILE", "start": {"x": 0, "y": 0}, "end": {"x": 3, "y": 4}},
        {"type": "LINE", "layer": "PROFILE", "start": {"x": 10, "y": 10}, "end": {"x": 12, "y": 10}},
        {"type": "CIRCLE", "layer": "HOLES", "center": {"x": 5, "y": 5}, "radius": 2},
    ])
    return path


def test_constraint_check_reports_actual_geometry_and_point_refs(tmp_path):
    source = _source(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("cad.constraints_check", {
        "project_dir": str(tmp_path), "input_file": str(source),
        "constraints": [
            {"id": "horizontal", "type": "horizontal", "a": "e00001"},
            {"id": "length", "type": "length", "a": "e00001", "value": 5},
            {"id": "point", "type": "coincident", "a": "e00001@end", "b": "e00002@start"},
        ],
    })
    assert result["valid"] is False
    assert result["checks"][0]["satisfied"] is False
    assert result["checks"][1]["actual"] == pytest.approx(5)
    assert result["checks"][2]["actual"] == pytest.approx((7**2 + 6**2) ** 0.5)


def test_constraint_enforce_rechecks_and_persists_changes(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "enforced.dxf"
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("cad.constraints_enforce", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
        "constraints": [
            {"id": "horizontal", "type": "horizontal", "a": "e00001"},
            {"id": "radius", "type": "radius", "a": "e00003", "value": 4},
        ],
    })
    assert result["validation"]["valid"] is True
    assert all(item["satisfied"] for item in result["checks"])
    document = CadDocument.open(output)
    line = document.entity("e00001")
    circle = document.entity("e00003")
    assert line["start"]["y"] == pytest.approx(line["end"]["y"])
    assert circle["radius"] == pytest.approx(4)


def test_constraint_enforce_rolls_back_when_constraints_conflict(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "should-not-commit.dxf"
    before = source.read_bytes()
    with pytest.raises(ValueError, match="CONSTRAINT_CONFLICT"):
        EngineeringAgent({"tools": {}}).execute_operation("cad.constraints_enforce", {
            "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
            "constraints": [
                {"type": "horizontal", "a": "e00001"},
                {"type": "vertical", "a": "e00001"},
            ],
        })
    assert not output.exists()
    assert source.read_bytes() == before
