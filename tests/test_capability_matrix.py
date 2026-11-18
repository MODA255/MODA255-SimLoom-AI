"""Regression tests for the shared agent-facing capability matrix."""
from __future__ import annotations

import json
import sys

from engineering_agent import EngineeringAgent
from engineering_agent.capability_matrix import (
    SCHEMA,
    build_action_matrix,
    summarize_action_matrix,
)
from engineering_agent.cli import _parser
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
        "next_action": "configure_capabilities",
    }]


def test_capabilities_expose_one_shared_matrix_for_agent_clients():
    capabilities = EngineeringAgent(_config()).capabilities()
    matrix = capabilities["capability_matrix"]
    assert matrix["schema_version"] == SCHEMA
    assert matrix["probe"] is True
    assert len(matrix["matrix_digest"]) == 64
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
    assert action["next_action"] == "run_preflight"


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


def test_mcp_capability_resource_is_read_only_and_matches_tool_output():
    agent = EngineeringAgent(_config())
    server = MCPServer(agent, profile="safe")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}})
    assert any(item["uri"] == "engineering://capabilities" for item in listed["result"]["resources"])
    templates = server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list", "params": {}})
    assert any(item["uriTemplate"] == "engineering://capabilities"
               for item in templates["result"]["resourceTemplates"])
    resource = server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                              "params": {"uri": "engineering://capabilities"}})
    value = json.loads(resource["result"]["contents"][0]["text"])
    assert value["capability_matrix"] == agent.capabilities()["capability_matrix"]


def test_mcp_action_profile_resource_exposes_bindings_without_secrets(tmp_path):
    config = _config()
    config["tools"]["catia"]["bridge_token"] = "do-not-export"
    config["tools"]["catia"]["actions"][ACTION]["repair_strategies"] = [{
        "id": "low-risk-retry", "when": {"metric": "stress", "failed": True},
        "parameter_patch": {"load_case": "backup"}, "priority": 4, "risk": "low",
        "max_attempts": 2,
    }]
    config["windows_bridge"] = {
        "commands": {
            "catia.create_or_update_model": ["C:/Python/python.exe", "{script}", "{request_file}"]
        }
    }
    agent = EngineeringAgent(config)
    server = MCPServer(agent, profile="full")

    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list",
                            "params": {"root": str(tmp_path)}})
    assert any(item["uri"].startswith("engineering://action-profile?")
               for item in listed["result"]["resources"])
    templates = server.handle({"jsonrpc": "2.0", "id": 2, "method": "resources/templates/list"})
    assert any(item["uriTemplate"] == "engineering://action-profile?root={root}"
               for item in templates["result"]["resourceTemplates"])

    uri = f"engineering://action-profile?root={tmp_path}"
    resource = server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read",
                              "params": {"uri": uri}})
    value = json.loads(resource["result"]["contents"][0]["text"])
    assert value["schema_version"] == "simloom-ai/action-profile/v1"
    assert value["actions"][0]["tool"] == "catia"
    assert value["actions"][0]["repair_strategies"][0]["priority"] == 4
    assert value["actions"][0]["repair_strategies"][0]["parameter_keys"] == ["load_case"]
    assert "do-not-export" not in json.dumps(value, ensure_ascii=False)


def test_mcp_worker_config_resource_is_redacted_template():
    agent = EngineeringAgent({"windows_bridge": {"token": "secret-value", "port": 9001}})
    server = MCPServer(agent, profile="full")
    resource = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/read",
                              "params": {"uri": "engineering://worker-config"}})
    value = json.loads(resource["result"]["contents"][0]["text"])
    assert value["schema_version"] == "simloom-ai/worker-config-template/v1"
    assert value["template_only"] is True
    assert value["token"] == "<set-on-windows>"
    assert "secret-value" not in json.dumps(value, ensure_ascii=False)


def test_action_profile_contains_typed_operation_catalog_even_without_bindings():
    profile = EngineeringAgent({"tools": {}}).config
    from engineering_agent.action_profile import build_action_profile

    value = build_action_profile(profile)
    assert value["operation_catalog_schema"] == "simloom-ai/software-operation-catalog/v1"
    assert value["summary"]["catalog_operations"] > 0
    catia = value["tools"]["catia"]
    ansys = value["tools"]["ansys"]
    assert catia["actions"] == []
    assert any(item["name"] == "catia.create_model" for item in catia["operation_catalog"])
    assert any(item["name"] == "ansys.solve_configured_model" for item in ansys["operation_catalog"])
    assert all("parameters_schema" not in item for item in catia["operation_catalog"])


def test_probe_flag_allows_offline_capability_discovery_without_network(monkeypatch):
    config = _config({"worker_features": ["durable_jobs"]})
    config["tools"]["catia"]["bridge_url"] = "http://worker.invalid"
    agent = EngineeringAgent(config)
    adapter = agent.adapters[next(name for name in agent.adapters if name.value == "catia")]

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("probe=False must not call the remote worker")

    monkeypatch.setattr(adapter, "_post", fail_if_called)
    matrix = agent.capabilities(probe=False)["capability_matrix"]
    action = matrix["tools"]["catia"]["actions"][0]
    assert action["status"] == "not_probed"
    assert action["ready"] is False
    assert matrix["probe"] is False


def test_capability_digest_is_stable_and_changes_with_probe_mode():
    agent = EngineeringAgent(_config())
    first = agent.capabilities(probe=False)["capability_matrix"]
    second = agent.capabilities(probe=False)["capability_matrix"]
    probed = agent.capabilities(probe=True)["capability_matrix"]
    assert first["matrix_digest"] == second["matrix_digest"]
    assert first["matrix_digest"] != probed["matrix_digest"]


def test_cli_exposes_explicit_no_probe_switch():
    args = _parser().parse_args(["capabilities", "--no-probe"])
    assert args.command == "capabilities" and args.probe is False

