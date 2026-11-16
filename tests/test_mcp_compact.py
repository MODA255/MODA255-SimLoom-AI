from __future__ import annotations

import json

from engineering_agent import EngineeringAgent
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
