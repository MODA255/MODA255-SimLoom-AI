"""Persisted, non-secret connection overrides for the local workbench.

The main JSON configuration remains the source of truth for action bindings,
scripts and security policy.  This small runtime profile stores only values an
engineer commonly changes while connecting a Mac control plane to a Windows
worker: bridge URL, executable hint, token environment variable and worker
listen settings.  Secrets themselves never enter the profile.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "simloom-ai/runtime-config/v1"
TOOLS = ("catia", "ansys", "adams", "vi-grade")
TOOL_FIELDS = ("bridge_url", "executable", "bridge_token_env")
TOKEN_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MAX_TEXT = 512


class RuntimeConfigError(ValueError):
    """Raised when a workbench connection override is unsafe or malformed."""


def _text(value: Any, field: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise RuntimeConfigError(f"{field} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise RuntimeConfigError(f"{field} must not be empty")
    if len(value) > MAX_TEXT:
        raise RuntimeConfigError(f"{field} is too long")
    if any(ord(char) < 32 for char in value):
        raise RuntimeConfigError(f"{field} contains control characters")
    return value


def normalize(value: Any) -> dict[str, Any]:
    """Validate and normalize a JSON-compatible runtime override object."""
    if not isinstance(value, dict):
        raise RuntimeConfigError("runtime configuration must be an object")
    unknown = set(value) - {"schema_version", "tools", "windows_bridge"}
    if unknown:
        raise RuntimeConfigError("unsupported runtime configuration field: " + sorted(unknown)[0])
    result: dict[str, Any] = {"schema_version": SCHEMA, "tools": {}, "windows_bridge": {}}
    tools = value.get("tools", {})
    if not isinstance(tools, dict):
        raise RuntimeConfigError("tools must be an object")
    for name, raw in tools.items():
        if name not in TOOLS:
            raise RuntimeConfigError("unsupported tool: " + str(name))
        if not isinstance(raw, dict):
            raise RuntimeConfigError(f"tools.{name} must be an object")
        unknown_fields = set(raw) - set(TOOL_FIELDS)
        if unknown_fields:
            raise RuntimeConfigError(f"unsupported tools.{name} field: " + sorted(unknown_fields)[0])
        item: dict[str, str] = {}
        for field in TOOL_FIELDS:
            if field not in raw:
                continue
            item[field] = _text(raw[field], f"tools.{name}.{field}")
        if "bridge_url" in item and item["bridge_url"]:
            if not (item["bridge_url"].startswith("http://") or item["bridge_url"].startswith("https://")):
                raise RuntimeConfigError(f"tools.{name}.bridge_url must start with http:// or https://")
        if "bridge_token_env" in item and item["bridge_token_env"] and not TOKEN_ENV.fullmatch(item["bridge_token_env"]):
            raise RuntimeConfigError(f"tools.{name}.bridge_token_env is not a valid environment variable")
        result["tools"][name] = item
    bridge = value.get("windows_bridge", {})
    if not isinstance(bridge, dict):
        raise RuntimeConfigError("windows_bridge must be an object")
    unknown_bridge = set(bridge) - {"host", "port", "root_dir", "timeout_seconds"}
    if unknown_bridge:
        raise RuntimeConfigError("unsupported windows_bridge field: " + sorted(unknown_bridge)[0])
    normalized_bridge: dict[str, Any] = {}
    if "host" in bridge:
        normalized_bridge["host"] = _text(bridge["host"], "windows_bridge.host", allow_empty=False)
    if "root_dir" in bridge:
        normalized_bridge["root_dir"] = _text(bridge["root_dir"], "windows_bridge.root_dir")
    if "port" in bridge:
        if type(bridge["port"]) is not int or not 1 <= bridge["port"] <= 65535:
            raise RuntimeConfigError("windows_bridge.port must be an integer from 1 to 65535")
        normalized_bridge["port"] = bridge["port"]
    if "timeout_seconds" in bridge:
        if type(bridge["timeout_seconds"]) not in {int, float} or not 1 <= float(bridge["timeout_seconds"]) <= 86400:
            raise RuntimeConfigError("windows_bridge.timeout_seconds must be between 1 and 86400")
        normalized_bridge["timeout_seconds"] = bridge["timeout_seconds"]
    result["windows_bridge"] = normalized_bridge
    return result


def merge(base: dict[str, Any] | None, override: dict[str, Any]) -> dict[str, Any]:
    """Return a deep merged config without mutating the caller's config."""
    normalized = normalize(override)
    merged = copy.deepcopy(base) if isinstance(base, dict) else {}
    merged.setdefault("tools", {})
    for name, fields in normalized["tools"].items():
        current = merged["tools"].setdefault(name, {})
        current.update(fields)
    if normalized["windows_bridge"]:
        merged.setdefault("windows_bridge", {}).update(normalized["windows_bridge"])
    return merged


def snapshot(config: dict[str, Any] | None) -> dict[str, Any]:
    """Extract only editable, non-secret fields from an agent config."""
    config = config if isinstance(config, dict) else {}
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    result = {"schema_version": SCHEMA, "tools": {}, "windows_bridge": {}}
    for name in TOOLS:
        item = tools.get(name) if isinstance(tools.get(name), dict) else {}
        result["tools"][name] = {field: item.get(field, "") for field in TOOL_FIELDS}
    bridge = config.get("windows_bridge") if isinstance(config.get("windows_bridge"), dict) else {}
    for field in ("host", "port", "root_dir", "timeout_seconds"):
        if field in bridge:
            result["windows_bridge"][field] = bridge[field]
    return result


class RuntimeConfigStore:
    """Atomic JSON persistence used by the local Web workbench."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    def read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": SCHEMA, "tools": {}, "windows_bridge": {}}
        try:
            return normalize(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, RuntimeConfigError) as exc:
            raise RuntimeConfigError("runtime configuration is invalid: " + str(exc)) from exc

    def write(self, value: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(normalized, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return normalized
