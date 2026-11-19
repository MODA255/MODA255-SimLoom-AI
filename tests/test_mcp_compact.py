from __future__ import annotations

import json

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_config import build_config
from engineering_agent.mcp_server import MCPServer


def _call(server: MCPServer, name: str, arguments: dict | None = None) -> dict:
    return server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })


def test_compact_toolset_discovers_operation_schemas_on_demand(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialized["result"]["toolset"] == "compact"

    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"limit": 500}})
    names = {item["name"] for item in listed["result"]["tools"]}
    assert "engineering_operation" in names
    assert "engineering_operation_schema" in names
    assert "engineering_operations" in names
    assert not any(name.startswith("operation.") for name in names)

    index = _call(server, "engineering_operations", {"operations": ["add_line", "cad.circle_create"]})
    entries = index["result"]["structuredContent"]["operations"]
    assert {item["name"] for item in entries} == {"add_line", "cad.circle_create"}
    assert all("parameters_schema" not in item for item in entries)

    schemas = _call(server, "engineering_operation_schema", {"operations": ["add_line"]})
    entry = schemas["result"]["structuredContent"]["schemas"][0]
    assert entry["name"] == "add_line"
    assert entry["compatibility"]["alias_for"] == "cad.line_create"
    assert "start" in entry["parameters_schema"]

    resource = server.handle({
        "jsonrpc": "2.0", "id": 3, "method": "resources/read",
        "params": {"uri": "engineering://operations"},
    })
    resource_value = json.loads(resource["result"]["contents"][0]["text"])
    assert resource_value["operations"] and "parameters_schema" not in resource_value["operations"][0]

    filtered = server.handle({
        "jsonrpc": "2.0", "id": 4, "method": "resources/read",
        "params": {"uri": "engineering://operations?detail=true&category=catia&limit=2"},
    })
    filtered_value = json.loads(filtered["result"]["contents"][0]["text"])
    assert filtered_value["operations"]
    assert len(filtered_value["operations"]) <= 2
    assert all(item["name"].startswith("catia.") for item in filtered_value["operations"])
    assert "parameters_schema" in filtered_value["operations"][0]

    single = server.handle({
        "jsonrpc": "2.0", "id": 5, "method": "resources/read",
        "params": {"uri": "engineering://operation?name=add_line"},
    })
    single_value = json.loads(single["result"]["contents"][0]["text"])
    assert single_value["operation"]["name"] == "add_line"
    assert "parameters_schema" in single_value["operation"]
    assert single_value["operation"]["compatibility"]["alias_for"] == "cad.line_create"


