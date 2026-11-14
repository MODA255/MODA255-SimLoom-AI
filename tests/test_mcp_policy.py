from __future__ import annotations

import json

from engineering_agent import EngineeringAgent
from engineering_agent.mcp_server import MCPServer


def _listed(server):
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"limit": 500},
    })
    return {item["name"] for item in response["result"]["tools"]}


def _call(server, name, arguments=None):
    return server.handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    })


def test_mcp_allow_list_filters_discovery_and_direct_calls():
    server = MCPServer(EngineeringAgent({"mcp": {
        "allow_tools": ["engineering_plan", "operation.cad.document_open"],
    }}), profile="full")

    names = _listed(server)
    assert "engineering_plan" in names
    assert "engineering_doctor" not in names
    assert "operation.cad.document_open" in names
    assert "operation.cad.entity_move" not in names

    denied = _call(server, "engineering_doctor")
    assert denied["result"]["isError"] is True
    error = denied["result"]["structuredContent"]["error"]
    assert error["code"] == "MCP_POLICY_DENIED"
    assert error["details"]["reason"] == "allow_tools"


def test_mcp_deny_rules_override_allow_rules_and_support_operation_aliases():
    server = MCPServer(EngineeringAgent({"mcp": {
        "allow_tools": ["operation.*"],
        "allow_operations": ["cad.document_*"],
        "deny_operations": ["operation.cad.document_open"],
    }}), profile="full")

    names = _listed(server)
    assert "operation.cad.document_open" not in names
    assert "operation.cad.document_diff" in names

    denied = _call(server, "operation.cad.document_open")
    assert denied["result"]["isError"] is True
    error = denied["result"]["structuredContent"]["error"]
    assert error["code"] == "MCP_POLICY_DENIED"
    assert error["details"]["scope"] == "operation"
    assert error["details"]["reason"] == "deny_operations"


def test_mcp_policy_filters_operation_resource_and_operation_listing():
    server = MCPServer(EngineeringAgent({"mcp": {
        "allow_operations": ["cad.document_open"],
    }}), profile="full")

    listed = _call(server, "engineering_operations")
    operations = {item["name"] for item in listed["result"]["structuredContent"]["operations"]}
    assert operations == {"cad.document_open"}

    resource = server.handle({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "resources/read",
        "params": {"uri": "engineering://operations"},
    })
    resource_operations = {
        item["name"] for item in json.loads(resource["result"]["contents"][0]["text"])["operations"]
    }
    assert resource_operations == {"cad.document_open"}


def test_mcp_policy_and_safe_profile_are_both_enforced():
    server = MCPServer(EngineeringAgent({"mcp": {"allow_tools": ["engineering_run"]}}), profile="safe")
    assert "engineering_run" not in _listed(server)

    allowed_by_policy = _call(server, "engineering_run", {"prompt": "执行任务", "dry_run": False})
    assert allowed_by_policy["result"]["isError"] is True
    assert "MCP_PROFILE_READ_ONLY" in allowed_by_policy["result"]["structuredContent"]["error"]["message"]

    denied_by_policy = _call(server, "engineering_plan", {"prompt": "用 Adams 做动力学"})
    assert denied_by_policy["result"]["structuredContent"]["error"]["code"] == "MCP_POLICY_DENIED"


def test_mcp_initialize_publishes_effective_policy():
    server = MCPServer(EngineeringAgent({"mcp": {"deny_tools": ["engineering_doctor"]}}))
    initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialized["result"]["policy"]["deny_tools"] == ["engineering_doctor"]
