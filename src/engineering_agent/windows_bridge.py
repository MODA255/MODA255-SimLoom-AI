from __future__ import annotations

import json
import platform
import shutil
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .worker_jobs import JobManager, TERMINAL
from .windows_control import WindowsAutomation
from .worker_protocol import SCHEMA, MAX_HTTP_BYTES, WorkerError, failure


@dataclass
class BridgeConfig:
    root_dir: str = "."
    host: str = "127.0.0.1"
    port: int = 8099
    token: str = ""
    timeout_seconds: int = 1800
    commands: dict[str, list[str]] = field(default_factory=dict)
    com: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeConfig":
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})


class WindowsBridge:
    def __init__(self, config: BridgeConfig | dict[str, Any]):
        self.config = config if isinstance(config, BridgeConfig) else BridgeConfig.from_dict(config)
        self.root = Path(self.config.root_dir).resolve()
        self.com = WindowsAutomation()
        self.jobs = JobManager(self.root, self.config.commands, self.config.timeout_seconds)

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

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise WorkerError("INVALID_WORKER_REQUEST")
        operation = payload.get("operation", "")
        if payload.get("schema_version") != SCHEMA:
            raise WorkerError("WORKER_SCHEMA_UNSUPPORTED")
        if operation == "health":
            return self.health()
        if operation == "run":
            return self.run(payload)
        if operation.startswith("job."):
            return self.jobs.handle(payload)
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
            return self._send(404, failure(WorkerError("NOT_FOUND")))

        def log_message(self, *_args):
            return

    return ThreadingHTTPServer((cfg.host, cfg.port), Handler)


def serve_bridge(config: BridgeConfig | dict[str, Any]):
    server = create_bridge_server(config)
    print(f"Engineering software worker: http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
