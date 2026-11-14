from __future__ import annotations

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf
from engineering_agent.operations import OperationRegistry


def _spec():
    return {
        "equipment": [
            {"id": "P1", "symbol": "centrifugal_pump", "x": 20, "y": 50, "label": "P-101"},
            {"id": "P2", "symbol": "centrifugal_pump", "x": 120, "y": 80, "label": "P-102"},
        ],
        "valves": [{"id": "V1", "symbol": "gate_valve", "x": 70, "y": 65, "label": "XV-101"}],
        "lines": [
            {"from": "P1.discharge", "to": "V1.inlet", "number": "DN50-FLOW", "service": "water"},
            {"from": "V1.outlet", "to": "P2.suction", "number": "DN50-FLOW-2", "service": "water"},
        ],
    }


def test_draw_pid_creates_port_connected_graph_and_readback_audit(tmp_path):
    source = tmp_path / "empty.dxf"
    output = tmp_path / "pid.dxf"
    write_dxf(source, entities=[])
    result = EngineeringAgent({"tools": {}}).execute_operation("cad.draw_pid", {
        "project_dir": str(tmp_path), "input_file": source, "output_file": output, "spec": _spec(),
    })
    drawing = result["results"][0]
    assert drawing["schema_version"] == "simloom-ai/pid/v1"
    assert len(drawing["symbols"]) == 3
    assert len(drawing["lines"]) == 2
    assert drawing["graph"]["valid"] is True
    assert result["persisted_validation"]["pid"]["valid"] is True
    persisted = CadDocument.open(output)
    assert persisted.metadata["pid"]["schema_version"] == "simloom-ai/pid/v1"
    audit = EngineeringAgent({"tools": {}}).execute_operation("cad.pid_audit", {
        "project_dir": str(tmp_path), "input_file": output,
    })
    assert audit["pid"]["valid"] is True
    assert {line["process_line"]["number"] for line in persisted.entities if line.get("process_line")} == {"DN50-FLOW", "DN50-FLOW-2"}


def test_draw_pid_rejects_unknown_ports_and_duplicate_line_numbers(tmp_path):
    source = tmp_path / "empty.dxf"
    write_dxf(source, entities=[])
    agent = EngineeringAgent({"tools": {}})
    missing = _spec()
    missing["lines"][0]["to"] = "V1.missing"
    with pytest.raises(ValueError, match="PID_PORT_NOT_FOUND"):
        agent.execute_operation("cad.draw_pid", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": tmp_path / "missing.dxf", "spec": missing,
        })
    duplicate = _spec()
    duplicate["lines"][1]["number"] = "DN50-FLOW"
    with pytest.raises(ValueError, match="PID_LINE_NUMBER_DUPLICATE"):
        agent.execute_operation("cad.draw_pid", {
            "project_dir": str(tmp_path), "input_file": source, "output_file": tmp_path / "duplicate.dxf", "spec": duplicate,
        })


def test_draw_pid_is_discoverable_with_graph_output():
    spec = OperationRegistry().get("cad.draw_pid")
    assert "pid" in spec.output_kinds
    assert spec.parameters_schema["spec"]["properties"]["lines"]["minItems"] == 1

