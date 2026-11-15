from __future__ import annotations

from pathlib import Path

from engineering_agent import EngineeringAgent
from engineering_agent.cad_model import CadDocument
from engineering_agent.formats import write_dxf
from engineering_agent.mcp_server import MCPServer


def _call(server: MCPServer, name: str, arguments: dict) -> dict:
    return server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def test_agentcad_operations_inherit_active_document_context(tmp_path: Path):
    drawing = tmp_path / "context.dxf"
    write_dxf(drawing, lines=[{"x1": 0, "y1": 0, "x2": 1, "y2": 0}])
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")

    opened = _call(server, "operation.cad.document_open", {
        "project_dir": str(tmp_path),
        "input_file": drawing.name,
    })
    assert opened["result"].get("isError", False) is False
    context = opened["result"]["structuredContent"]["cad_context"]
    assert context["project_dir"] == str(tmp_path.resolve())
    assert context["input_file"] == drawing.name
    assert context["output_file"] == drawing.name

    listed = server.handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {"limit": 500},
    })
    context_tool = next(item for item in listed["result"]["tools"]
                        if item["name"] == "engineering_cad_context")
    assert context_tool["outputSchema"]["properties"]["context"]["type"] == ["object", "null"]

    inspected = _call(server, "engineering_cad_context", {"action": "inspect"})
    assert inspected["result"]["structuredContent"]["active"] is True

    edited = _call(server, "operation.add_line", {
        "p1": {"x": 2, "y": 0},
        "p2": {"x": 3, "y": 1},
    })
    assert edited["result"].get("isError", False) is False
    assert edited["result"]["structuredContent"]["operation"] == "cad.line_create"
    assert len(CadDocument.open(drawing).entities) == 2

    cleared = _call(server, "engineering_cad_context", {"action": "clear"})
    assert cleared["result"]["structuredContent"]["context"] is None
    missing_context = _call(server, "operation.add_line", {
        "p1": {"x": 4, "y": 0},
        "p2": {"x": 5, "y": 1},
    })
    assert missing_context["result"]["isError"] is True
    assert "MISSING_PARAMETER:input_file" in missing_context["result"]["structuredContent"]["error"]["message"]


def test_explicit_document_switch_does_not_write_to_old_active_context(tmp_path: Path):
    first = tmp_path / "first.dxf"
    second = tmp_path / "second.dxf"
    write_dxf(first, lines=[{"x1": 0, "y1": 0, "x2": 1, "y2": 0}])
    write_dxf(second, lines=[{"x1": 10, "y1": 0, "x2": 11, "y2": 0}])
    server = MCPServer(EngineeringAgent({"tools": {}}), profile="full")
    assert not _call(server, "operation.cad.document_open", {
        "project_dir": str(tmp_path), "input_file": first.name,
    })["result"].get("isError", False)

    edited = _call(server, "operation.add_line", {
        "input_file": second.name,
        "p1": {"x": 12, "y": 0},
        "p2": {"x": 13, "y": 1},
    })
    assert edited["result"].get("isError", False) is False
    assert len(CadDocument.open(first).entities) == 1
    assert len(CadDocument.open(second).entities) == 2
