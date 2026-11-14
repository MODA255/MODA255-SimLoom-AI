from __future__ import annotations

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_server import MCPServer


def _dimension_file(tmp_path):
    source = tmp_path / "dimensions-source.dxf"
    output = tmp_path / "dimensions.dxf"
    write_dxf(source, entities=[])
    EngineeringAgent({"tools": {}}).execute_operation("cad.dimension_linear_create", {
        "project_dir": str(tmp_path),
        "input_file": source,
        "output_file": output,
        "start": {"x": 0, "y": 0},
        "end": {"x": 30, "y": 40},
        "text": "50.00",
    })
    return output


def test_verify_dimensions_uses_physical_measurement_and_display_text(tmp_path):
    path = _dimension_file(tmp_path)
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.verify_dimensions", {
        "project_dir": str(tmp_path),
        "input_file": path,
        "checks": [{"entity_id": "e00001", "value": 50, "tolerance": 0.001, "check_display": True}],
    })
    assert result["valid"] is True
    assert result["checks"][0]["actual"] == pytest.approx(50)
    assert result["checks"][0]["display"] == pytest.approx(50)
    assert result["checks"][0]["ref"]["document_id"]


def test_verify_dimensions_reports_tolerance_and_display_failures(tmp_path):
    path = _dimension_file(tmp_path)
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.verify_dimensions", {
        "project_dir": str(tmp_path),
        "input_file": path,
        "checks": [{"entity_id": "e00001", "value": 49, "tolerance": {"lower": -0.1, "upper": 0.1}, "check_display": True}],
    })
    assert result["valid"] is False
    assert {error["code"] for error in result["errors"]} == {"DIMENSION_DISPLAY_MISMATCH", "DIMENSION_VALUE_MISMATCH"}
    assert result["checks"][0]["lower"] == pytest.approx(48.9)
    assert result["checks"][0]["upper"] == pytest.approx(49.1)


def test_verify_dimensions_accepts_entity_ref_and_rejects_missing_or_non_dimension(tmp_path):
    path = _dimension_file(tmp_path)
    agent = EngineeringAgent({"tools": {}})
    query = agent.execute_operation("cad.query", {"project_dir": str(tmp_path), "input_file": path, "entity_type": "DIMENSION"})
    ref = query["entities"][0]["ref"]
    passed = agent.execute_operation("cad.verify_dimensions", {
        "project_dir": str(tmp_path), "input_file": path,
        "checks": [{"entity_id": ref, "value": 50}],
    })
    assert passed["valid"] is True
    failed = agent.execute_operation("cad.verify_dimensions", {
        "project_dir": str(tmp_path), "input_file": path,
        "checks": [{"entity_id": "missing", "value": 50}, {"entity_id": "e00001", "value": 49}],
    })
    assert failed["valid"] is False
    assert failed["errors"][0]["code"] == "DIMENSION_NOT_FOUND"


def test_verify_dimensions_is_read_only_and_discoverable(tmp_path):
    path = _dimension_file(tmp_path)
    before = path.read_bytes()
    agent = EngineeringAgent({"tools": {}})
    agent.execute_operation("cad.verify_dimensions", {
        "project_dir": str(tmp_path), "input_file": path,
        "checks": [{"entity_id": "e00001", "value": 50}],
    })
    assert path.read_bytes() == before
    listed = MCPServer(agent, profile="safe").handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"limit": 500},
    })
    operation = next(item for item in listed["result"]["tools"] if item["name"] == "operation.cad.verify_dimensions")
    assert operation["inputSchema"]["required"] == ["input_file", "checks"]
    from engineering_agent.operations import OperationRegistry
    assert "dimension_verification" in OperationRegistry().get("cad.verify_dimensions").output_kinds

