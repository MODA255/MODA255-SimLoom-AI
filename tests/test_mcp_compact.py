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


def test_agentcad_history_and_rollback_facades_use_durable_cad_history(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    write_dxf(tmp_path / "history.dxf", lines=[])
    applied = _call(server, "apply_operations", {
        "project_dir": str(tmp_path), "document": "history.dxf",
        "operations": [{"op": "add_line", "p1": {"x": 0, "y": 0}, "p2": {"x": 1, "y": 0}}],
    })
    assert applied["result"].get("isError", False) is False
    history = _call(server, "history", {"project_dir": str(tmp_path), "document": "history.dxf"})
    assert history["result"].get("isError", False) is False
    assert history["result"]["structuredContent"]["history"]["can_undo"] is True
    rollback = _call(server, "rollback", {"project_dir": str(tmp_path), "document": "history.dxf"})
    assert rollback["result"].get("isError", False) is False
    assert len(CadDocument.open(tmp_path / "history.dxf").entities) == 0
    safe = MCPServer(EngineeringAgent({"tools": {}}), profile="safe", toolset="compact")
    denied = _call(safe, "rollback", {"project_dir": str(tmp_path), "document": "history.dxf"})
    assert denied["result"]["isError"] is True


def test_validate_drawing_facade_accepts_generic_dxf_and_requirements(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    drawing = tmp_path / "generic-validate.dxf"
    write_dxf(drawing, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    result = _call(server, "validate_drawing", {
        "project_dir": str(tmp_path), "document": drawing.name,
        "requirements": {"required_entity_types": ["LINE"], "min_entity_count": 1},
    })
    assert result["result"].get("isError", False) is False
    assert result["result"]["structuredContent"]["validation"]["valid"] is True


def test_agentcad_standard_library_facade_maps_item_and_size_queries(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    result = _call(server, "standard_library", {"item": "fasteners", "size": "M8"})
    assert result["result"].get("isError", False) is False
    categories = result["result"]["structuredContent"]["categories"]
    assert "fasteners" in categories
    assert any("M8" in str(item) for item in categories["fasteners"])


def test_agentcad_checkpoint_and_recover_facades_expose_durable_transactions(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    write_dxf(tmp_path / "checkpoint.dxf", lines=[])
    applied = _call(server, "apply_operations", {
        "project_dir": str(tmp_path), "document": "checkpoint.dxf",
        "operations": [{"op": "add_line", "p1": {"x": 0, "y": 0}, "p2": {"x": 1, "y": 0}}],
    })
    assert applied["result"].get("isError", False) is False
    checkpoints = _call(server, "list_checkpoints", {"project_dir": str(tmp_path)})
    assert checkpoints["result"].get("isError", False) is False
    assert checkpoints["result"]["structuredContent"]["count"] >= 1
    recovered = _call(server, "recover", {"project_dir": str(tmp_path), "max_age_seconds": 0})
    assert recovered["result"].get("isError", False) is False
    assert recovered["result"]["structuredContent"]["schema_version"] == "simloom-ai/cad-recovery/v1"
    safe = MCPServer(EngineeringAgent({"tools": {}}), profile="safe", toolset="compact")
    denied = _call(safe, "recover", {"project_dir": str(tmp_path)})
    assert denied["result"]["isError"] is True


def test_agentcad_refine_and_deliver_facades_close_quality_and_delivery_loop(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    drawing = tmp_path / "deliver.dxf"
    write_dxf(drawing, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    refined = _call(server, "refine_drawing", {"project_dir": str(tmp_path), "document": drawing.name})
    assert refined["result"].get("isError", False) is False
    delivered = _call(server, "deliver_drawing", {
        "project_dir": str(tmp_path), "document": drawing.name, "output_dir": "delivery",
    })
    assert delivered["result"].get("isError", False) is False
    assert (tmp_path / "delivery" / "manifest.json").is_file()
    assert (tmp_path / "delivery" / "deliver.dxf").is_file()


def test_agentcad_search_and_metrics_facades_improve_tool_discovery(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    searched = _call(server, "search_tools", {"query": "constraint", "limit": 5})
    assert searched["result"].get("isError", False) is False
    results = searched["result"]["structuredContent"]["results"]
    assert results and any("constraint" in item["name"] for item in results)
    metrics = _call(server, "get_metrics")
    assert metrics["result"].get("isError", False) is False
    payload = metrics["result"]["structuredContent"]
    assert payload["tool_calls"] >= 2
    assert payload["operation_count"] >= 160


def test_agentcad_preflight_facades_check_plan_and_single_action(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    plan = _call(server, "preflight", {"project_dir": str(tmp_path), "prompt": "create a CATIA model"})
    assert plan["result"].get("isError", False) is False
    assert plan["result"]["structuredContent"]["schema_version"] == "simloom-ai/software-preflight/v1"
    action = _call(server, "action_preflight", {"project_dir": str(tmp_path), "tool": "catia", "action": "create_or_update_model"})
    assert action["result"].get("isError", False) is False
    assert action["result"]["structuredContent"]["schema_version"] == "simloom-ai/action-preflight/v1"


def test_agentcad_import_and_autocad_audit_facades_preserve_backend_truth(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    drawing = tmp_path / "source.dxf"
    write_dxf(drawing, lines=[])
    audit = _call(server, "autocad_audit", {"project_dir": str(tmp_path), "document": drawing.name})
    assert audit["result"].get("isError", False) is False
    assert "local_validation" in audit["result"]["structuredContent"]
    imported = _call(server, "import_drawing", {
        "project_dir": str(tmp_path), "source": drawing.name, "output": "roundtrip.dxf",
    })
    # Without AutoCAD Core Console the façade returns a structured backend error.
    assert imported["result"]["isError"] is True
    assert "Windows worker" in imported["result"]["structuredContent"]["error"]["message"] or "BACKEND_UNAVAILABLE" in imported["result"]["structuredContent"]["error"]["message"]


def test_agentcad_discovery_helpers_delegate_to_compact_operation_protocol(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    listed = _call(server, "list_operations", {"category": "cad", "detail": False, "limit": 5})
    assert listed["result"].get("isError", False) is False
    assert listed["result"]["structuredContent"]["operations"]
    schema = _call(server, "tool_schema", {"name": "add_line"})
    assert schema["result"].get("isError", False) is False
    assert schema["result"]["structuredContent"]["schemas"][0]["name"] == "add_line"
    write_dxf(tmp_path / "helper.dxf", lines=[])
    called = _call(server, "call_tool", {"name": "add_line", "arguments": {
        "project_dir": str(tmp_path), "input_file": "helper.dxf", "output_file": "helper.dxf",
        "p1": {"x": 0, "y": 0}, "p2": {"x": 1, "y": 0},
    }})
    assert called["result"].get("isError", False) is False


def test_deliver_drawing_facade_exports_requested_plot_formats_into_manifest(tmp_path):
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full", toolset="compact")
    drawing = tmp_path / "multi-delivery.dxf"
    write_dxf(drawing, lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0}])
    result = _call(server, "deliver_drawing", {
        "project_dir": str(tmp_path), "document": drawing.name, "output_dir": "multi-delivery",
        "formats": ["dxf", "svg"],
    })
    assert result["result"].get("isError", False) is False
    manifest = result["result"]["structuredContent"]["manifest"]
    assert manifest["formats"] == ["dxf", "svg"]
    assert (tmp_path / "multi-delivery" / "multi-delivery.svg").is_file()
    assert len(manifest["artifacts"]) == 2
