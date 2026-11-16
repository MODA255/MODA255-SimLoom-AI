"""Optional AutoCAD Core Console integration.

The controller can run on macOS or Linux, so this module deliberately keeps
all AutoCAD imports and process assumptions behind a small, testable boundary.
The backend is useful even before AutoCAD is installed: ``status`` and
``build_script`` expose deterministic capability information, while actual
conversion and native AUDIT fail closed with a structured, actionable error.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "simloom-ai/autocad/v1"
_EXECUTABLE_ENV = ("SIMLOOM_ACCORECONSOLE", "AGENTCAD_ACCORECONSOLE", "AUTOCAD_ACCORECONSOLE")


class AutoCADError(RuntimeError):
    """An expected AutoCAD backend failure with a stable machine code."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _config_value(config: dict[str, Any] | None, *keys: str) -> str:
    config = config or {}
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def locate_accoreconsole(config: dict[str, Any] | None = None) -> str | None:
    """Find an explicitly configured or PATH-provided Core Console binary."""
    config = config or {}
    configured = _config_value(config, "accoreconsole", "executable", "core_console")
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(os.environ.get(key, "") for key in _EXECUTABLE_ENV)
    candidates.extend(("accoreconsole.exe", "accoreconsole"))
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return str(Path(resolved).resolve())
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path.resolve())
    return None


def backend_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return capability evidence without launching AutoCAD."""
    system = platform.system()
    executable = locate_accoreconsole(config)
    windows = system.lower() == "windows"
    if not windows:
        reason = "AUTOCAD_REQUIRES_WINDOWS"
    elif not executable:
        reason = "AUTOCAD_CORE_CONSOLE_NOT_FOUND"
    else:
        reason = ""
    return {
        "schema_version": SCHEMA,
        "tool": "autocad",
        "platform": system,
        "windows": windows,
        "executable": executable,
        "available": bool(windows and executable),
        "reason": reason,
        "capabilities": ["audit", "dxf_dwg_convert", "stdout_stderr_evidence"],
        "real_machine_verified": False,
    }


def _script_path(path: str | Path) -> str:
    """Quote a Windows path for an AutoCAD ``.scr`` file."""
    value = str(Path(path)).replace("/", "\\").replace('"', '""')
    return f'"{value}"'


def build_script(action: str, *, output_path: str | Path | None = None, audit: bool = True) -> str:
    """Build a deterministic Core Console script.

    AutoCAD scripts are line-oriented.  Keeping this function pure makes the
    command contract testable on non-Windows hosts and avoids shell quoting.
    """
    if action not in {"audit", "convert"}:
        raise AutoCADError("AUTOCAD_ACTION_UNSUPPORTED", f"unsupported AutoCAD action: {action}")
    lines: list[str] = []
    if audit:
        lines.extend(("_.AUDIT", "_Y"))
    if action == "convert":
        if output_path is None:
            raise AutoCADError("AUTOCAD_OUTPUT_REQUIRED", "output_path is required for conversion")
        lines.extend(("_.SAVEAS", _script_path(output_path)))
    lines.extend(("_.QUIT", "_Y", ""))
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_core_console(
    input_path: str | Path,
    *,
    action: str,
    output_path: str | Path | None = None,
    config: dict[str, Any] | None = None,
    timeout_seconds: int = 600,
    audit: bool = True,
) -> dict[str, Any]:
    """Run Core Console and return bounded, auditable process evidence."""
    status = backend_status(config)
    if not status["windows"]:
        raise AutoCADError("AUTOCAD_REQUIRES_WINDOWS", "AutoCAD Core Console requires a Windows worker",
                           details=status)
    executable = status.get("executable")
    if not executable:
        raise AutoCADError("AUTOCAD_CORE_CONSOLE_NOT_FOUND", "AutoCAD Core Console was not found",
                           details=status)
    source = Path(input_path).resolve()
    if not source.is_file():
        raise AutoCADError("AUTOCAD_INPUT_NOT_FOUND", str(source))
    target = Path(output_path).resolve() if output_path is not None else None
    if action == "convert" and target is None:
        raise AutoCADError("AUTOCAD_OUTPUT_REQUIRED", "output_path is required for conversion")
    timeout_seconds = max(1, min(int(timeout_seconds), 7200))
    with tempfile.TemporaryDirectory(prefix="simloom-autocad-") as temporary:
        script = Path(temporary) / "simloom.scr"
        script.write_text(build_script(action, output_path=target, audit=audit), encoding="utf-8", newline="\r\n")
        command = [executable, "/i", str(source), "/s", str(script)]
        started = time.monotonic()
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                       timeout=timeout_seconds, cwd=temporary, check=False)
        except subprocess.TimeoutExpired as exc:
            raise AutoCADError("AUTOCAD_TIMEOUT", f"AutoCAD timed out after {timeout_seconds}s",
                               details={"command": command, "timeout_seconds": timeout_seconds,
                                        "stdout": str(exc.stdout or "")[-20000:], "stderr": str(exc.stderr or "")[-20000:]}) from exc
        duration = time.monotonic() - started
    result: dict[str, Any] = {
        "schema_version": SCHEMA,
        "action": action,
        "command": command,
        "returncode": int(completed.returncode),
        "stdout": str(completed.stdout or "")[-20000:],
        "stderr": str(completed.stderr or "")[-20000:],
        "duration_seconds": round(duration, 6),
        "input": {"path": str(source), "sha256": _sha256(source), "bytes": source.stat().st_size},
        "output": None,
    }
    if completed.returncode != 0:
        raise AutoCADError("AUTOCAD_PROCESS_FAILED", f"AutoCAD exited with code {completed.returncode}", details=result)
    if target is not None:
        if not target.is_file() or target.stat().st_size <= 0:
            raise AutoCADError("AUTOCAD_OUTPUT_MISSING", str(target), details=result)
        result["output"] = {"path": str(target), "sha256": _sha256(target), "bytes": target.stat().st_size}
    return result


def iter_candidate_config(config: dict[str, Any] | None) -> Iterable[dict[str, Any]]:
    """Yield compatible nested config blocks for callers that support both forms."""
    if not isinstance(config, dict):
        return
    yield config
    for key in ("autocad", "windows_bridge"):
        nested = config.get(key)
        if isinstance(nested, dict):
            yield nested
