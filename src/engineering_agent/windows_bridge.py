from __future__ import annotations

import json
import os
import platform
import shutil
import socketserver
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .worker_jobs import JobManager, TERMINAL
from .artifact_transfer import ArtifactTransferStore
from .machine_evidence import MachineEvidenceStore, SCHEMA as EVIDENCE_SCHEMA
from .windows_control import WindowsAutomation
from .worker_protocol import SCHEMA, MAX_HTTP_BYTES, WorkerError, failure


DIAGNOSTICS_SCHEMA = "simloom-ai/worker-diagnostics/v1"


class _LocalThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server that never performs a reverse-DNS lookup on bind."""

    def server_bind(self):
        # http.server.HTTPServer.server_bind calls socket.getfqdn(). On some
        # hosted macOS runners that lookup can block for tens of seconds even
        # for 127.0.0.1, preventing a restarted worker from becoming ready.
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


@dataclass
class BridgeConfig:
    root_dir: str = "."
    host: str = "127.0.0.1"
    port: int = 8099
    token: str = ""
    token_env: str = ""
    timeout_seconds: int = 1800
    commands: dict[str, list[str]] = field(default_factory=dict)
    com: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeConfig":
        values = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        token_env = values.get("token_env")
        if isinstance(token_env, str):
            token_env = token_env.strip()
            values["token_env"] = token_env
            if not values.get("token") and token_env:
                values["token"] = os.environ.get(token_env, "")
        return cls(**values)


class WindowsBridge:
    def __init__(self, config: BridgeConfig | dict[str, Any]):
        self.config = config if isinstance(config, BridgeConfig) else BridgeConfig.from_dict(config)
        self.root = Path(self.config.root_dir).resolve()
        self.com = WindowsAutomation()
        self.jobs = JobManager(self.root, self.config.commands, self.config.timeout_seconds)
        self.transfers = ArtifactTransferStore(self.root)
        # Do not open the worker database while the HTTP listener is starting.
        # A bridge restart can happen while a supervisor is writing a job; the
        # first health/port response must remain available during that write.
        self._evidence = None

    @property
    def evidence(self) -> MachineEvidenceStore:
        if self._evidence is None:
            self._evidence = MachineEvidenceStore(self.root, self.jobs.store)
        return self._evidence

    def health(self) -> dict[str, Any]:
        checks = {}
        for key, command in self.config.commands.items():
            valid = (isinstance(command, list) and bool(command) and all(isinstance(p, str) and p for p in command)
                     and any("{script}" in p for p in command))
            resolved = shutil.which(command[0]) if valid else None
            checks[key] = {"executable_found": bool(resolved), "resolved": resolved, "valid_argv": valid,
                           "real_machine_verified": False}
        return {"schema_version": SCHEMA, "ok": True, "platform": platform.system(),
                "commands": sorted(self.config.commands), "command_checks": checks,
                "com": sorted(self.config.com), "vendor_validation": "not_run",
                "features": {"durable_jobs": True, "job_logs": True, "software_serialization": True}}

    def capabilities(self) -> dict[str, Any]:
        """Return a discovery-safe action matrix for a remote Mac controller.

        Presence of a command or COM allow-list is configuration evidence only;
        it never marks a licensed vendor operation as real-machine verified.
        """
        health = self.health()
        diagnostics = self.diagnostics()
        verified = self.evidence.verified_actions()
        tools: dict[str, dict[str, Any]] = {}
        for tool in ("catia", "ansys", "adams", "vi-grade"):
            actions = sorted(key.split(".", 1)[1] for key in self.config.commands
                             if key.startswith(tool + "."))
            checks = {key.split(".", 1)[1]: value for key, value in health["command_checks"].items()
                      if key.startswith(tool + ".")}
            token = tool.replace("-", "_")
            verified_actions = {key.split(".", 1)[1]: value for key, value in verified.items()
                                if key.startswith(tool + ".")}
            tools[tool] = {"actions": actions, "action_checks": checks,
                           "com_configured": any(token in str(key).lower() for key in self.config.com),
                           "verified_actions": sorted(verified_actions),
                           "evidence": verified_actions,
                           "real_machine_verified": bool(verified_actions)}
        return {"schema_version": SCHEMA, "capabilities_schema": "simloom-ai/worker-capabilities/v1",
                "platform": health["platform"], "tools": tools,
                "features": health["features"], "vendor_validation": "not_run",
                "evidence_schema": EVIDENCE_SCHEMA,
                "diagnostics": diagnostics,
                "real_machine_verified": bool(verified)}

    def diagnostics(self) -> dict[str, Any]:
        """Return read-only worker configuration and evidence diagnostics.

        The report deliberately separates configuration readiness from vendor
        acceptance. A resolved command or configured COM ProgID is useful
        evidence for setup, but it never becomes ``real_machine_verified``.
        """
        health = self.health()
        records = self.evidence.list(limit=500)["records"]
        command_tools: dict[str, dict[str, Any]] = {}
        for key, check in health["command_checks"].items():
            if "." in key:
                tool, action = key.split(".", 1)
            else:
                tool, action = "unknown", key
            item = command_tools.setdefault(tool, {"actions": [], "ready_actions": [], "missing_actions": []})
            action_record = {
                "name": action,
                "status": "ready" if check.get("valid_argv") and check.get("executable_found") else "missing",
                "valid_argv": bool(check.get("valid_argv")),
                "executable_found": bool(check.get("executable_found")),
                "resolved": check.get("resolved"),
            }
            item["actions"].append(action_record)
            (item["ready_actions"] if action_record["status"] == "ready" else item["missing_actions"]).append(action)
        for item in command_tools.values():
            item["actions"].sort(key=lambda record: record["name"])
            item["ready_actions"].sort()
            item["missing_actions"].sort()
        evidence_by_tool: dict[str, dict[str, int]] = {}
        license_states: set[str] = set()
        valid_count = 0
        for record in records:
            tool = str(record.get("tool", "unknown"))
            summary = evidence_by_tool.setdefault(tool, {"records": 0, "valid": 0, "invalid": 0})
            summary["records"] += 1
            if record.get("valid") is True:
                summary["valid"] += 1
                valid_count += 1
            else:
                summary["invalid"] += 1
            status = record.get("license_status")
            if isinstance(status, str) and status:
                license_states.add(status)
        root_exists = self.root.is_dir()
        root_writable = root_exists and bool(os.access(self.root, os.W_OK))
        warnings = []
        issues = []
        if platform.system().lower() != "windows":
            issues.append("WINDOWS_HOST_REQUIRED")
        if not root_exists:
            issues.append("WORKER_ROOT_MISSING")
        elif not root_writable:
            issues.append("WORKER_ROOT_NOT_WRITABLE")
        if not self.config.token:
            warnings.append("BRIDGE_TOKEN_NOT_CONFIGURED")
        command_count = sum(len(item["actions"]) for item in command_tools.values())
        ready_count = sum(len(item["ready_actions"]) for item in command_tools.values())
        authentication = {"token_configured": bool(self.config.token), "header": "X-Bridge-Token"}
        if self.config.token_env:
            authentication["token_env"] = self.config.token_env
        return {
            "schema_version": DIAGNOSTICS_SCHEMA,
            "worker_protocol": SCHEMA,
            "platform": health["platform"],
            "root": {"path": str(self.root), "exists": root_exists, "writable": root_writable},
            "authentication": authentication,
            "commands": command_tools,
            "com": {"configured_prog_ids": sorted(self.config.com), "dispatch_probe": "not_run",
                    "real_machine_verified": False},
            "features": health["features"],
            "evidence": {"schema_version": EVIDENCE_SCHEMA, "records": len(records),
                         "valid": valid_count, "invalid": len(records) - valid_count,
                         "by_tool": evidence_by_tool, "license_states": sorted(license_states)},
            "readiness": {"worker_health": bool(health.get("ok")), "configured_actions": command_count,
                          "ready_actions": ready_count, "execution_ready": ready_count > 0,
                          "real_machine_verified": valid_count > 0},
            "issues": sorted(issues),
            "warnings": sorted(warnings),
        }

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise WorkerError("INVALID_WORKER_REQUEST")
        operation = payload.get("operation", "")
        if payload.get("schema_version") != SCHEMA:
            raise WorkerError("WORKER_SCHEMA_UNSUPPORTED")
        if operation == "health":
            return self.health()
        if operation == "capabilities":
            return self.capabilities()
        if operation == "diagnostics":
            return self.diagnostics()
        if operation == "run":
            return self.run(payload)
        if operation.startswith("job."):
            return self.jobs.handle(payload)
        if operation == "artifact.upload_begin":
            return {**self.transfers.begin(payload.get("transfer_id"), payload.get("files")), "schema_version": SCHEMA}
        if operation == "artifact.upload_status":
            return {**self.transfers.status(payload.get("transfer_id")), "schema_version": SCHEMA}
        if operation == "artifact.upload_chunk":
            return {**self.transfers.put_chunk(payload.get("transfer_id"), payload.get("path"), payload.get("offset"),
                                               payload.get("content_base64"), payload.get("chunk_sha256")), "schema_version": SCHEMA}
        if operation == "artifact.upload_complete":
            return {**self.transfers.complete(payload.get("transfer_id")), "schema_version": SCHEMA}
        if operation == "artifact.download_chunk":
            path = self.jobs.store.output_path(payload.get("request_id"), payload.get("path"))
            result = self.transfers.read_chunk(path, payload.get("offset", 0), payload.get("limit", 1024 * 1024))
            return {"schema_version": SCHEMA, "request_id": payload.get("request_id"),
                    "path": payload.get("path"), **result}
        if operation == "evidence.record":
            return self.evidence.record(payload)
        if operation == "evidence.list":
            return self.evidence.list(tool=payload.get("tool"), action=payload.get("action"),
                                      limit=payload.get("limit", 100))
        if operation == "com.invoke":
            return self.invoke_com(payload)
        raise WorkerError("UNKNOWN_BRIDGE_OPERATION", str(operation))

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self.jobs.submit(payload)
        while state["status"] not in TERMINAL:
            time.sleep(0.2)
            state = self.jobs.store.status(payload["request_id"])
        return self.jobs.store.result(payload["request_id"])

    def invoke_com(self, payload: dict[str, Any]) -> dict[str, Any]:
        prog_id, method = str(payload.get("prog_id", "")), str(payload.get("method", ""))
        allow = self.config.com.get(prog_id, {})
        if not allow or method not in set(allow.get("methods", [])):
            raise WorkerError("COM_METHOD_NOT_ALLOWED", f"{prog_id}:{method}")
        result = self.com.invoke(prog_id, method, *(payload.get("args") or []))
        return {"schema_version": SCHEMA, "status": "completed", "result": str(result),
                "verification": "com_invocation_only", "vendor_validation": "not_run"}


def create_bridge_server(config: BridgeConfig | dict[str, Any]) -> ThreadingHTTPServer:
    bridge = WindowsBridge(config)
    cfg = bridge.config

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any]):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            return not cfg.token or self.headers.get("X-Bridge-Token", "") == cfg.token

        def do_POST(self):
            if not self._authorized():
                return self._send(401, failure(WorkerError("BRIDGE_UNAUTHORIZED")))
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise WorkerError("UNSUPPORTED_TRANSFER_ENCODING")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_HTTP_BYTES:
                    return self._send(413, failure(WorkerError("INVALID_REQUEST_SIZE")))
                self.connection.settimeout(30)
                content = self.rfile.read(length)
                if len(content) != length:
                    raise WorkerError("INCOMPLETE_REQUEST")
                result = bridge.handle(json.loads(content))
                self._send(200, result)
            except (BrokenPipeError, ConnectionResetError):
                return  # A disconnected caller does not cancel a durable job.
            except Exception as exc:
                self._send(400, failure(exc))

        def do_GET(self):
            if not self._authorized():
                return self._send(401, failure(WorkerError("BRIDGE_UNAUTHORIZED")))
            if self.path == "/health":
                return self._send(200, bridge.health())
            if self.path == "/capabilities":
                return self._send(200, bridge.capabilities())
            if self.path == "/diagnostics":
                return self._send(200, bridge.diagnostics())
            if self.path == "/evidence":
                return self._send(200, bridge.evidence.list())
            return self._send(404, failure(WorkerError("NOT_FOUND")))

        def log_message(self, *_args):
            return

    return _LocalThreadingHTTPServer((cfg.host, cfg.port), Handler)


def serve_bridge(config: BridgeConfig | dict[str, Any]):
    server = create_bridge_server(config)
    print(f"Engineering software worker: http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
