from __future__ import annotations

import json
import sys
from urllib.parse import parse_qs, urlparse
from typing import Any

from .agent import EngineeringAgent
from .operations import OperationRegistry
from .protocol import ErrorCode, ProtocolError, failure, parse_json_line
from .validation import validate_plan
from .versioning import VersionStore


class MCPServer:
    """Small stdio MCP-compatible tool surface for local agent clients."""

    def __init__(self, agent: EngineeringAgent, profile: str = "safe"):
        self.agent = agent
        self.operations = OperationRegistry()
        self.profile = profile if profile in {"safe", "full"} else "safe"

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "engineering-agent", "version": "0.3.0"}, "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}, "prompts": {"listChanged": False}}, "profile": self.profile}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self._tools()}}
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": self._resources(request.get("params", {}))}}
        if method == "resources/templates/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resourceTemplates": self._resource_templates()}}
        if method == "resources/read":
            try:
                return {"jsonrpc": "2.0", "id": request_id, "result": {"contents": [self._read_resource(request.get("params", {}).get("uri", ""))]}}
            except Exception as exc:
                return self._error(request_id, exc)
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"prompts": self._prompts()}}
        if method == "prompts/get":
            try:
                return {"jsonrpc": "2.0", "id": request_id, "result": self._get_prompt(request.get("params", {}))}
            except Exception as exc:
                return self._error(request_id, exc)
        if method == "tools/call":
            params = request.get("params", {})
            try:
                result = self.call(str(params.get("name", "")), params.get("arguments") or {})
                return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
            except Exception as exc:
                return self._error(request_id, exc)
        return self._error(request_id, ProtocolError(ErrorCode.INVALID_REQUEST, f"METHOD_NOT_FOUND:{method}"))

    @staticmethod
    def _error(request_id: Any, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, ProtocolError):
            envelope = failure(exc)
        elif isinstance(exc, FileNotFoundError):
            envelope = failure(ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, str(exc), details={"path": str(exc)}))
        elif isinstance(exc, ValueError):
            text = str(exc)
            code = ErrorCode.UNKNOWN_OPERATION if text.startswith("UNKNOWN_OPERATION:") else ErrorCode.INVALID_ARGUMENTS
            envelope = failure(ProtocolError(code, text))
        elif isinstance(exc, RuntimeError) and "LOCKED" in str(exc):
            envelope = failure(ProtocolError(ErrorCode.LOCKED, str(exc), retryable=True))
        else:
            envelope = failure(ProtocolError(ErrorCode.INTERNAL_ERROR, str(exc)))
        return {"jsonrpc": "2.0", "id": request_id, "error": envelope["error"]}

    def _tools(self) -> list[dict[str, Any]]:
        tools = [
            {"name": "engineering_plan", "description": "Create a validated four-tool execution plan", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "project_dir": {"type": "string"}}}},
            {"name": "engineering_run", "description": "Run a plan in dry-run or execution mode", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "project_dir": {"type": "string"}, "dry_run": {"type": "boolean"}}}},
            {"name": "engineering_generate", "description": "Generate vendor scripts and scenario files", "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string"}, "model_spec": {"type": "object"}}}},
            {"name": "engineering_doctor", "description": "Inspect configured vendor backends and COM", "inputSchema": {"type": "object"}},
            {"name": "engineering_operations", "description": "List typed operations and schemas", "inputSchema": {"type": "object"}},
            {"name": "engineering_ingest", "description": "Read STEP/CSV/JSON into the canonical object model", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "kind": {"type": "string"}}}},
            {"name": "engineering_project", "description": "Inspect all supported artifacts in a project", "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string"}}}},
            {"name": "engineering_version", "description": "Create an immutable project snapshot", "inputSchema": {"type": "object", "required": ["project_dir", "label", "paths"], "properties": {"project_dir": {"type": "string"}, "label": {"type": "string"}, "paths": {"type": "array"}}}},
            {"name": "engineering_diff", "description": "Compare two immutable snapshots", "inputSchema": {"type": "object", "required": ["project_dir", "left", "right"], "properties": {"project_dir": {"type": "string"}, "left": {"type": "string"}, "right": {"type": "string"}}}},
        ]
        for operation in self.operations.list():
            if self.profile == "safe" and operation["safety"] in {"write", "execute"}:
                continue
            tools.append({"name": "operation." + operation["name"], "description": operation["description"], "inputSchema": {"type": "object", "properties": operation.get("parameters_schema", {}), "required": operation.get("required_parameters", [])}})
        return tools

    def _resources(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        root = params.get("root", ".")
        resources = [
            {"uri": "engineering://operations", "name": "Operation schemas", "mimeType": "application/json"},
            {"uri": f"engineering://versions?root={root}", "name": "Project versions", "mimeType": "application/json"},
            {"uri": f"engineering://project?root={root}", "name": "Canonical project object", "mimeType": "application/json"},
        ]
        return resources

    @staticmethod
    def _resource_templates() -> list[dict[str, Any]]:
        return [
            {"uriTemplate": "engineering://project?root={root}", "name": "Canonical project object", "mimeType": "application/json"},
            {"uriTemplate": "engineering://versions?root={root}", "name": "Project versions and timeline", "mimeType": "application/json"},
        ]

    def _read_resource(self, uri: str) -> dict[str, Any]:
        parsed = urlparse(uri)
        if parsed.scheme != "engineering":
            raise ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, "unsupported resource URI")
        query = parse_qs(parsed.query)
        root = query.get("root", ["."])[0]
        if parsed.netloc == "operations":
            value = {"operations": self.operations.list()}
        elif parsed.netloc == "versions":
            value = {"versions": VersionStore(root).list(), "timeline": VersionStore(root).timeline()}
        elif parsed.netloc == "project":
            value = self.agent.inspect_project(root)
        else:
            raise ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, f"unknown resource: {uri}")
        return {"uri": uri, "mimeType": "application/json", "text": json.dumps(value, ensure_ascii=False)}

    @staticmethod
    def _prompts() -> list[dict[str, Any]]:
        return [
            {"name": "engineering_plan", "description": "Turn an engineering request into a validated plan", "arguments": [{"name": "task", "required": True}]},
            {"name": "engineering_review", "description": "Review result metrics against engineering rules", "arguments": [{"name": "result_file", "required": True}]},
        ]

    def _get_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "engineering_plan":
            return {"description": "Validated planning prompt", "messages": [{"role": "user", "content": {"type": "text", "text": f"Create a safe engineering plan for: {arguments.get('task', '')}"}}]}
        if name == "engineering_review":
            return {"description": "Result review prompt", "messages": [{"role": "user", "content": {"type": "text", "text": f"Review engineering result file: {arguments.get('result_file', '')}"}}]}
        raise ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, f"unknown prompt: {name}")

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "engineering_plan":
            plan = self.agent.plan(arguments.get("prompt", ""), project_dir=arguments.get("project_dir", "."))
            return {"plan": plan.to_dict(), "validation": validate_plan(plan)}
        if name == "engineering_run":
            return self.agent.run(arguments.get("prompt", ""), project_dir=arguments.get("project_dir", "."), dry_run=arguments.get("dry_run", True)).to_dict()
        if name == "engineering_generate":
            return self.agent.generate(arguments.get("project_dir", "."), arguments.get("model_spec") or {})
        if name == "engineering_doctor":
            return {"tools": self.agent.doctor()}
        if name == "engineering_operations":
            return {"operations": self.operations.list()}
        if name == "engineering_ingest":
            return self.agent.ingest(arguments["path"], arguments.get("kind"))
        if name == "engineering_project":
            return self.agent.inspect_project(arguments.get("project_dir", "."))
        if name == "engineering_version":
            return self.agent.create_version(arguments["project_dir"], arguments["label"], arguments["paths"], arguments.get("parent"))
        if name == "engineering_diff":
            return self.agent.diff_versions(arguments["project_dir"], arguments["left"], arguments["right"])
        if name.startswith("operation."):
            return self.agent.execute_operation(name[len("operation."):], arguments)
        raise ValueError(f"UNKNOWN_TOOL:{name}")


def serve_stdio(agent: EngineeringAgent, profile: str = "safe"):
    server = MCPServer(agent, profile=profile)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = server.handle(parse_json_line(line))
        except ProtocolError as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": failure(exc)["error"]}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
