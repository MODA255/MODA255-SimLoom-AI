"""Generate client configuration for the stdio MCP server."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


CLIENTS = frozenset({"generic", "claude", "codex"})


def _server_name(value: str) -> str:
    name = str(value or "simloom-ai").strip()
    if not name:
        raise ValueError("MCP server name must not be empty")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError("MCP server name may contain only letters, numbers, '.', '_' and '-'")
    return name


def _command(config_path: str | Path, profile: str, python_executable: str | None) -> dict[str, Any]:
    if profile not in {"safe", "full"}:
        raise ValueError("MCP profile must be safe or full")
    path = Path(config_path).expanduser().resolve()
    executable = str(Path(python_executable).expanduser().resolve()) if python_executable else sys.executable
    return {
        "command": executable,
        "args": ["-m", "engineering_agent.cli", "--config", str(path), "mcp", "--profile", profile],
    }


def build_config(*, client: str = "generic", name: str = "simloom-ai",
                 config_path: str | Path = "configs/engineering-agent.example.json",
                 profile: str = "safe", python_executable: str | None = None) -> dict[str, Any]:
    """Return a JSON-compatible config for a supported MCP host."""
    if client not in CLIENTS:
        raise ValueError(f"unsupported MCP client: {client}")
    server_name = _server_name(name)
    command = _command(config_path, profile, python_executable)
    if client in {"generic", "claude"}:
        return {"mcpServers": {server_name: command}}
    return {"mcp_servers": {server_name.replace("-", "_"): {**command, "startup_timeout_sec": 20}}}


def render_config(*, client: str = "generic", name: str = "simloom-ai",
                  config_path: str | Path = "configs/engineering-agent.example.json",
                  profile: str = "safe", python_executable: str | None = None) -> str:
    """Render JSON for generic/Claude hosts or TOML for Codex."""
    value = build_config(client=client, name=name, config_path=config_path,
                         profile=profile, python_executable=python_executable)
    if client != "codex":
        return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    server_name, settings = next(iter(value["mcp_servers"].items()))
    def quote(text: str) -> str:
        return json.dumps(str(text), ensure_ascii=False)
    args = ", ".join(quote(item) for item in settings["args"])
    return (f"[mcp_servers.{server_name}]\n"
            f"command = {quote(settings['command'])}\n"
            f"args = [{args}]\n"
            f"startup_timeout_sec = {int(settings['startup_timeout_sec'])}\n")


def write_config(path: str | Path, **kwargs: Any) -> Path:
    """Render and atomically replace a client configuration file."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = render_config(**kwargs)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)
    return target