def test_compact_dispatcher_keeps_transaction_and_profile_guards(tmp_path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "output.dxf"
    write_dxf(source, lines=[])
    full = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    result = _call(full, "engineering_operation", {
        "operation": "add_line",
        "arguments": {
            "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
            "p1": {"x": 0, "y": 0}, "p2": {"x": 10, "y": 0},
        },
    })
    assert result["result"].get("isError") is not True
    assert result["result"]["structuredContent"]["operation"] == "cad.line_create"
    assert output.is_file()

    safe = MCPServer(EngineeringAgent({"tools": {}}), profile="safe", toolset="compact")
    denied = _call(safe, "engineering_operation", {
        "operation": "add_line",
        "arguments": {
            "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(tmp_path / "denied.dxf"),
            "p1": {"x": 0, "y": 0}, "p2": {"x": 10, "y": 0},
        },
    })
    assert denied["result"]["isError"] is True
    assert "MCP_PROFILE_READ_ONLY" in denied["result"]["structuredContent"]["error"]["message"]


def test_mcp_config_can_select_compact_toolset(tmp_path):
    config = build_config(client="generic", config_path=tmp_path / "agent.json", toolset="compact")
    args = config["mcpServers"]["simloom-ai"]["args"]
    assert "--toolset" in args
    assert args[args.index("--toolset") + 1] == "compact"


def test_agentcad_named_cad_facade_uses_same_transaction_and_context_path(tmp_path):
    source = tmp_path / "source.dxf"
    edited = tmp_path / "edited.dxf"
    write_dxf(source, lines=[{"x1": 0, "y1": 0, "x2": 1, "y2": 0}])
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")

    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"limit": 500}})
    names = {item["name"] for item in listed["result"]["tools"]}
    assert {"apply_operations", "read_drawing", "measure_entities", "verify_drawing", "get_diff"} <= names

    applied = _call(server, "apply_operations", {
        "project_dir": str(tmp_path), "document": source.name, "output_file": edited.name,
        "operations": [{"op": "add_line", "p1": {"x": 2, "y": 0}, "p2": {"x": 3, "y": 1}}],
    })
    assert applied["result"].get("isError", False) is False
    assert len(CadDocument.open(edited).entities) == 2
    assert applied["result"]["structuredContent"]["operation"] == "cad.batch_apply"

    measured = _call(server, "measure_entities", {
        "project_dir": str(tmp_path), "document": edited.name, "entity_type": "LINE",
    })
    assert measured["result"].get("isError", False) is False
    assert measured["result"]["structuredContent"]["summary"]["measured_count"] == 2

    inspected = _call(server, "read_drawing", {"project_dir": str(tmp_path), "document": edited.name})
    assert inspected["result"].get("isError", False) is False
    assert inspected["result"]["structuredContent"]["document"]["entity_count"] == 2

    verified = _call(server, "verify_drawing", {"project_dir": str(tmp_path), "document": edited.name})
    assert verified["result"].get("isError", False) is False
    assert verified["result"]["structuredContent"]["validation"]["valid"] is True

    diff = _call(server, "get_diff", {
        "project_dir": str(tmp_path), "document": source.name, "right_file": edited.name,
    })
    assert diff["result"].get("isError", False) is False
    assert diff["result"]["structuredContent"]["diff"]["change_count"] >= 1

    safe = MCPServer(EngineeringAgent({"tools": {}}), profile="safe", toolset="compact")
    denied = _call(safe, "apply_operations", {
        "project_dir": str(tmp_path), "document": source.name,
        "operations": [{"op": "add_line", "p1": {"x": 4, "y": 0}, "p2": {"x": 5, "y": 1}}],
    })
    assert denied["result"]["isError"] is True
    assert "MCP_PROFILE_READ_ONLY" in denied["result"]["structuredContent"]["error"]["message"]


