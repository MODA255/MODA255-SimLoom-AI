"""Regression tests for the shared agent-facing capability matrix."""
from __future__ import annotations

import sys

from engineering_agent import EngineeringAgent
from engineering_agent.capability_matrix import (
    SCHEMA,
    build_action_matrix,
    summarize_action_matrix,
)
from engineering_agent.mcp_server import MCPServer


ACTION = "create_or_update_model"


def _config(requirements=None):
    return {
        "generate_scripts": False,
        "tools": {
            "catia": {
                "actions": {
                    ACTION: {
                        "script": "scripts/runner.py",
                        "command": [sys.executable, "{script}", "{request_file}"],
                        **({"capability_requirements": requirements} if requirements is not None else {}),
                    }
                }
            }
        },
    }


def test_action_matrix_preserves_requirements_and_explains_missing_capabilities():
    diagnostic = {
        "tool": "catia",
        "actions": [ACTION],
        "reachable": False,
        "bridge_url": None,
        "action_requirements": {
            ACTION: {
                "declared": {
                    "adapter_capabilities": ["parametric-geometry"],
                    "worker_features": ["durable_jobs"],
                },
                "status": "missing",
                "missing_adapter_capabilities": ["parametric-geometry"],
                "missing_worker_features": ["durable_jobs"],
            }
        },
    }
    actions = build_action_matrix("catia", diagnostic)
    assert actions == [{
        "tool": "catia",
        "name": ACTION,
        "status": "missing",
        "ready": False,
        "requirements": {
            "adapter_capabilities": ["parametric-geometry"],
            "worker_features": ["durable_jobs"],
        },
        "missing": {
            "adapter_capabilities": ["parametric-geometry"],
            "worker_features": ["durable_jobs"],
        },
        "discovered": True,
        "real_machine_verified": False,
        "evidence": {
            "level": "executable_found",
            "worker_reachable": False,
            "real_machine_verified": False,
        },
        "reason": "缺少适配器能力：parametric-geometry；worker 特性：durable_jobs",
    }]


def test_capabilities_expose_one_shared_matrix_for_agent_clients():
    capabilities = EngineeringAgent(_config()).capabilities()
    matrix = capabilities["capability_matrix"]
    assert matrix["schema_version"] == SCHEMA
    assert matrix["summary"] == {
        "total": 1,
        "ready": 1,
        "missing": 0,
        "not_probed": 0,
        "unavailable": 0,
        "discovered": 1,
        "real_machine_verified": 0,
    }
    action = matrix["tools"]["catia"]["actions"][0]
    assert action["name"] == ACTION
    assert action["status"] == "ready"
    assert action["evidence"]["level"] == "executable_found"
    assert action["real_machine_verified"] is False


def test_capability_matrix_counts_are_deterministic():
    actions = [
        {"status": "ready", "discovered": True, "real_machine_verified": False},
        {"status": "missing", "discovered": True, "real_machine_verified": False},
        {"status": "not_probed", "discovered": False, "real_machine_verified": False},
    ]
    assert summarize_action_matrix(actions) == {
        "total": 3,
        "ready": 1,
        "missing": 1,
        "not_probed": 1,
        "unavailable": 0,
        "discovered": 2,
        "real_machine_verified": 0,
    }


def test_mcp_output_schema_advertises_the_shared_matrix():
    server = MCPServer(EngineeringAgent(_config()), profile="safe")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    tool = next(item for item in listed["result"]["tools"] if item["name"] == "engineering_capabilities")
    matrix = tool["outputSchema"]["properties"]["capability_matrix"]
    assert matrix["properties"]["schema_version"]["const"] == SCHEMA


def test_mcp_runtime_returns_the_same_matrix_as_python_api():
    agent = EngineeringAgent(_config())
    server = MCPServer(agent, profile="safe")
    response = server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "engineering_capabilities", "arguments": {}},
    })
    assert response["result"]["structuredContent"]["capability_matrix"] == agent.capabilities()["capability_matrix"]

