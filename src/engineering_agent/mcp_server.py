from __future__ import annotations

import json
import sys
import base64
import hashlib
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from typing import Any

from .agent import EngineeringAgent
from .agentcad_compat import normalize_operation
from .cad_model import CadDocument
from .operations import OperationRegistry
from .protocol import ErrorCode, ProtocolError, failure, parse_json_line
from .validation import validate_plan
from .versioning import VersionStore
from .viewer import asset_view
from .software_preflight import PREFLIGHT_OUTPUT_SCHEMA
from .action_profile import build_action_profile, build_worker_config_template


@dataclass(frozen=True)
class MCPPolicy:
    """Discovery and execution policy for MCP tools.

    Empty allow lists mean "no restriction".  Deny rules always win.  Rules
    accept exact names or shell-style ``*`` patterns.  Operation rules may
    use either ``cad.entity_move`` or ``operation.cad.entity_move``.
    """

    allow_tools: tuple[str, ...] = ()
    deny_tools: tuple[str, ...] = ()
    allow_operations: tuple[str, ...] = ()
    deny_operations: tuple[str, ...] = ()

    @staticmethod
    def _patterns(value: Any, key: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"mcp.{key} must be a list of strings")
        patterns = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"mcp.{key} must contain non-empty strings")
            pattern = item.strip()
            if pattern not in patterns:
                patterns.append(pattern)
        return tuple(patterns)

    @classmethod
    def from_dict(cls, value: Any) -> "MCPPolicy":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("mcp configuration must be an object")
        return cls(
            allow_tools=cls._patterns(value.get("allow_tools"), "allow_tools"),
            deny_tools=cls._patterns(value.get("deny_tools"), "deny_tools"),
            allow_operations=cls._patterns(value.get("allow_operations"), "allow_operations"),
            deny_operations=cls._patterns(value.get("deny_operations"), "deny_operations"),
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "allow_tools": list(self.allow_tools),
            "deny_tools": list(self.deny_tools),
            "allow_operations": list(self.allow_operations),
            "deny_operations": list(self.deny_operations),
        }

    @staticmethod
    def _matches(patterns: tuple[str, ...], names: tuple[str, ...]) -> str | None:
        for pattern in patterns:
            if any(fnmatchcase(name, pattern) for name in names):
                return pattern
        return None

    def denial(self, tool_name: str) -> dict[str, Any] | None:
        tool_match = self._matches(self.deny_tools, (tool_name,))
        if tool_match:
            return {"scope": "tool", "rule": tool_match, "reason": "deny_tools"}
        if self.allow_tools and not self._matches(self.allow_tools, (tool_name,)):
            return {"scope": "tool", "rule": None, "reason": "allow_tools"}
        if not tool_name.startswith("operation."):
            return None
        operation_name = tool_name[len("operation."):]
        names = (tool_name, operation_name)
        operation_match = self._matches(self.deny_operations, names)
        if operation_match:
            return {"scope": "operation", "rule": operation_match, "reason": "deny_operations",
                    "operation": operation_name}
        if self.allow_operations and not self._matches(self.allow_operations, names):
            return {"scope": "operation", "rule": None, "reason": "allow_operations",
                    "operation": operation_name}
        return None

    def allows(self, tool_name: str) -> bool:
        return self.denial(tool_name) is None


