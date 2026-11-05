from __future__ import annotations

import json
import sys
import base64
import hashlib
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
        self.schema_version = "engineering-agent/mcp/v1"
        self._subscriptions: set[str] = set()

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            params = request.get("params") or {}
            requested = params.get("protocolVersion")
            if requested and requested not in {"2024-11-05", "2025-03-26"}:
                return self._error(request_id, ProtocolError(ErrorCode.INVALID_ARGUMENTS, f"UNSUPPORTED_PROTOCOL_VERSION:{requested}"))
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2024-11-05", "serverInfo": {"name": "engineering-agent", "version": "0.4.0", "schemaVersion": self.schema_version, "supportedSchemaVersions": [self.schema_version]}, "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": True, "listChanged": False}, "prompts": {"listChanged": False}}, "profile": self.profile}}
        if method == "tools/list":
            try:
                return {"jsonrpc": "2.0", "id": request_id, "result": self._paged(self._tools(), request.get("params") or {}, "tools")}
            except Exception as exc:
                return self._error(request_id, exc)
        if method == "resources/list":
            params = request.get("params") or {}
            try:
                return {"jsonrpc": "2.0", "id": request_id, "result": self._paged(self._resources(params), params, "resources")}
            except Exception as exc:
                return self._error(request_id, exc)
        if method == "resources/templates/list":
            params = request.get("params") or {}
            try:
                return {"jsonrpc": "2.0", "id": request_id, "result": self._paged(self._resource_templates(), params, "resourceTemplates")}
            except Exception as exc:
                return self._error(request_id, exc)
        if method == "resources/subscribe":
            try:
                uri = str((request.get("params") or {}).get("uri", ""))
                if not uri:
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "resource URI is required")
                self._read_resource(uri)
                self._subscriptions.add(uri)
                return {"jsonrpc": "2.0", "id": request_id, "result": {"uri": uri, "subscribed": True, "schemaVersion": self.schema_version}}
            except Exception as exc:
                return self._error(request_id, exc)
        if method == "resources/unsubscribe":
            uri = str((request.get("params") or {}).get("uri", ""))
            self._subscriptions.discard(uri)
            return {"jsonrpc": "2.0", "id": request_id, "result": {"uri": uri, "subscribed": False}}
        if method == "resources/read":
            try:
                return {"jsonrpc": "2.0", "id": request_id, "result": {"contents": [self._read_resource(request.get("params", {}).get("uri", ""))]}}
            except Exception as exc:
                return self._error(request_id, exc)
        if method == "prompts/list":
            params = request.get("params") or {}
            try:
                return {"jsonrpc": "2.0", "id": request_id, "result": self._paged(self._prompts(), params, "prompts")}
            except Exception as exc:
                return self._error(request_id, exc)
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
        elif isinstance(exc, RuntimeError) and ("BACKEND_UNAVAILABLE" in str(exc) or "REQUIRES_WINDOWS" in str(exc)):
            envelope = failure(ProtocolError(ErrorCode.BACKEND_UNAVAILABLE, str(exc), retryable=False))
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
            tools.append({"name": "operation." + operation["name"], "description": operation["description"], "inputSchema": {"type": "object", "properties": operation.get("parameters_schema", {}), "required": operation.get("required_parameters", []), "x-engineering-schema-version": operation.get("version", "1.0")}})
        return tools

    @staticmethod
    def _encode_cursor(index: int) -> str:
        return base64.urlsafe_b64encode(str(index).encode("ascii")).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> int:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = int(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
            if value < 0:
                raise ValueError
            return value
        except (ValueError, UnicodeDecodeError, base64.binascii.Error) as exc:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "invalid pagination cursor") from exc

    def _paged(self, items: list[dict[str, Any]], params: dict[str, Any], key: str) -> dict[str, Any]:
        limit = int(params.get("limit", 50))
        if limit < 1 or limit > 500:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "limit must be between 1 and 500")
        start = self._decode_cursor(str(params["cursor"])) if params.get("cursor") else 0
        page = items[start:start + limit]
        result = {key: page, "schemaVersion": self.schema_version}
        if start + limit < len(items):
            result["nextCursor"] = self._encode_cursor(start + limit)
        return result

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
            value = {"operations": self.operations.list(), "schemaVersion": self.schema_version}
        elif parsed.netloc == "versions":
            value = {"versions": VersionStore(root).list(), "timeline": VersionStore(root).timeline(), "schemaVersion": self.schema_version}
        elif parsed.netloc == "project":
            value = self.agent.inspect_project(root)
        else:
            raise ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, f"unknown resource: {uri}")
        serialized = json.dumps(value, ensure_ascii=False)
        return {"uri": uri, "mimeType": "application/json", "text": serialized, "textHash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(), "schemaVersion": self.schema_version}

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
        requested_schema = arguments.get("schema_version") or arguments.get("schemaVersion")
        if requested_schema and requested_schema not in {self.schema_version, "1.0"}:
            raise ProtocolError(ErrorCode.SCHEMA_VERSION_UNSUPPORTED, f"unsupported schema version: {requested_schema}")
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
