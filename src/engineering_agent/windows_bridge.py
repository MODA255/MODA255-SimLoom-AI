from __future__ import annotations

import json
import platform
import shlex
import subprocess
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .windows_control import WindowsAutomation


@dataclass
class BridgeConfig:
    root_dir: str = "."
    host: str = "127.0.0.1"
    port: int = 8099
    token: str = ""
    timeout_seconds: int = 1800
    commands: dict[str, list[str] | str] = field(default_factory=dict)
    com: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeConfig":
        return cls(**{key: value for key, value in data.items() if key in {"root_dir", "host", "port", "token", "timeout_seconds", "commands", "com"}})


class WindowsBridge:
    def __init__(self, config: BridgeConfig | dict[str, Any]):
        self.config = config if isinstance(config, BridgeConfig) else BridgeConfig.from_dict(config)
        self.root = Path(self.config.root_dir).resolve()
        self.com = WindowsAutomation()

    def health(self) -> dict[str, Any]:
        return {"ok": True, "platform": platform.system(), "root_dir": str(self.root), "commands": sorted(self.config.commands), "com": sorted(self.config.com)}

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation = payload.get("operation", "")
        if operation == "health":
            return self.health()
        if operation == "run":
            return self.run(payload)
        if operation == "com.invoke":
            return self.invoke_com(payload)
        raise ValueError(f"UNKNOWN_BRIDGE_OPERATION:{operation}")

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = f"{payload.get('tool', '')}.{payload.get('action', payload.get('operation_name', ''))}"
        command_template = self.config.commands.get(key) or self.config.commands.get(str(payload.get("tool", "")))
        if not command_template:
            raise ValueError(f"COMMAND_NOT_CONFIGURED:{key}")
        project_dir = self._safe_path(payload.get("project_dir", str(self.root)))
        script = payload.get("script", "")
        script_path = self._safe_path(script, project_dir) if script else None
        values = {"project_dir": str(project_dir), "script": str(script_path or ""), "tool": str(payload.get("tool", ""))}
        command = [part.format(**values) for part in command_template] if isinstance(command_template, list) else [part.format(**values) for part in shlex.split(command_template)]
        completed = subprocess.run(command, cwd=project_dir, capture_output=True, text=True, timeout=self.config.timeout_seconds, check=False, shell=False)
        return {"status": "completed" if completed.returncode == 0 else "failed", "returncode": completed.returncode, "command": command, "stdout": completed.stdout[-10000:], "stderr": completed.stderr[-10000:]}

    def invoke_com(self, payload: dict[str, Any]) -> dict[str, Any]:
        prog_id = str(payload.get("prog_id", ""))
        method = str(payload.get("method", ""))
        allow = self.config.com.get(prog_id, {})
        if not allow or method not in set(allow.get("methods", [])):
            raise PermissionError(f"COM_METHOD_NOT_ALLOWED:{prog_id}:{method}")
        result = self.com.invoke(prog_id, method, *(payload.get("args") or []))
        return {"status": "completed", "result": str(result)}

    def _safe_path(self, value: str, base: Path | None = None) -> Path:
        base = base or self.root
        path = (Path(value) if Path(value).is_absolute() else base / value).resolve()
        if path != self.root and self.root not in path.parents:
            raise PermissionError("BRIDGE_PATH_OUTSIDE_ROOT")
        return path


def serve_bridge(config: BridgeConfig | dict[str, Any]):
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

        def do_POST(self):
            if cfg.token and self.headers.get("X-Bridge-Token", "") != cfg.token:
                return self._send(401, {"error": "BRIDGE_UNAUTHORIZED"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                result = bridge.handle(json.loads(self.rfile.read(length) or b"{}"))
                self._send(200, result)
            except Exception as exc:
                self._send(400, {"error": str(exc)})

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, bridge.health())
            return self._send(404, {"error": "not found"})

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    print(f"Engineering Windows bridge: http://{cfg.host}:{cfg.port}")
    server.serve_forever()