def test_agentcad_create_and_inspect_tools_use_native_document_operation(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    created = _call(server, "create_drawing", {
        "project_dir": str(tmp_path), "output": "blank.dxf", "units": "mm", "annotation_scale": 2,
    })
    assert created["result"].get("isError", False) is False
    assert created["result"]["structuredContent"]["document"]["entity_count"] == 0
    inspected = _call(server, "inspect_entities", {
        "project_dir": str(tmp_path), "document": "blank.dxf", "limit": 10,
    })
    assert inspected["result"].get("isError", False) is False
    assert inspected["result"]["structuredContent"]["total_count"] == 0
    safe = MCPServer(EngineeringAgent({"tools": {}}), profile="safe", toolset="compact")
    denied = _call(safe, "create_drawing", {"project_dir": str(tmp_path), "output": "safe.dxf"})
    assert denied["result"]["isError"] is True
    assert "MCP_PROFILE_READ_ONLY" in denied["result"]["structuredContent"]["error"]["message"]


def test_agentcad_apply_constraints_facade_checks_and_enforces(tmp_path):
    source = tmp_path / "constraints.dxf"
    output = tmp_path / "constraints-enforced.dxf"
    write_dxf(source, lines=[{"x1": 0, "y1": 0, "x2": 3, "y2": 4}])
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    checked = _call(server, "apply_constraints", {
        "project_dir": str(tmp_path), "document": source.name,
        "constraints": [{"type": "horizontal", "a": "e00001"}], "mode": "check",
    })
    assert checked["result"].get("isError", False) is False
    assert checked["result"]["structuredContent"]["valid"] is False
    enforced = _call(server, "apply_constraints", {
        "project_dir": str(tmp_path), "document": source.name, "output_file": output.name,
        "constraints": [{"type": "horizontal", "a": "e00001"}], "mode": "enforce",
    })
    assert enforced["result"].get("isError", False) is False
    assert enforced["result"]["structuredContent"]["checks"][0]["satisfied"] is True


def test_agentcad_draw_from_spec_facade_creates_plate(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    result = _call(server, "draw_from_spec", {
        "project_dir": str(tmp_path), "output": "plate.dxf",
        "spec": {"kind": "plate", "width": 20, "height": 10, "holes": []},
    })
    assert result["result"].get("isError", False) is False
    assert result["result"]["structuredContent"]["hole_count"] == 0


def test_agentcad_revise_and_validate_facade(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    created = _call(server, "draw_from_spec", {
        "project_dir": str(tmp_path), "output": "plate.dxf",
        "spec": {"kind": "plate", "width": 20, "height": 10, "holes": []},
    })
    assert created["result"].get("isError", False) is False
    revised = _call(server, "revise_drawing", {
        "project_dir": str(tmp_path), "document": "plate.dxf", "output_file": "plate.dxf",
        "changes": {"width": 30},
    })
    assert revised["result"].get("isError", False) is False
    checked = _call(server, "validate_drawing", {"project_dir": str(tmp_path), "document": "plate.dxf"})
    assert checked["result"].get("isError", False) is False
    assert checked["result"]["structuredContent"]["valid"] is True


def test_agentcad_apply_batch_processes_multiple_drawings_and_reports_each_result(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    write_dxf(tmp_path / "one.dxf", lines=[])
    write_dxf(tmp_path / "two.dxf", lines=[])
    result = _call(server, "apply_batch", {
        "jobs": [
            {"project_dir": str(tmp_path), "document": "one.dxf", "operations": [
                {"op": "add_line", "p1": {"x": 0, "y": 0}, "p2": {"x": 1, "y": 0}}]},
            {"project_dir": str(tmp_path), "document": "two.dxf", "operations": [
                {"op": "add_circle", "center": {"x": 2, "y": 2}, "radius": 1}]},
        ],
    })
    assert result["result"].get("isError", False) is False
    batch = result["result"]["structuredContent"]
    assert batch["completed"] == 2 and batch["failed"] == 0
    assert len(CadDocument.open(tmp_path / "one.dxf").entities) == 1
    assert len(CadDocument.open(tmp_path / "two.dxf").entities) == 1


def test_agentcad_export_drawing_facade_uses_audited_plot_pipeline(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    source = tmp_path / "export-source.dxf"
    write_dxf(source, lines=[{"x1": 0, "y1": 0, "x2": 20, "y2": 0}])
    result = _call(server, "export_drawing", {
        "project_dir": str(tmp_path), "document": source.name, "output": "preview.svg", "format": "svg",
    })
    assert result["result"].get("isError", False) is False
    assert (tmp_path / "preview.svg").is_file()
    assert result["result"]["structuredContent"]["validation"]["valid"] is True


def test_agentcad_runtime_facades_expose_status_library_and_controlled_script(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    status = _call(server, "agentcad_status", {"project_dir": str(tmp_path), "label": "test"})
    assert status["result"]["structuredContent"]["connected"] is True
    library = _call(server, "standard_library", {"category": "fasteners", "query": "M8"})
    assert library["result"].get("isError", False) is False
    source = tmp_path / "script-source.dxf"
    output = tmp_path / "script-output.dxf"
    write_dxf(source, lines=[])
    denied = _call(server, "run_script", {
        "project_dir": str(tmp_path), "document": source.name, "output": output.name,
        "code": "print('x')",
    })
    assert denied["result"]["isError"] is True
    assert "CAD_SCRIPTS_DISABLED" in denied["result"]["structuredContent"]["error"]["message"]


def test_agentcad_requirement_extraction_and_document_revision_facade(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    spec = tmp_path / "requirements.md"
    spec.write_text("plate width 120 mm, height 70 mm, hole diameter 10 mm, edge offset 12 mm", encoding="utf-8")
    extracted = _call(server, "extract_requirements", {"project_dir": str(tmp_path), "document": spec.name})
    assert extracted["result"].get("isError", False) is False
    assert extracted["result"]["structuredContent"]["facts"]["width"] == 120
    created = _call(server, "draw_from_spec", {
        "project_dir": str(tmp_path), "output": "plate.dxf",
        "spec": {"kind": "plate", "width": 100, "height": 60, "holes": [{"pattern": "corners", "diameter": 8, "edge_offset": 10}]},
    })
    assert created["result"].get("isError", False) is False
    revised = _call(server, "revise_from_document", {
        "project_dir": str(tmp_path), "document": spec.name, "drawing": "plate.dxf", "output_file": "plate.dxf",
    })
    assert revised["result"].get("isError", False) is False
    assert revised["result"]["structuredContent"]["revision"]["drawing_spec"]["valid"] is True
