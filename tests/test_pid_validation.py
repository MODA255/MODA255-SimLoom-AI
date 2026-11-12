from __future__ import annotations

import pytest

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf


def test_pid_audit_builds_connectivity_graph_and_detects_orphans(tmp_path):
    source = tmp_path / "pid.dxf"
    write_dxf(source)
    agent = EngineeringAgent({"tools": {}})
    edited = agent.execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
        "operations": [
            {"operation": "cad.pid_symbol_create", "center": {"x": 0, "y": 0}, "symbol": "PUMP", "label": "P-01"},
            {"operation": "cad.pid_symbol_create", "center": {"x": 10, "y": 0}, "symbol": "VALVE", "label": "V-01"},
            {"operation": "cad.process_line_create", "start": {"x": 0, "y": 0}, "end": {"x": 10, "y": 0}, "service": "hydraulic"},
        ],
    })
    assert edited["persisted_validation"]["valid"]
    result = agent.execute_operation("cad.pid_audit", {
        "project_dir": str(tmp_path), "input_file": str(source),
        "requirements": {"required_symbols": ["PUMP", "VALVE"]},
    })["pid"]
    assert result["valid"]
    assert len(result["edges"]) == 1
    assert {node["symbol"] for node in result["nodes"] if node.get("symbol")} == {"PUMP", "VALVE"}


def test_pid_audit_reports_orphan_symbol(tmp_path):
    source = tmp_path / "pid.dxf"
    write_dxf(source)
    agent = EngineeringAgent({"tools": {}})
    agent.execute_operation("cad.pid_symbol_create", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
        "center": {"x": 100, "y": 100}, "symbol": "TANK", "label": "T-01",
    })
    result = agent.execute_operation("cad.pid_audit", {"project_dir": str(tmp_path), "input_file": str(source)})["pid"]
    assert not result["valid"]
    assert result["errors"][0]["code"] == "PID_ORPHAN_SYMBOL"


def test_process_route_is_orthogonal_and_survives_native_round_trip(tmp_path):
    source = tmp_path / "route.dxf"
    write_dxf(source)
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("cad.batch_apply", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
        "operations": [
            {"operation": "cad.pid_symbol_create", "center": {"x": 0, "y": 0}, "symbol": "PUMP", "label": "P-01"},
            {"operation": "cad.pid_symbol_create", "center": {"x": 20, "y": 10}, "symbol": "VALVE", "label": "V-01"},
            {"operation": "cad.process_route_create", "start": {"x": 0, "y": 0}, "end": {"x": 20, "y": 10}, "service": "hydraulic", "corner": "x_then_y"},
        ],
    })
    assert result["persisted_validation"]["valid"]
    persisted = CadDocument.open(source)
    route = next(entity for entity in persisted.entities if entity.get("process_route"))
    assert route["type"] == "LWPOLYLINE"
    assert route["process_line"]["service"] == "hydraulic"
    assert route["process_route"]["orthogonal"] is True
    assert [(point["x"], point["y"]) for point in route["vertices"]] == [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0)]

    audit = agent.execute_operation("cad.pid_audit", {"project_dir": str(tmp_path), "input_file": str(source)})["pid"]
    assert audit["valid"]
    assert len(audit["process_lines"]) == 1
    assert len(audit["process_lines"][0]["vertices"]) == 3
    assert len(audit["edges"]) == 2
    assert {node.get("symbol") for node in audit["nodes"] if node.get("symbol")} == {"PUMP", "VALVE"}


def test_process_route_rejects_non_orthogonal_waypoints(tmp_path):
    source = tmp_path / "route-invalid.dxf"
    write_dxf(source)
    agent = EngineeringAgent({"tools": {}})
    with pytest.raises(ValueError, match="PROCESS_ROUTE_NOT_ORTHOGONAL"):
        agent.execute_operation("cad.process_route_create", {
            "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
            "start": {"x": 0, "y": 0}, "end": {"x": 20, "y": 10},
            "waypoints": [{"x": 8, "y": 4}], "service": "hydraulic",
        })


def test_pid_valve_cut_pipe_splits_route_and_connects_valve(tmp_path):
    source = tmp_path / "valve.dxf"
    write_dxf(source)
    agent = EngineeringAgent({"tools": {}})
    created = agent.execute_operation("cad.process_line_create", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
        "start": {"x": 0, "y": 0}, "end": {"x": 40, "y": 0}, "service": "hydraulic",
    })
    line_id = next(entity["id"] for entity in CadDocument.open(source).entities if entity.get("process_line"))
    result = agent.execute_operation("cad.pid_valve_cut_pipe", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
        "entity_id": line_id, "fraction": 0.5, "radius": 3, "gap": 10, "symbol": "VALVE", "label": "V-01", "strict_standard": True,
    })
    assert result["persisted_validation"]["valid"]
    assert result["results"][0]["valve_id"]
    persisted = CadDocument.open(source)
    valve = persisted.entity(result["results"][0]["valve_id"])
    assert valve["pid_valve"]["source_process_line_id"] == line_id
    connectors = [entity for entity in persisted.entities if entity.get("pid_connector", {}).get("valve_id") == valve["id"]]
    assert len(connectors) == 2
    assert {port["name"] for port in valve["pid_ports"]} == {"inlet", "outlet"}
    audit = agent.execute_operation("cad.pid_audit", {"project_dir": str(tmp_path), "input_file": str(source)})["pid"]
    assert audit["valid"]
    assert any(node.get("id") == valve["id"] and node.get("symbol") == "VALVE" for node in audit["nodes"])
    assert {port["name"] for port in audit["ports"]} == {"inlet", "outlet"}
    assert {edge["connector"]["port"] for edge in audit["edges"] if edge.get("connector")} == {"inlet", "outlet"}
    assert len(audit["edges"]) == 4


def test_pid_symbol_ports_are_audited_and_round_tripped(tmp_path):
    source = tmp_path / "ports.dxf"
    write_dxf(source)
    agent = EngineeringAgent({"tools": {}})
    result = agent.execute_operation("cad.pid_symbol_create", {
        "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(source),
        "center": {"x": 5, "y": 5}, "symbol": "PUMP", "label": "P-01",
        "ports": [{"name": "suction", "direction": "in", "point": {"x": 0, "y": 5}}, {"name": "discharge", "direction": "out", "point": {"x": 10, "y": 5}}],
    })
    assert result["persisted_validation"]["valid"]
    persisted = CadDocument.open(source)
    pump = next(entity for entity in persisted.entities if entity.get("pid_symbol") == "PUMP" and entity.get("type") == "CIRCLE")
    assert {port["name"] for port in pump["pid_ports"]} == {"suction", "discharge"}