class MCPServer:
    """Small stdio MCP-compatible tool surface for local agent clients.

    ``toolset='all'`` keeps the historical surface where every typed
    operation is discoverable as an MCP tool.  ``toolset='compact'`` exposes
    an operation index, on-demand schemas and one generic dispatcher so
    clients do not need to load hundreds of operation schemas up front.
    """

    SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26")
    DEFAULT_PROTOCOL_VERSION = "2025-03-26"

    def __init__(self, agent: EngineeringAgent, profile: str = "safe",
                 policy: dict[str, Any] | MCPPolicy | None = None,
                 toolset: str = "all"):
        self.agent = agent
        self.operations = OperationRegistry()
        self.profile = profile if profile in {"safe", "full"} else "safe"
        if toolset not in {"all", "compact"}:
            raise ValueError("MCP toolset must be all or compact")
        self.toolset = toolset
        configured_policy = policy if policy is not None else getattr(agent, "config", {}).get("mcp", {})
        self.policy = configured_policy if isinstance(configured_policy, MCPPolicy) else MCPPolicy.from_dict(configured_policy)
        self.schema_version = "engineering-agent/mcp/v1"
        self._subscriptions: set[str] = set()
        self._pending_notifications: list[dict[str, Any]] = []
        # Scope the active CAD document to this MCP session.  It is never
        # persisted as project state and disappears when the stdio server exits.
        self._cad_context: dict[str, str] | None = None

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
        operation_name = None
        operation_arguments = arguments
        if tool_name == "engineering_operation":
            operation_name = str(arguments.get("operation", ""))
            nested = arguments.get("arguments")
            operation_arguments = nested if isinstance(nested, dict) else {}
        elif tool_name.startswith("operation."):
            operation_name = tool_name[len("operation."):]
        mutating = (
            tool_name in {"engineering_generate", "engineering_offline_run", "engineering_version", "apply_operations"}
            or tool_name == "engineering_run" and not bool(arguments.get("dry_run", True))
            or tool_name == "engineering_workflow" and arguments.get("action") in {"resume", "repair"}
            or operation_name is not None and self.operations.get(operation_name).safety in {"write", "execute"}
        )
        if not mutating:
            return
        project_dir = str(operation_arguments.get("project_dir") or operation_arguments.get("root")
                          or arguments.get("project_dir") or arguments.get("root") or "")
        for uri in sorted(self._subscriptions):
            query = parse_qs(urlparse(uri).query)
            subscribed_root = query.get("root", [""])[0]
            if project_dir and subscribed_root and Path(project_dir).resolve() != Path(subscribed_root).resolve():
                continue
            self._pending_notifications.append({
                "jsonrpc": "2.0", "method": "notifications/resources/updated",
                "params": {"uri": uri, "schemaVersion": self.schema_version},
            })

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if method in {"notifications/initialized", "notifications/cancelled", "notifications/progress"}:
            # MCP notifications are one-way messages and must not produce a
            # JSON-RPC response on stdout.
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "initialize":
            params = request.get("params") or {}
            requested = params.get("protocolVersion")
            if requested and requested not in self.SUPPORTED_PROTOCOL_VERSIONS:
                return self._error(request_id, ProtocolError(ErrorCode.INVALID_ARGUMENTS, f"UNSUPPORTED_PROTOCOL_VERSION:{requested}"))
            selected = requested or self.DEFAULT_PROTOCOL_VERSION
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": selected, "serverInfo": {"name": "engineering-agent", "version": "0.4.0", "schemaVersion": self.schema_version, "supportedSchemaVersions": [self.schema_version]}, "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": True, "listChanged": False}, "prompts": {"listChanged": False}}, "profile": self.profile, "toolset": self.toolset, "policy": self.policy.to_dict()}}
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
            tool_name = str(params.get("name", ""))
            try:
                if not tool_name:
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "tool name is required")
                result = self.call(tool_name, params.get("arguments") or {})
                self._queue_resource_updates(tool_name, params.get("arguments") or {})
                structured = self._structured_content(tool_name, result)
                if isinstance(result, dict) and isinstance(result.get("_mcp_content"), list):
                    content = result["_mcp_content"]
                    return {"jsonrpc": "2.0", "id": request_id,
                            "result": {"content": content, "structuredContent": structured}}
                return {"jsonrpc": "2.0", "id": request_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                                   "structuredContent": structured}}
            except Exception as exc:
                # A discovered tool that rejects or cannot complete its input
                # reports a tool execution error in the MCP result.  JSON-RPC
                # errors remain reserved for transport/protocol failures and
                # unknown tools, which lets clients distinguish a fixable
                # engineering failure from a broken MCP request.
                if not self._is_unknown_tool_error(exc):
                    return self._tool_error(request_id, tool_name, exc)
                return self._error(request_id, exc)
        return self._error(request_id, ProtocolError(ErrorCode.INVALID_REQUEST, f"METHOD_NOT_FOUND:{method}"))

    @staticmethod
    def _is_unknown_tool_error(exc: Exception) -> bool:
        text = str(exc)
        return text.startswith("UNKNOWN_TOOL:") or text.startswith("UNKNOWN_OPERATION:")

    def _error_payload(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, ProtocolError):
            envelope = failure(exc)
        elif isinstance(exc, FileNotFoundError):
            envelope = failure(ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, str(exc), details={"path": str(exc)}))
        elif getattr(exc, "code", None):
            envelope = failure(ProtocolError(str(exc.code), str(exc), details=getattr(exc, "details", {}) or {}))
        elif isinstance(exc, ValueError) and getattr(exc, "code", None):
            envelope = failure(ProtocolError(str(exc.code), str(exc)))
        elif isinstance(exc, ValueError):
            text = str(exc)
            if text.startswith("UNKNOWN_OPERATION:"):
                code = ErrorCode.UNKNOWN_OPERATION
            elif text.startswith(("CAD_UNIT_", "CAD_QUANTITY_")):
                # CAD quantity parsing emits stable domain prefixes so an
                # external agent can repair a unit or dimension without
                # scraping the human-readable message.
                code = text.split(":", 1)[0]
            else:
                code = ErrorCode.INVALID_ARGUMENTS
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
        return error

    def _tool_error(self, request_id: Any, tool_name: str, exc: Exception) -> dict[str, Any]:
        error = self._error_payload(exc)
        structured = {
            "schemaVersion": self.schema_version,
            "tool": tool_name,
            "ok": False,
            "error": error,
        }
        text = json.dumps({"error": error}, ensure_ascii=False)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": text}],
                "structuredContent": structured,
            },
        }

    def _error(self, request_id: Any, exc: Exception) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": self._error_payload(exc)}

    def _tools(self, *, include_profile: bool = True, apply_policy: bool = True,
               toolset: str | None = None) -> list[dict[str, Any]]:
        toolset = toolset or self.toolset
        if toolset not in {"all", "compact"}:
            raise ValueError("MCP toolset must be all or compact")
        tools = [
            {"name": "engineering_plan", "description": "Create a validated four-tool execution plan", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "project_dir": {"type": "string"}}}},
            {"name": "engineering_preflight", "description": "Check planned vendor action bindings, scripts, commands and output contracts without starting CATIA, Ansys, Adams or VI-grade", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "project_dir": {"type": "string"}, "parameters": {"type": "object"}, "probe": {"type": "boolean", "default": False}}}},
            {"name": "engineering_run", "description": "Run a plan in dry-run or execution mode; optionally evaluate a bounded sequence of explicitly configured repair strategies", "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "project_dir": {"type": "string"}, "parameters": {"type": "object"}, "dry_run": {"type": "boolean"}, "auto_repair": {"type": "boolean", "description": "After a stable completed workflow, evaluate declared repair strategies"}, "repair_budget": {"type": "integer", "minimum": 1, "maximum": 8, "description": "Maximum number of independent repair child workflows"}}}},
            {"name": "engineering_generate", "description": "Generate vendor scripts and scenario files", "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string"}, "model_spec": {"type": "object"}}}},
            {"name": "engineering_offline_run", "description": "Run the deterministic CATIA-to-VI-grade fixture chain without commercial software", "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string"}, "model_spec": {"type": "object"}, "rules": {"type": "array"}}}},
            {"name": "engineering_doctor", "description": "Inspect configured vendor backends and COM", "inputSchema": {"type": "object", "properties": {"probe": {"type": "boolean", "default": True, "description": "Probe configured remote worker health and capabilities"}}}},
            {"name": "engineering_capabilities", "description": "Return the offline, rehearsal, software connection, execution and real-machine readiness matrix", "inputSchema": {"type": "object", "properties": {"probe": {"type": "boolean", "default": True, "description": "Probe configured remote worker health and capabilities"}}}},
            {"name": "engineering_operations", "description": "List typed operations and schemas", "inputSchema": {"type": "object"}},
            {"name": "engineering_ingest", "description": "Read STEP/CSV/JSON into the canonical object model", "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}, "kind": {"type": "string"}}}},
            {"name": "engineering_project", "description": "Inspect all supported artifacts in a project", "inputSchema": {"type": "object", "properties": {"project_dir": {"type": "string"}}}},
            {"name": "engineering_asset_view", "description": "Read a project asset and return structured metadata plus an SVG image for agent inspection", "inputSchema": {"type": "object", "required": ["root", "path"], "properties": {"root": {"type": "string", "description": "Project root directory"}, "path": {"type": "string", "description": "Project-relative asset path"}, "include_image": {"type": "boolean", "default": True}, "max_image_bytes": {"type": "integer", "minimum": 1024, "maximum": 8000000, "default": 4000000}}}},
            {"name": "engineering_inspect_image", "description": "Read-only crop, magnify and rotate a PNG/JPEG/WebP drawing preview with source pixel coordinates", "inputSchema": {"type": "object", "required": ["root", "path"], "properties": {"root": {"type": "string"}, "path": {"type": "string"}, "region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}, "max_size": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1024}, "rotate": {"type": "integer", "enum": [0, 90, 180, 270], "default": 0}}}},
            {"name": "engineering_measure_image", "description": "Read-only source-pixel color measurement with optional pixel-to-drawing coordinate mapping and annotated PNG overlay", "inputSchema": {"type": "object", "required": ["root", "path", "target_color"], "properties": {"root": {"type": "string"}, "path": {"type": "string"}, "region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}, "target_color": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 255}, "minItems": 3, "maxItems": 3}, "tolerance": {"type": "integer", "minimum": 0, "maximum": 255, "default": 16}, "alpha_threshold": {"type": "integer", "minimum": 0, "maximum": 255, "default": 1}, "max_size": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1024}, "overlay": {"type": "boolean", "default": True}, "coordinate_system": {"type": "object", "description": "Full-image drawing coordinate extents; y_axis is up or down", "required": ["x_min", "x_max", "y_min", "y_max", "y_axis"], "properties": {"x_min": {"type": "number"}, "x_max": {"type": "number"}, "y_min": {"type": "number"}, "y_max": {"type": "number"}, "y_axis": {"type": "string", "enum": ["up", "down"]}}}}}},
            {"name": "engineering_version", "description": "Create an immutable project snapshot", "inputSchema": {"type": "object", "required": ["project_dir", "label", "paths"], "properties": {"project_dir": {"type": "string"}, "label": {"type": "string"}, "paths": {"type": "array"}}}},
            {"name": "engineering_diff", "description": "Compare two immutable snapshots", "inputSchema": {"type": "object", "required": ["project_dir", "left", "right"], "properties": {"project_dir": {"type": "string"}, "left": {"type": "string"}, "right": {"type": "string"}}}},
            {"name": "apply_operations", "description": "Apply an AgentCAD-compatible atomic operation batch to the active drawing", "inputSchema": {"type": "object", "required": ["operations"], "properties": {"document": {"type": "string"}, "path": {"type": "string"}, "project_dir": {"type": "string"}, "output_file": {"type": "string"}, "operations": {"type": "array", "items": {"type": "object"}}, "requirements": {"type": "object"}, "auto_repair": {"type": "boolean", "default": True}}}},
            {"name": "read_drawing", "description": "Read the active drawing into the canonical document model", "inputSchema": {"type": "object", "properties": {"document": {"type": "string"}, "path": {"type": "string"}, "project_dir": {"type": "string"}}}},
            {"name": "measure_entities", "description": "Measure selected drawing entities with analytic geometry", "inputSchema": {"type": "object", "properties": {"document": {"type": "string"}, "path": {"type": "string"}, "project_dir": {"type": "string"}, "target": {}, "entity_ids": {"type": "array"}, "selection_set": {"type": "string"}, "entity_type": {"type": "string"}, "layer": {"type": "string"}, "window": {"type": "array"}, "strict": {"type": "boolean"}}}},
            {"name": "verify_drawing", "description": "Run independent drawing validation and engineering requirements", "inputSchema": {"type": "object", "properties": {"document": {"type": "string"}, "path": {"type": "string"}, "project_dir": {"type": "string"}, "requirements": {"type": "object"}}}},
            {"name": "get_diff", "description": "Compare the active drawing with another drawing", "inputSchema": {"type": "object", "required": ["right_file"], "properties": {"document": {"type": "string"}, "path": {"type": "string"}, "project_dir": {"type": "string"}, "right_file": {"type": "string"}}}},
            {"name": "create_drawing", "description": "Create a new blank native DXF drawing", "inputSchema": {"type": "object", "required": ["output"], "properties": {"output": {"type": "string"}, "path": {"type": "string"}, "project_dir": {"type": "string"}, "units": {"type": "string", "default": "mm"}, "template": {"type": "string", "enum": ["blank", "iso"]}, "annotation_scale": {"type": "number"}, "overwrite": {"type": "boolean"}}}},
            {"name": "inspect_entities", "description": "Inspect entities using AgentCAD-compatible filters and pagination", "inputSchema": {"type": "object", "properties": {"document": {"type": "string"}, "path": {"type": "string"}, "project_dir": {"type": "string"}, "filter": {"type": "object"}, "ids": {"type": "array"}, "entity_type": {"type": "string"}, "layer": {"type": "string"}, "window": {"type": "array"}, "limit": {"type": "integer"}, "offset": {"type": "integer"}}}},
            {"name": "apply_constraints", "description": "Check or sequentially enforce geometric constraints", "inputSchema": {"type": "object", "properties": {"document": {"type": "string"}, "path": {"type": "string"}, "project_dir": {"type": "string"}, "output_file": {"type": "string"}, "constraints": {"type": "array"}, "mode": {"type": "string", "enum": ["check", "enforce"], "default": "check"}, "tolerance": {"type": "number"}}}},
        ]
        if include_profile and self.profile == "safe":
            tools = [tool for tool in tools if tool["name"] not in {
                "engineering_run", "engineering_generate", "engineering_offline_run", "engineering_version", "apply_operations", "create_drawing"}]
        for tool in tools:
            if tool["name"] in {"engineering_plan", "engineering_preflight", "engineering_run"}:
                tool["inputSchema"]["properties"]["parameters"] = {"type": "object", "description": "Explicit engineering parameters forwarded to software steps"}
        tools.append({"name": "engineering_workflow", "description": "Inspect a saved multi-step software plan or resume remaining operations with the original request IDs",
                      "inputSchema": {"type": "object", "required": ["action"], "properties": {
                          "action": {"type": "string", "enum": ["list", "status"] + (["resume", "repair"] if self.profile == "full" else [])},
                          "workflow_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                          "project_dir": {"type": "string"},
                          "repair_budget": {"type": "integer", "minimum": 1, "maximum": 8,
                                             "description": "repair 动作最多连续尝试的独立子工程链数量"}}}})
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
        evidence_actions = ["list"] + (["record"] if self.profile == "full" else [])
        tools.append({"name": "engineering_worker_evidence",
                      "description": "Read Windows worker smoke-test evidence or record a completed real-software acceptance run",
                      "inputSchema": {"type": "object", "required": ["tool", "action"], "properties": {
                          "tool": {"type": "string", "enum": ["catia", "ansys", "adams", "vi-grade"]},
                          "action": {"type": "string", "enum": evidence_actions},
                          "project_dir": {"type": "string"}, "request_id": {"type": "string"},
                          "vendor_action": {"type": "string"}, "operator": {"type": "string"},
                          "software_version": {"type": "string"}, "license_status": {"type": "string", "enum": ["available", "borrowed"]},
                          "smoke_test_id": {"type": "string"}, "notes": {"type": "string"},
                          "limit": {"type": "integer"}}}})
        tools.append({"name": "engineering_cad_context",
                      "description": "Inspect or clear the active CAD document context for this MCP session",
                      "inputSchema": {"type": "object", "required": ["action"], "properties": {
                          "action": {"type": "string", "enum": ["inspect", "clear"]}}}})
        for operation in self.operations.list():
            if include_profile and self.profile == "safe" and operation["safety"] in {"write", "execute"}:
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
            if operation.get("compatibility"):
                # Preserve AgentCAD alias metadata in the MCP contract.  A
                # client can therefore choose the flat public name while
                # still understanding the canonical SimLoom operation and
                # parameter spellings before it makes a call.
                input_schema["x-compatibility"] = operation["compatibility"]
            input_schema["properties"]["schema_version"] = {
                "type": "string",
                "description": "Optional MCP schema version; rejected when unsupported",
            }
            input_schema["properties"]["operation_schema_version"] = {
                "type": "string",
                "description": "Optional operation contract version; rejected when unsupported",
            }
            input_schema["properties"]["operationSchemaVersion"] = {
                "type": "string",
                "description": "Camel-case alias for operation_schema_version",
            }
            if operation.get("parameter_alternatives"):
                input_schema["allOf"] = [{"anyOf": [{"required": [key]} for key in group]}
                                         for group in operation["parameter_alternatives"]]
            output_properties = operation.get("output_schema") or {}
            output_required = operation.get("output_required") or []
            output_contract = {"type": "object", "properties": output_properties}
            if output_required:
                output_contract["required"] = output_required
            tools.append({"name": "operation." + operation["name"], "description": operation["description"],
                          "inputSchema": input_schema,
                          "outputSchema": output_contract})
        if toolset == "compact":
            # Keep the operation index and generic dispatcher small enough for
            # MCP clients that load every advertised tool schema into context.
            tools = [tool for tool in tools if not tool["name"].startswith("operation.")]
            tools.extend([
                {"name": "engineering_operation_schema",
                 "description": "Fetch full schemas for selected typed operations on demand",
                 "inputSchema": {"type": "object", "properties": {
                     "operations": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                                    "description": "Operation names, with or without the operation. prefix"},
                     "category": {"type": "string", "description": "Filter by canonical category such as cad, brep or ansys"},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                     "cursor": {"type": "string"}}}},
                {"name": "engineering_operation",
                 "description": "Call one typed operation by name using its discovered schema",
                 "inputSchema": {"type": "object", "required": ["operation"], "properties": {
                     "operation": {"type": "string"},
                     "arguments": {"type": "object", "description": "Arguments matching engineering_operation_schema"}}}},
            ])
        # MCP clients should be able to discover a typed output contract using
        # the standard field.  Keep the schemas intentionally permissive for
        # built-in tools because their result shape is versioned in
        # structuredContent and can include vendor-specific details.
        builtin_outputs = {
            "engineering_plan": {"type": "object", "properties": {"plan": {"type": "object"}, "validation": {"type": "object"}}},
            "engineering_preflight": PREFLIGHT_OUTPUT_SCHEMA,
            "engineering_run": {"type": "object"},
            "engineering_generate": {"type": "object"},
            "engineering_offline_run": {"type": "object"},
            "engineering_doctor": {"type": "object"},
            "engineering_capabilities": {"type": "object", "properties": {
                "schema_version": {"type": "string", "const": "simloom-ai/capabilities/v1"},
                "capability_matrix": {"type": "object", "properties": {
                    "schema_version": {"type": "string", "const": "simloom-ai/capability-matrix/v1"},
                    "probe": {"type": "boolean"}, "matrix_digest": {"type": "string"},
                    "actions": {"type": "array", "items": {"type": "object"}},
                    "summary": {"type": "object"}, "tools": {"type": "object"},
                }},
                "modes": {"type": "array", "items": {"type": "object"}},
                "components": {"type": "object"}, "tools": {"type": "object"},
            }},
            "engineering_operations": {"type": "object", "properties": {"operations": {"type": "array"}}},
            "engineering_ingest": {"type": "object"},
            "engineering_project": {"type": "object"},
            "engineering_asset_view": {"type": "object", "properties": {"asset": {"type": "object"}, "preview_note": {"type": ["string", "null"]}}},
            "engineering_inspect_image": {"type": "object"},
            "engineering_measure_image": {"type": "object"},
            "engineering_version": {"type": "object"},
            "engineering_diff": {"type": "object"},
            "engineering_workflow": {"type": "object"},
            "engineering_software_job": {"type": "object"},
            "engineering_worker_evidence": {"type": "object"},
            "engineering_cad_context": {"type": "object", "properties": {"context": {"type": ["object", "null"]}}},
            "engineering_operation_schema": {"type": "object", "properties": {"schemas": {"type": "array"}}},
            "engineering_operation": {"type": "object"},
        }
        for tool in tools:
            tool.setdefault("outputSchema", builtin_outputs.get(tool["name"], {"type": "object"}))
            schema = tool.setdefault("inputSchema", {"type": "object", "properties": {}})
            schema.setdefault("properties", {})
            schema["properties"].setdefault("schema_version", {
                "type": "string",
                "description": "Optional MCP schema version; rejected when unsupported",
            })
        if apply_policy:
            tools = [tool for tool in tools if self.policy.allows(tool["name"])]
        return tools

    def _known_tool_names(self) -> set[str]:
        """Names that are valid regardless of profile or allow-list filtering."""
        names = {tool["name"] for tool in self._tools(include_profile=False, apply_policy=False, toolset="all")}
        names.update({"engineering_operation_schema", "engineering_operation"})
        return names

    def _visible_operation_names(self) -> set[str]:
        return {tool["name"][len("operation."):]
                for tool in self._tools(toolset="all")
                if tool["name"].startswith("operation.")}

    def _check_access(self, tool_name: str) -> None:
        if tool_name not in self._known_tool_names():
            raise ValueError(f"UNKNOWN_TOOL:{tool_name}")
        denial = self.policy.denial(tool_name)
        if denial:
            details = {"tool": tool_name, **denial}
            raise ProtocolError(ErrorCode.MCP_POLICY_DENIED,
                                f"MCP policy denied tool: {tool_name}", details=details)

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
            {"uri": "engineering://capabilities", "name": "Software capability matrix", "mimeType": "application/json"},
            {"uri": "engineering://operations", "name": "Operation schemas", "mimeType": "application/json"},
            {"uri": f"engineering://action-profile?root={root}", "name": "Software action bindings", "mimeType": "application/json"},
            {"uri": "engineering://worker-config", "name": "Windows worker configuration template", "mimeType": "application/json"},
            {"uri": f"engineering://workflows?root={root}", "name": "Saved software workflows", "mimeType": "application/json"},
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
        operation_name = params.get("operation") or params.get("name")
        if isinstance(operation_name, str) and operation_name:
            resources.append({"uri": f"engineering://operation?name={quote(operation_name, safe='._-')}",
                              "name": f"Operation schema: {operation_name}", "mimeType": "application/json"})
        workflow_id = params.get("workflow_id")
        if isinstance(workflow_id, str) and workflow_id:
            resources.append({"uri": f"engineering://workflow?root={root}&workflow_id={workflow_id}",
                              "name": "Saved workflow status and failure evidence", "mimeType": "application/json"})
        watch_id = params.get("watch_id")
        if isinstance(watch_id, str) and watch_id:
            resources.append({"uri": f"engineering://watch?root={root}&watch_id={watch_id}", "name": "External file watch status", "mimeType": "application/json"})
        run_id = params.get("run_id")
        if isinstance(run_id, str) and run_id:
            resources.append({"uri": f"engineering://cad-events?root={root}&run_id={run_id}", "name": "CAD transaction events", "mimeType": "application/json"})
        if params.get("include_runs", True):
            resources.append({"uri": f"engineering://cad-runs?root={root}", "name": "CAD transaction runs", "mimeType": "application/json"})
        return resources

    @staticmethod
    def _resource_templates() -> list[dict[str, Any]]:
        return [
            {"uriTemplate": "engineering://capabilities", "name": "Software capability matrix", "mimeType": "application/json"},
            {"uriTemplate": "engineering://action-profile?root={root}", "name": "Software action bindings", "mimeType": "application/json"},
            {"uriTemplate": "engineering://worker-config", "name": "Windows worker configuration template", "mimeType": "application/json"},
            {"uriTemplate": "engineering://workflows?root={root}&limit={limit}&cursor={cursor}", "name": "Saved software workflows", "mimeType": "application/json"},
            {"uriTemplate": "engineering://workflow?root={root}&workflow_id={workflow_id}&detail={detail}", "name": "Saved workflow status and failure evidence", "mimeType": "application/json"},
            {"uriTemplate": "engineering://operations?detail={detail}&category={category}&limit={limit}&cursor={cursor}", "name": "Filtered operation schemas", "mimeType": "application/json"},
            {"uriTemplate": "engineering://operation?name={name}", "name": "Single operation schema", "mimeType": "application/json"},
            {"uriTemplate": "engineering://project?root={root}", "name": "Canonical project object", "mimeType": "application/json"},
            {"uriTemplate": "engineering://versions?root={root}", "name": "Project versions and timeline", "mimeType": "application/json"},
            {"uriTemplate": "engineering://dependencies?root={root}", "name": "Project dependency graph", "mimeType": "application/json"},
            {"uriTemplate": "engineering://cad?root={root}&path={path}", "name": "CAD document context", "mimeType": "application/json"},
            {"uriTemplate": "engineering://cad-events?root={root}&run_id={run_id}&after={after}", "name": "CAD transaction events", "mimeType": "application/json"},
            {"uriTemplate": "engineering://cad-runs?root={root}&offset={offset}", "name": "CAD transaction runs", "mimeType": "application/json"},
            {"uriTemplate": "engineering://asset?root={root}&path={path}", "name": "Project asset preview and image", "mimeType": "image/svg+xml"},
            {"uriTemplate": "engineering://watch?root={root}&watch_id={watch_id}", "name": "External file watch status", "mimeType": "application/json"},
        ]

    def _read_resource(self, uri: str) -> dict[str, Any]:
        parsed = urlparse(uri)
        if parsed.scheme != "engineering":
            raise ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, "unsupported resource URI")
        query = parse_qs(parsed.query)
        root = query.get("root", ["."])[0]
        if parsed.netloc == "capabilities":
            probe = query.get("probe", ["true"])[0].strip().lower() not in {"0", "false", "no", "off"}
            value = self.agent.capabilities(probe=probe)
        elif parsed.netloc == "operations":
            detail = query.get("detail", ["true" if self.toolset == "all" else "false"])[0].strip().lower() not in {"0", "false", "no", "off"}
            try:
                operation_limit = int(query.get("limit", ["500"])[0])
            except ValueError as exc:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "operation resource limit must be an integer") from exc
            arguments: dict[str, Any] = {"detail": detail, "limit": operation_limit}
            if query.get("cursor", [""])[0]:
                arguments["cursor"] = query["cursor"][0]
            if query.get("category", [""])[0]:
                arguments["category"] = query["category"][0]
            if query.get("operation", [""])[0]:
                arguments["operations"] = query["operation"]
            value = self._operation_discovery(arguments)
        elif parsed.netloc == "operation":
            name = query.get("name", [""])[0].strip()
            if not name:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "operation name is required")
            discovered = self._operation_discovery({"operations": [name], "detail": True, "limit": 1})
            if not discovered.get("operations"):
                raise ProtocolError(ErrorCode.RESOURCE_NOT_FOUND, f"operation schema not found: {name}")
            value = {"operation": discovered["operations"][0], "schemaVersion": self.schema_version}
        elif parsed.netloc == "action-profile":
            value = build_action_profile(self.agent.config, project_root=root)
        elif parsed.netloc == "worker-config":
            value = build_worker_config_template(self.agent.config)
        elif parsed.netloc == "workflows":
            try:
                limit = int(query.get("limit", ["50"])[0])
            except ValueError as exc:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "workflow resource limit must be an integer") from exc
            workflow_result = self.agent.workflow("list", project_dir=root)
            workflows = workflow_result.get("workflows") if isinstance(workflow_result, dict) else None
            if not isinstance(workflows, list):
                raise ProtocolError(ErrorCode.INTERNAL_ERROR, "workflow list did not return an array")
            value = self._paged(workflows, {"limit": limit, "cursor": query.get("cursor", [""])[0]}, "workflows")
        elif parsed.netloc == "workflow":
            workflow_id = query.get("workflow_id", [""])[0].strip()
            if not re.fullmatch(r"[0-9a-f]{32}", workflow_id):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "workflow_id must be a 32-character lowercase hexadecimal id")
            detail = query.get("detail", ["summary"])[0].strip().lower()
            if detail not in {"summary", "failures"}:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "workflow detail must be summary or failures")
            summary = self.agent.workflow("status", workflow_id, project_dir=root)
            if not isinstance(summary, dict):
                raise ProtocolError(ErrorCode.INTERNAL_ERROR, "workflow status did not return an object")
            if detail == "failures":
                failures = []
                for step in summary.get("steps", []) if isinstance(summary.get("steps"), list) else []:
                    if not isinstance(step, dict):
                        continue
                    if step.get("failure") is None and step.get("failure_evidence") is None:
                        continue
                    failures.append({key: step[key] for key in (
                        "id", "tool", "action", "status", "request_id", "failure", "failure_evidence", "lifecycle"
                    ) if key in step})
                value = {"schemaVersion": self.schema_version, "workflow_id": workflow_id,
                         "status": summary.get("status"), "failures": failures[:256],
                         "repair_decision": summary.get("repair_decision"),
                         "repair_loop": summary.get("repair_loop")}
            else:
                value = summary
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
        elif parsed.netloc == "cad-events":
            run_id = query.get("run_id", [""])[0]
            if not run_id:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "run_id is required")
            try:
                after = int(query.get("after", ["0"])[0])
                limit = int(query.get("limit", ["100"])[0])
            except ValueError as exc:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "after and limit must be integers") from exc
            value = self.agent.execute_operation("cad.events", {
                "project_dir": root, "run_id": run_id, "after": after, "limit": limit})
        elif parsed.netloc == "cad-runs":
            try:
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["50"])[0])
            except ValueError as exc:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "offset and limit must be integers") from exc
            value = self.agent.execute_operation("cad.runs", {
                "project_dir": root, "offset": offset, "limit": limit,
                "state": query.get("state", [None])[0]})
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

    def _operation_tools_for_discovery(self) -> list[dict[str, Any]]:
        """Return operation schemas after profile and policy filtering."""
        operations = []
        for operation in self.operations.list():
            if self.profile == "safe" and operation["safety"] in {"write", "execute"}:
                continue
            if not self.policy.allows("operation." + operation["name"]):
                continue
            operations.append(operation)
        return operations

    @staticmethod
    def _operation_category(tool: dict[str, Any]) -> str:
        compatibility = tool.get("compatibility") or {}
        alias_for = compatibility.get("alias_for")
        name = str(alias_for or tool.get("name", ""))
        return name.split(".", 1)[0] if "." in name else "cad"

    def _operation_discovery(self, arguments: dict[str, Any]) -> dict[str, Any]:
        tools = self._operation_tools_for_discovery()
        category = arguments.get("category")
        if category:
            category = str(category).strip().lower()
            tools = [tool for tool in tools if self._operation_category(tool) == category]
        requested = arguments.get("operations", arguments.get("operation"))
        if requested is not None:
            if isinstance(requested, str):
                requested = [requested]
            if not isinstance(requested, list) or not all(isinstance(item, str) and item.strip() for item in requested):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "operations must be a string or a list of strings")
            wanted = {item.strip().removeprefix("operation.") for item in requested}
            tools = [tool for tool in tools if tool["name"] in wanted]
        detail_default = self.toolset == "all"
        detail = bool(arguments.get("detail", detail_default))
        if not detail:
            compact: list[dict[str, Any]] = []
            for tool in tools:
                item = {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "tool": tool.get("tool"),
                    "safety": tool.get("safety", "safe"),
                    "version": tool.get("version", "1.0"),
                    "required_parameters": tool.get("required_parameters", []),
                    "output_kinds": tool.get("output_kinds", []),
                }
                compatibility = tool.get("compatibility")
                if compatibility:
                    item["compatibility"] = compatibility
                compact.append(item)
            tools = compact
        params = dict(arguments)
        params.setdefault("limit", 500 if self.toolset == "all" else 50)
        return self._paged(tools, params, "operations")

    def _operation_schemas(self, arguments: dict[str, Any]) -> dict[str, Any]:
        params = dict(arguments)
        params["detail"] = True
        result = self._operation_discovery(params)
        result["schemas"] = result.pop("operations")
        return result

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check_access(name)
        if self.profile == "safe":
            mutating = name in {"engineering_run", "engineering_generate", "engineering_offline_run", "engineering_version", "apply_operations"}
            if name.startswith("operation."):
                mutating = self.operations.get(name[len("operation."):]).safety in {"write", "execute"}
            if mutating:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "MCP_PROFILE_READ_ONLY: use the full profile for writes or execution")
        requested_schema = arguments.get("schema_version") or arguments.get("schemaVersion")
        if requested_schema and requested_schema not in {self.schema_version, "1.0"}:
            raise ProtocolError(ErrorCode.SCHEMA_VERSION_UNSUPPORTED, f"unsupported schema version: {requested_schema}")
        if name == "engineering_operations":
            return self._operation_discovery(arguments)
        if name == "engineering_operation_schema":
            return self._operation_schemas(arguments)
        if name == "engineering_operation":
            operation = arguments.get("operation")
            operation_arguments = arguments.get("arguments") or {}
            if not isinstance(operation, str) or not operation.strip():
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "operation is required")
            if not isinstance(operation_arguments, dict):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "arguments must be an object")
            target = operation if operation.startswith("operation.") else "operation." + operation
            return self.call(target, operation_arguments)
        if name in {"apply_operations", "read_drawing", "measure_entities", "verify_drawing", "get_diff", "create_drawing", "inspect_entities", "apply_constraints"}:
            mapped = dict(arguments)
            if "input_file" not in mapped:
                for key in ("document", "path"):
                    if mapped.get(key):
                        mapped["input_file"] = mapped[key]
                        break
            mapped.pop("document", None)
            mapped.pop("path", None)
            if name == "apply_constraints":
                mode = str(mapped.pop("mode", "check")).lower()
                if mode not in {"check", "enforce"}:
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "mode must be check or enforce")
                target = "operation.cad.constraints_enforce" if mode == "enforce" else "operation.cad.constraints_check"
                return self.call(target, mapped)
            if name == "create_drawing":
                if "output_file" not in mapped:
                    mapped["output_file"] = mapped.get("output") or mapped.get("path")
                mapped.pop("output", None)
                mapped.pop("path", None)
                return self.call("operation.cad.document_create", mapped)
            if name == "inspect_entities":
                if "entity_ids" not in mapped and mapped.get("ids") is not None:
                    mapped["entity_ids"] = mapped.pop("ids")
                return self.call("operation.cad.query", mapped)
            if name == "apply_operations":
                operations = mapped.get("operations")
                if not isinstance(operations, list) or not operations:
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "operations must be a non-empty array")
                normalized = []
                for item in operations:
                    if not isinstance(item, dict):
                        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "each operation must be an object")
                    item = dict(item)
                    operation_name = item.pop("operation", item.pop("op", None))
                    if not isinstance(operation_name, str) or not operation_name.strip():
                        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "each operation requires operation or op")
                    canonical, params, _ = normalize_operation(operation_name, item)
                    normalized.append({"operation": canonical, **params})
                mapped["operations"] = normalized
                return self.call("operation.cad.batch_apply", mapped)
            target = {"read_drawing": "operation.cad.document_inspect",
                      "measure_entities": "operation.measure_entities",
                      "verify_drawing": "operation.cad.audit",
                      "get_diff": "operation.cad.document_diff"}[name]
            if name == "get_diff" and "right_file" not in mapped:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "right_file is required")
            return self.call(target, mapped)
        operation_arguments = arguments
        operation_spec = None
        operation_name = None
        if name.startswith("operation."):
            operation_name = name[len("operation."):]
            operation_spec = self.operations.get(operation_name)
            requested_operation_schema = arguments.get("operation_schema_version") or arguments.get("operationSchemaVersion")
            supported = {operation_spec.version, *operation_spec.compatible_versions}
            if requested_operation_schema and requested_operation_schema not in supported:
                raise ProtocolError(ErrorCode.SCHEMA_VERSION_UNSUPPORTED,
                                    f"unsupported operation schema version: {requested_operation_schema}",
                                    details={"operation": operation_name,
                                             "requested": requested_operation_schema,
                                             "supported": sorted(supported)})
            if ("operation_schema_version" in arguments or "operationSchemaVersion" in arguments
                    or "document" in arguments or self._cad_context):
                operation_arguments = dict(arguments)
                operation_arguments.pop("operation_schema_version", None)
                operation_arguments.pop("operationSchemaVersion", None)
            # AgentCAD clients call the source file ``document``.  Preserve
            # the explicit value, then fill omitted file arguments from the
            # session context established by cad.document_open.
            if "document" in operation_arguments and "input_file" not in operation_arguments:
                operation_arguments["input_file"] = operation_arguments["document"]
            if operation_spec.name != "cad.document_open" and self._cad_context:
                operation_arguments.setdefault("project_dir", self._cad_context["project_dir"])
                operation_arguments.setdefault("input_file", self._cad_context["input_file"])
                if operation_spec.safety in {"write", "execute"}:
                    operation_arguments.setdefault(
                        "output_file",
                        operation_arguments.get("input_file", self._cad_context["output_file"]),
                    )
            if (operation_spec.safety in {"write", "execute"}
                    and "output_file" not in operation_arguments
                    and operation_arguments.get("input_file")):
                # In-place edits are the natural AgentCAD default.  The
                # canonical engine still validates and journals the write.
                operation_arguments["output_file"] = operation_arguments["input_file"]
        if name == "engineering_workflow":
            action = arguments.get("action", "list")
            if self.profile == "safe" and action not in {"list", "status"}:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "Safe profile permits workflow observation only")
            return self.agent.workflow(action, arguments.get("workflow_id"), project_dir=arguments.get("project_dir", "."),
                                       repair_budget=arguments.get("repair_budget"))
        if name == "engineering_software_job":
            action = arguments.get("action", "status")
            if self.profile == "safe" and action not in {"list", "receipts", "status", "logs", "events"}:
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "Safe profile permits job observation only")
            return self.agent.software_job(arguments.get("tool", ""), action, arguments.get("request_id"),
                                          project_dir=arguments.get("project_dir", "."),
                                          **{k: v for k, v in arguments.items() if k in {"limit", "cursor", "offset", "after", "stream", "confirmed_stopped", "reason"}})
        if name == "engineering_worker_evidence":
            action = arguments.get("action", "list")
            if self.profile == "safe" and action != "list":
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "Safe profile permits worker evidence observation only")
            metadata = {key: value for key, value in arguments.items()
                         if key in {"vendor_action", "operator", "software_version", "license_status",
                                    "smoke_test_id", "notes", "limit"}}
            return self.agent.worker_evidence(arguments.get("tool", ""), action,
                                              request_id=arguments.get("request_id"),
                                              project_dir=arguments.get("project_dir", "."), **metadata)
        if name == "engineering_cad_context":
            action = str(arguments.get("action", "inspect"))
            if action == "inspect":
                return {"context": dict(self._cad_context) if self._cad_context else None,
                        "active": self._cad_context is not None,
                        "schemaVersion": self.schema_version}
            if action == "clear":
                previous = dict(self._cad_context) if self._cad_context else None
                self._cad_context = None
                return {"context": None, "previous": previous, "schemaVersion": self.schema_version}
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "CAD context action must be inspect or clear")
        if name == "engineering_plan":
            plan = self.agent.plan(arguments.get("prompt", ""), project_dir=arguments.get("project_dir", "."), parameters=arguments.get("parameters"))
            return {"plan": plan.to_dict(), "validation": validate_plan(plan)}
        if name == "engineering_preflight":
            return self.agent.preflight(arguments.get("prompt", ""), project_dir=arguments.get("project_dir", "."),
                                        parameters=arguments.get("parameters"), probe=bool(arguments.get("probe", False)))
        if name == "engineering_run":
            return self.agent.run(arguments.get("prompt", ""), project_dir=arguments.get("project_dir", "."),
                                  parameters=arguments.get("parameters"), dry_run=arguments.get("dry_run", True),
                                  auto_repair=bool(arguments.get("auto_repair", False)),
                                  repair_budget=arguments.get("repair_budget")).to_dict()
        if name == "engineering_generate":
            return self.agent.generate(arguments.get("project_dir", "."), arguments.get("model_spec") or {})
        if name == "engineering_offline_run":
            return self.agent.run_offline_vehicle_chain(arguments.get("project_dir", "."), arguments.get("model_spec") or {}, arguments.get("rules"))
        if name == "engineering_doctor":
            return {"tools": self.agent.doctor(probe=bool(arguments.get("probe", True)))}
        if name == "engineering_capabilities":
            return self.agent.capabilities(probe=bool(arguments.get("probe", True)))
        if name == "engineering_operations":
            visible = self._visible_operation_names()
            return {"operations": [item for item in self.operations.list()
                                    if item["name"] in visible]}
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
            result = self.agent.execute_operation(name[len("operation."):], operation_arguments)
            if operation_spec and operation_spec.name == "cad.document_open":
                input_file = operation_arguments.get("input_file")
                if input_file:
                    project_dir = Path(str(operation_arguments.get("project_dir") or ".")).expanduser().resolve()
                    output_file = str(operation_arguments.get("output_file") or input_file)
                    self._cad_context = {
                        "project_dir": str(project_dir),
                        "input_file": str(input_file),
                        "output_file": output_file,
                        "document_id": str((result.get("document") or {}).get("document_id", "")),
                    }
                    if isinstance(result, dict):
                        result = dict(result)
                        result["cad_context"] = dict(self._cad_context)
            elif self._cad_context and isinstance(result, dict):
                document = result.get("document")
                if isinstance(document, dict) and document.get("document_id"):
                    self._cad_context["document_id"] = str(document["document_id"])
            return result
        raise ValueError(f"UNKNOWN_TOOL:{name}")


def serve_stdio(agent: EngineeringAgent, profile: str = "safe", toolset: str = "all"):
    server = MCPServer(agent, profile=profile, toolset=toolset)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            response = server.handle(parse_json_line(line))
        except ProtocolError as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": failure(exc)["error"]}
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        if hasattr(server, "drain_notifications"):
            for notification in server.drain_notifications():
                sys.stdout.write(json.dumps(notification, ensure_ascii=False) + "\n")
        sys.stdout.flush()
