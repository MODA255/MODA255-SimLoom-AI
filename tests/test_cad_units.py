from pathlib import Path

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.cad_units import normalize_cad_arguments, parse_quantity
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_server import MCPServer
from engineering_agent.operations import OperationRegistry


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.dxf"
    write_dxf(path, entities=[])
    return path


def test_parse_quantity_normalizes_lengths_angles_and_dimensionless_values():
    assert parse_quantity("1in", dimension="length") == pytest.approx(25.4)
    assert parse_quantity({"value": 3.141592653589793, "unit": "rad"}, dimension="angle") == pytest.approx(180)
    assert parse_quantity("25%", dimension="dimensionless") == pytest.approx(.25)
    assert parse_quantity(2.5) == pytest.approx(2.5)


def test_normalize_cad_arguments_preserves_text_and_normalizes_vectors():
    value = normalize_cad_arguments({
        "text": "25mm is a label",
        "center": {"x": "1in", "y": "2.5mm"},
        "rotation": "90deg",
        "scale": 2,
        "rows": 2,
    })
    assert value["text"] == "25mm is a label"
    assert value["center"]["x"] == pytest.approx(25.4)
    assert value["center"]["y"] == pytest.approx(2.5)
    assert value["rotation"] == pytest.approx(90)
    assert value["scale"] == pytest.approx(2)
    assert value["rows"] == 2


def test_cad_operation_accepts_agentcad_style_unit_strings(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "rectangle.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.rectangle_create", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "origin": {"x": "1in", "y": "0mm"}, "width": "1in", "height": "25.4mm",
        "rotation": "90deg",
    })
    assert result["persisted_validation"]["valid"]
    entity = CadDocument.open(output).entities[0]
    assert entity["vertices"][0]["x"] == pytest.approx(25.4)
    assert entity["vertices"][1]["x"] == pytest.approx(25.4)
    assert entity["vertices"][1]["y"] == pytest.approx(25.4)


def test_agentcad_alias_and_mcp_discovery_keep_quantity_behavior(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "circle.dxf"
    result = EngineeringAgent({"tools": {}}).execute_operation("add_circle", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output,
        "point": {"x": "1in", "y": "0mm"}, "radius": "5mm",
    })
    assert result["persisted_validation"]["valid"]
    circle = CadDocument.open(output).entities[0]
    assert circle["center"]["x"] == pytest.approx(25.4)
    assert circle["radius"] == pytest.approx(5)
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"limit": 500}})
    alias = next(item for item in listed["result"]["tools"] if item["name"] == "operation.add_circle")
    assert alias["inputSchema"]["properties"]["radius"]["type"] == ["number", "string"]
    assert alias["inputSchema"]["properties"]["radius"]["x-quantity-dimension"] == "length"
    assert alias["inputSchema"]["x-compatibility"]["alias_for"] == "cad.circle_create"


def test_cad_operation_rejects_unknown_and_incompatible_units(tmp_path):
    source = _source(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    with pytest.raises(ValueError, match="CAD_UNIT_UNKNOWN"):
        agent.execute_operation("cad.circle_create", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": tmp_path / "bad.dxf",
            "center": {"x": 0, "y": 0}, "radius": "2furlong",
        })
    with pytest.raises(ValueError, match="CAD_UNIT_DIMENSION_MISMATCH"):
        agent.execute_operation("cad.circle_create", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": tmp_path / "bad-angle.dxf",
            "center": {"x": 0, "y": 0}, "radius": "30deg",
        })


def test_mcp_quantity_failures_have_repairable_domain_codes(tmp_path):
    source = _source(tmp_path)
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "operation.cad.circle_create", "arguments": {
            "project_dir": str(tmp_path), "input_file": str(source),
            "output_file": str(tmp_path / "bad.dxf"), "center": {"x": 0, "y": 0},
            "radius": "2furlong",
        }}})
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["error"]["code"] == "CAD_UNIT_UNKNOWN"


def test_operation_discovery_publishes_quantity_contract():
    schema = next(item for item in OperationRegistry().list() if item["name"] == "cad.circle_create")
    radius = schema["parameters_schema"]["radius"]
    assert radius["type"] == ["number", "string"]
    assert radius["x-quantity-dimension"] == "length"
    assert "<value><unit>" in radius["x-quantity-format"]

