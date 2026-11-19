"""Analytic CAD measurements are read-only and retain per-entity evidence."""
from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.formats import write_dxf


def _drawing(tmp_path: Path) -> Path:
    path = tmp_path / "measurements.dxf"
    write_dxf(path, entities=[
        {"type": "LINE", "layer": "PROFILE", "start": {"x": 0, "y": 0}, "end": {"x": 3, "y": 4}},
        {"type": "CIRCLE", "layer": "HOLES", "center": {"x": 5, "y": 5}, "radius": 2},
        {"type": "ARC", "layer": "PROFILE", "center": {"x": 0, "y": 0}, "radius": 3,
         "start_angle": 0, "end_angle": 90},
        {"type": "LWPOLYLINE", "layer": "PROFILE",
         "vertices": [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}],
         "closed": True},
        {"type": "POINT", "layer": "REFERENCE", "point": {"x": 1, "y": 1}},
    ])
    return path


def test_measure_entities_returns_exact_planar_values_and_unsupported_evidence(tmp_path):
    path = _drawing(tmp_path)
    before = path.read_bytes()
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.measure_entities", {
        "project_dir": str(tmp_path), "input_file": str(path),
    })

    assert result["schema_version"] == "simloom-ai/cad-measurements/v1"
    assert result["summary"]["selected_count"] == 5
    assert result["summary"]["measured_count"] == 4
    assert result["summary"]["unsupported_count"] == 1
    line = next(item for item in result["measurements"] if item["type"] == "LINE")
    circle = next(item for item in result["measurements"] if item["type"] == "CIRCLE")
    arc = next(item for item in result["measurements"] if item["type"] == "ARC")
    polyline = next(item for item in result["measurements"] if item["type"] == "LWPOLYLINE")
    assert line["length"] == pytest.approx(5)
    assert circle["area"] == pytest.approx(4 * 3.141592653589793)
    assert circle["perimeter"] == pytest.approx(4 * 3.141592653589793)
    assert arc["sweep_degrees"] == pytest.approx(90)
    assert arc["perimeter"] == pytest.approx(1.5 * 3.141592653589793)
    assert polyline["area"] == pytest.approx(12)
    assert polyline["perimeter"] == pytest.approx(14)
    assert "UNSUPPORTED_MEASUREMENT:e00005" in result["warnings"]
    assert path.read_bytes() == before


def test_measure_entities_supports_agentcad_style_selection_and_strict_mode(tmp_path):
    path = _drawing(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    selected = agent.execute_operation("cad.measure_entities", {
        "project_dir": str(tmp_path), "input_file": str(path), "target": ["e00002"],
    })
    assert selected["summary"]["selected_count"] == 1
    assert selected["measurements"][0]["type"] == "CIRCLE"

    filtered = agent.execute_operation("cad.measure_entities", {
        "project_dir": str(tmp_path), "input_file": str(path), "layer": "PROFILE",
    })
    assert {item["type"] for item in filtered["measurements"]} == {"LINE", "ARC", "LWPOLYLINE"}

    with pytest.raises(ValueError, match="UNSUPPORTED_MEASUREMENT:e00005"):
        agent.execute_operation("cad.measure_entities", {
            "project_dir": str(tmp_path), "input_file": str(path), "strict": True,
        })
