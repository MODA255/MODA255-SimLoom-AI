from __future__ import annotations

import json
import sys
import base64
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from typing import Any

from .agent import EngineeringAgent
from .cad_model import CadDocument
from .operations import OperationRegistry
from .protocol import ErrorCode, ProtocolError, failure, parse_json_line
from .validation import validate_plan
from .versioning import VersionStore
from .viewer import asset_view


class MCPServer:
    """Small stdio MCP-compatible tool surface for local agent clients."""

    def __init__(self, agent: EngineeringAgent, profile: str = "safe"):
        self.agent = agent
        self.operations = OperationRegistry()
        self.profile = profile if profile in {"safe", "full"} else "safe"
        self.schema_version = "engineering-agent/mcp/v1"
        self._subscriptions: set[str] = set()
        self._pending_notifications: list[dict[str, Any]] = []

    def drain_notifications(self) -> list[dict[str, Any]]:
        """Return queued MCP notifications for a stdio host or test client."""
        pending, self._pending_notifications = self._pending_notifications, []
        return pending

    def _structured_content(self, tool_name: str, result: Any) -> dict[str, Any]:
        """Build the optional MCP structuredContent payload.

        Text content remains available for older clients.  Newer clients can
        consume the typed result without parsing JSON embedded in text.  The
        metadata is deliberately explicit so operation-level schema upgrades
        can be negotiated independently from the MCP transport schema.
        """
        if isinstance(result, dict):
            structured = {key: value for key, value in result.items() if key != "_mcp_content"}
        else:
            structured = {"value": result}
        structured.setdefault("schemaVersion", self.schema_version)
        if tool_name.startswith("operation."):
            try:
                spec = self.operations.get(tool_name[len("operation."):])
            except ValueError:
                spec = None
            if spec is not None:
                structured.setdefault("operationSchemaVersion", spec.version)
                structured.setdefault("operation", spec.name)
        return structured

    def _queue_resource_updates(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """Queue standard resource-update notifications after mutating calls."""
        mutating = (
            tool_name in {"engineering_generate", "engineering_offline_run", "engineering_version"}
            or tool_name == "engineering_run" and not bool(arguments.get("dry_run", True))
            or tool_name.startswith("operation.") and self.operations.get(tool_name[len("operation."):]).safety in {"write", "execute"}
        )
        if not mutating:
            return
        project_dir = str(arguments.get("project_dir") or arguments.get("root") or "")
        for uri in sorted(self._subscriptions):
            query = parse_qs(urlparse(uri).query)
            subscribed_root = query.get("root", [""])[0]
            if project_dir and subscribed_root and Path(project_dir).resolve() != Path(subscribed_root).resolve():
                continue
            self._pending_notifications.append({
                "jsonrpc": "2.0", "method": "notifications/resources/updated",
                "params": {"uri": uri, "schemaVersion": self.schema_version},
            })

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
                resource = self._read_resource(request.get("params", {}).get("uri", ""))
                contents = resource if isinstance(resource, list) else [resource]
                return {"jsonrpc": "2.0", "id": request_id, "result": {"contents": contents}}
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
                self._queue_resource_updates(str(params.get("name", "")), params.get("arguments") or {})
                tool_name = str(params.get("name", ""))
                structured = self._structured_content(tool_name, result)
                if isinstance(result, dict) and isinstance(result.get("_mcp_content"), list):
                    content = result["_mcp_content"]
                    return {"jsonrpc": "2.0", "id": request_id,
                            "result": {"content": content, "structuredContent": structured}}
                return {"jsonrpc": "2.0", "id": request_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                                   "structuredContent": structured}}
            except Exception as exc:
                return self._error(request_id, exc)
        return self._error(request_id, ProtocolError(ErrorCode.INVALID_REQUEST, f"METHOD_NOT_FOUND:{method}"))

    def _error(self, request_id: Any, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, ProtocolError):
            envelope = failure(exc)
        elif isinstance(exc, FileNotFoundError):
            envelope = failure(ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, str(exc), details={"path": str(exc)}))
        elif isinstance(exc, ValueError) and getattr(exc, "code", None):
            envelope = failure(ProtocolError(str(exc.code), str(exc)))
        elif isinstance(exc, ValueError):
            text = str(exc)
            code = ErrorCode.UNKNOWN_OPERATION if text.startswith("UNKNOWN_OPERATION:") else ErrorCode.INVALID_ARGUMENTS
            envelope = failure(ProtocolError(code, text))
        elif isinstance(exc, RuntimeError) and "CONFLICT" in str(exc):
            envelope = failure(ProtocolError(ErrorCode.CONFLICT, str(exc), retryable=False))
        elif isinstance(exc, RuntimeError) and "LOCKED" in str(exc):
            envelope = failure(ProtocolError(ErrorCode.LOCKED, str(exc), retryable=True))
        elif isinstance(exc, RuntimeError) and ("BACKEND_UNAVAILABLE" in str(exc) or "REQUIRES_WINDOWS" in str(exc)):
            envelope = failure(ProtocolError(ErrorCode.BACKEND_UNAVAILABLE, str(exc), retryable=False))
        else:
            envelope = failure(ProtocolError(ErrorCode.INTERNAL_ERROR, str(exc)))
        error = dict(envelope["error"])
        error.setdefault("schemaVersion", self.schema_version)
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def _tools(self) -> list[dict[str, Any]]:
        tools = [
            {"name": "engineering_plan", "description": "Create a validated four-tool execution plan", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "project_dir": {"type": "string"}}}},
            {"name": "engineering_preflight", "description": "Check planned vendor action bindings, scripts, commands and output contracts without starting CATIA, Ansys, Adams or VI-grade", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "project_dir": {"type": "string"}, "parameters": {"type": "object"}, "probe": {"type": "boolean", "default": False}}}},
            {"name": "engineering_run", "description": "Run a plan in dry-run or execution mode", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "project_dir": {"type": "string"}, "dry_run": {"type": "boolean"}}}},
            {"name": "engineering_generate", "description": "Generate vendor scripts and scenario files", "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string"}, "model_spec": {"type": "object"}}}},
            {"name": "engineering_offline_run", "description": "Run the deterministic CATIA-to-VI-grade fixture chain without commercial software", "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string"}, "model_spec": {"type": "object"}, "rules": {"type": "array"}}}},
            {"name": "engineering_doctor", "description": "Inspect configured vendor backends and COM", "inputSchema": {"type": "object"}},
            {"name": "engineering_operations", "description": "List typed operations and schemas", "inputSchema": {"type": "object"}},
            {"name": "engineering_ingest", "description": "Read STEP/CSV/JSON into the canonical object model", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "kind": {"type": "string"}}}},
            {"name": "engineering_project", "description": "Inspect all supported artifacts in a project", "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string"}}}},
            {"name": "engineering_asset_view", "description": "Read a project asset and return structured metadata plus an SVG image for agent inspection", "inputSchema": {"type": "object", "required": ["root", "path"], "properties": {"root": {"type": "string", "description": "Project root directory"}, "path": {"type": "string", "description": "Project-relative asset path"}, "include_image": {"type": "boolean", "default": True}, "max_image_bytes": {"type": "integer", "minimum": 1024, "maximum": 8000000, "default": 4000000}}}},
            {"name": "engineering_inspect_image", "description": "Read-only crop, magnify and rotate a PNG/JPEG/WebP drawing preview with source pixel coordinates", "inputSchema": {"type": "object", "required": ["root", "path"], "properties": {"root": {"type": "string"}, "path": {"type": "string"}, "region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}, "max_size": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1024}, "rotate": {"type": "integer", "enum": [0, 90, 180, 270], "default": 0}}}},
            {"name": "engineering_measure_image", "description": "Read-only source-pixel color measurement with optional pixel-to-drawing coordinate mapping and annotated PNG overlay", "inputSchema": {"type": "object", "required": ["root", "path", "target_color"], "properties": {"root": {"type": "string"}, "path": {"type": "string"}, "region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}, "target_color": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 255}, "minItems": 3, "maxItems": 3}, "tolerance": {"type": "integer", "minimum": 0, "maximum": 255, "default": 16}, "alpha_threshold": {"type": "integer", "minimum": 0, "maximum": 255, "default": 1}, "max_size": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1024}, "overlay": {"type": "boolean", "default": True}, "coordinate_system": {"type": "object", "description": "Full-image drawing coordinate extents; y_axis is up or down", "required": ["x_min", "x_max", "y_min", "y_max", "y_axis"], "properties": {"x_min": {"type": "number"}, "x_max": {"type": "number"}, "y_min": {"type": "number"}, "y_max": {"type": "number"}, "y_axis": {"type": "string", "enum": ["up", "down"]}}}}}},
            {"name": "engineering_version", "description": "Create an immutable project snapshot", "inputSchema": {"type": "object", "required": ["project_dir", "label", "paths"], "properties": {"project_dir": {"type": "string"}, "label": {"type": "string"}, "paths": {"type": "array"}}}},
            {"name": "engineering_diff", "description": "Compare two immutable snapshots", "inputSchema": {"type": "object", "required": ["project_dir", "left", "right"], "properties": {"project_dir": {"type": "string"}, "left": {"type": "string"}, "right": {"type": "string"}}}},
        ]
        if self.profile == "safe":
            tools = [tool for tool in tools if tool["name"] not in {
                "engineering_run", "engineering_generate", "engineering_offline_run", "engineering_version"}]
        for tool in tools:
            if tool["name"] in {"engineering_plan", "engineering_preflight", "engineering_run"}:
                tool["inputSchema"]["properties"]["parameters"] = {"type": "object", "description": "Explicit engineering parameters forwarded to software steps"}
        tools.append({"name": "engineering_workflow", "description": "Inspect a saved multi-step software plan or resume remaining operations with the original request IDs",
                      "inputSchema": {"type": "object", "required": ["action"], "properties": {
                          "action": {"type": "string", "enum": ["list", "status"] + (["resume", "repair"] if self.profile == "full" else [])},
                          "workflow_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                          "project_dir": {"type": "string"}}}})
        job_actions = ["list", "receipts", "status", "logs", "events"]
        if self.profile == "full":
            job_actions += ["cancel", "release", "collect"]
        tools.append({"name": "engineering_software_job", "description": "Reconnect to an existing software job, inspect logs, or collect results without starting another solve",
                      "inputSchema": {"type": "object", "required": ["tool", "action"], "properties": {
                          "tool": {"type": "string", "enum": ["catia", "ansys", "adams", "vi-grade"]},
                          "action": {"type": "string", "enum": job_actions}, "request_id": {"type": "string"},
                          "project_dir": {"type": "string"}, "limit": {"type": "integer"}, "cursor": {"type": "integer"},
                          "offset": {"type": "integer"}, "after": {"type": "integer"},
                          "stream": {"type": "string", "enum": ["stdout", "stderr"]},
                          "confirmed_stopped": {"type": "boolean", "description": "Only true after a human confirms the vendor operation is stopped"},
                          "reason": {"type": "string"}}}})
        for operation in self.operations.list():
            if self.profile == "safe" and operation["safety"] in {"write", "execute"}:
                continue
            input_schema = {
                "type": "object",
                "properties": operation.get("parameters_schema", {}),
                "required": operation.get("required_parameters", []),
                "x-engineering-schema-version": operation.get("version", "1.0"),
                "x-operation-schema-version": operation.get("version", "1.0"),
                "x-supported-operation-schema-versions": [operation.get("version", "1.0"),
                                                             *operation.get("compatible_versions", [])],
                "x-output-schema": operation.get("output_schema", {}),
                "x-output-kinds": operation.get("output_kinds", []),
                "x-side-effects": operation.get("side_effects", []),
                "x-safety": operation.get("safety", "safe"),
            }
            input_schema["properties"]["operation_schema_version"] = {
                "type": "string",
                "description": "Optional operation contract version; rejected when unsupported",
            }
            if operation.get("parameter_alternatives"):
                input_schema["allOf"] = [{"anyOf": [{"required": [key]} for key in group]}
                                         for group in operation["parameter_alternatives"]]
            tools.append({"name": "operation." + operation["name"], "description": operation["description"], "inputSchema": input_schema})
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
            {"uri": f"engineering://dependencies?root={root}", "name": "Project dependency graph", "mimeType": "application/json"},
        ]
        path = params.get("path")
        if isinstance(path, str) and path:
            resources.extend([
                {"uri": f"engineering://cad?root={root}&path={path}", "name": "CAD document context", "mimeType": "application/json"},
                {"uri": f"engineering://asset?root={root}&path={path}", "name": "Project asset preview and image", "mimeType": "image/svg+xml"},
            ])
        watch_id = params.get("watch_id")
        if isinstance(watch_id, str) and watch_id:
            resources.append({"uri": f"engineering://watch?root={root}&watch_id={watch_id}", "name": "External file watch status", "mimeType": "application/json"})
        return resources

    @staticmethod
    def _resource_templates() -> list[dict[str, Any]]:
        return [
            {"uriTemplate": "engineering://project?root={root}", "name": "Canonical project object", "mimeType": "application/json"},
            {"uriTemplate": "engineering://versions?root={root}", "name": "Project versions and timeline", "mimeType": "application/json"},
            {"uriTemplate": "engineering://dependencies?root={root}", "name": "Project dependency graph", "mimeType": "application/json"},
            {"uriTemplate": "engineering://cad?root={root}&path={path}", "name": "CAD document context", "mimeType": "application/json"},
            {"uriTemplate": "engineering://asset?root={root}&path={path}", "name": "Project asset preview and image", "mimeType": "image/svg+xml"},
            {"uriTemplate": "engineering://watch?root={root}&watch_id={watch_id}", "name": "External file watch status", "mimeType": "application/json"},
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
        elif parsed.netloc == "dependencies":
            value = self.agent.execute_operation("project.dependency_scan", {"project_dir": root})
        elif parsed.netloc == "watch":
            watch_id = query.get("watch_id", [""])[0]
            if not watch_id:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "watch_id is required")
            value = self.agent.execute_operation("project.watch_status", {"project_dir": root, "watch_id": watch_id})
        elif parsed.netloc == "cad":
            path = query.get("path", [""])[0]
            if not path:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "CAD resource path is required")
            root_path = Path(root).expanduser().resolve()
            candidate = (Path(path).expanduser() if Path(path).is_absolute() else root_path / path).resolve()
            if candidate != root_path and root_path not in candidate.parents:
                raise ProtocolError(ErrorCode.PATH_OUTSIDE_ROOT, "CAD resource path is outside the project root", details={"root": str(root_path), "path": str(candidate)})
            document = CadDocument.open(candidate)
            value = {"document": document.to_dict()}
        elif parsed.netloc == "asset":
            path = query.get("path", [""])[0]
            if not path:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "asset path is required")
            view = asset_view(root, path)
            serialized = json.dumps({"asset": view.get("asset", {}), "preview_note": view.get("preview_note"),
                                     "cad_history": view.get("cad_history"), "schemaVersion": self.schema_version},
                                    ensure_ascii=False, allow_nan=False)
            svg = str(view.get("svg", ""))
            image = base64.b64encode(svg.encode("utf-8")).decode("ascii")
            return [
                {"uri": uri, "mimeType": "application/json", "text": serialized,
                 "textHash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(), "schemaVersion": self.schema_version},
                {"uri": uri + "#image", "mimeType": "image/svg+xml", "blob": image,
                 "schemaVersion": self.schema_version},
            ]
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
        if self.profile == "safe":
            mutating = name in {"engineering_run", "engineering_generate", "engineering_offline_run", "engineering_version"}
            if name.startswith("operation."):
                mutating = self.operations.get(name[len("operation."):]).safety in {"write", "execute"}
            if mutating:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "MCP_PROFILE_READ_ONLY: use the full profile for writes or execution")
        requested_schema = arguments.get("schema_version") or arguments.get("schemaVersion")
        if requested_schema and requested_schema not in {self.schema_version, "1.0"}:
            raise ProtocolError(ErrorCode.SCHEMA_VERSION_UNSUPPORTED, f"unsupported schema version: {requested_schema}")
        operation_arguments = arguments
        if name.startswith("operation."):
            operation_name = name[len("operation."):]
            spec = self.operations.get(operation_name)
            requested_operation_schema = arguments.get("operation_schema_version") or arguments.get("operationSchemaVersion")
            supported = {spec.version, *spec.compatible_versions}
            if requested_operation_schema and requested_operation_schema not in supported:
                raise ProtocolError(ErrorCode.SCHEMA_VERSION_UNSUPPORTED,
                                    f"unsupported operation schema version: {requested_operation_schema}",
                                    details={"operation": operation_name,
                                             "requested": requested_operation_schema,
                                             "supported": sorted(supported)})
            if "operation_schema_version" in arguments or "operationSchemaVersion" in arguments:
                operation_arguments = dict(arguments)
                operation_arguments.pop("operation_schema_version", None)
                operation_arguments.pop("operationSchemaVersion", None)
        if name == "engineering_workflow":
            action = arguments.get("action", "list")
            if self.profile == "safe" and action not in {"list", "status"}:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "Safe profile permits workflow observation only")
            return self.agent.workflow(action, arguments.get("workflow_id"), project_dir=arguments.get("project_dir", "."))
        if name == "engineering_software_job":
            action = arguments.get("action", "status")
            if self.profile == "safe" and action not in {"list", "receipts", "status", "logs", "events"}:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "Safe profile permits job observation only")
            return self.agent.software_job(arguments.get("tool", ""), action, arguments.get("request_id"),
                                          project_dir=arguments.get("project_dir", "."),
                                          **{k: v for k, v in arguments.items() if k in {"limit", "cursor", "offset", "after", "stream", "confirmed_stopped", "reason"}})
        if name == "engineering_plan":
            plan = self.agent.plan(arguments.get("prompt", ""), project_dir=arguments.get("project_dir", "."), parameters=arguments.get("parameters"))
            return {"plan": plan.to_dict(), "validation": validate_plan(plan)}
        if name == "engineering_preflight":
            return self.agent.preflight(arguments.get("prompt", ""), project_dir=arguments.get("project_dir", "."),
                                        parameters=arguments.get("parameters"), probe=bool(arguments.get("probe", False)))
        if name == "engineering_run":
            return self.agent.run(arguments.get("prompt", ""), project_dir=arguments.get("project_dir", "."), parameters=arguments.get("parameters"), dry_run=arguments.get("dry_run", True)).to_dict()
        if name == "engineering_generate":
            return self.agent.generate(arguments.get("project_dir", "."), arguments.get("model_spec") or {})
        if name == "engineering_offline_run":
            return self.agent.run_offline_vehicle_chain(arguments.get("project_dir", "."), arguments.get("model_spec") or {}, arguments.get("rules"))
        if name == "engineering_doctor":
            return {"tools": self.agent.doctor()}
        if name == "engineering_operations":
            return {"operations": self.operations.list()}
        if name == "engineering_ingest":
            return self.agent.ingest(arguments["path"], arguments.get("kind"))
        if name == "engineering_project":
            return self.agent.inspect_project(arguments.get("project_dir", "."))
        if name == "engineering_asset_view":
            root = arguments.get("root", ".")
            relative = arguments.get("path")
            if not isinstance(relative, str) or not relative:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "asset path is required")
            view = asset_view(root, relative)
            summary = {"asset": view.get("asset", {}), "preview_note": view.get("preview_note"),
                       "cad_history": view.get("cad_history"), "schemaVersion": self.schema_version}
            serialized = json.dumps(summary, ensure_ascii=False, allow_nan=False)
            content = [{"type": "text", "text": serialized}]
            if arguments.get("include_image", True):
                svg = str(view.get("svg", ""))
                max_bytes = int(arguments.get("max_image_bytes", 4_000_000))
                if max_bytes < 1024 or max_bytes > 8_000_000:
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "max_image_bytes must be between 1024 and 8000000")
                encoded = svg.encode("utf-8")
                if len(encoded) > max_bytes:
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "asset preview exceeds max_image_bytes", details={"bytes": len(encoded), "max_image_bytes": max_bytes})
                content.append({"type": "image", "data": base64.b64encode(encoded).decode("ascii"), "mimeType": "image/svg+xml"})
            return {"_mcp_content": content, **summary}
        if name == "engineering_inspect_image":
            result = self.agent.inspect_image(arguments.get("root", "."), arguments.get("path", ""),
                                              arguments.get("region"), int(arguments.get("max_size", 1024)),
                                              int(arguments.get("rotate", 0)))
            image_data = result.pop("image_base64")
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, allow_nan=False)},
                       {"type": "image", "data": image_data, "mimeType": "image/png"}]
            return {"_mcp_content": content, **result}
        if name == "engineering_measure_image":
            result = self.agent.measure_image(arguments.get("root", "."), arguments.get("path", ""),
                                              region=arguments.get("region"), target_color=arguments.get("target_color"),
                                              tolerance=int(arguments.get("tolerance", 16)),
                                              alpha_threshold=int(arguments.get("alpha_threshold", 1)),
                                              max_size=int(arguments.get("max_size", 1024)),
                                              overlay=bool(arguments.get("overlay", True)),
                                              coordinate_system=arguments.get("coordinate_system"))
            image_data = result.pop("image_base64")
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, allow_nan=False)},
                       {"type": "image", "data": image_data, "mimeType": "image/png"}]
            return {"_mcp_content": content, **result}
        if name == "engineering_version":
            return self.agent.create_version(arguments["project_dir"], arguments["label"], arguments["paths"], arguments.get("parent"))
        if name == "engineering_diff":
            return self.agent.diff_versions(arguments["project_dir"], arguments["left"], arguments["right"])
        if name.startswith("operation."):
            return self.agent.execute_operation(name[len("operation."):], operation_arguments)
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
        if hasattr(server, "drain_notifications"):
            for notification in server.drain_notifications():
                sys.stdout.write(json.dumps(notification, ensure_ascii=False) + "\n")
        sys.stdout.flush()
