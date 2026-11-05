from __future__ import annotations

import platform
import shutil
from typing import Any


class WindowsAutomation:
    """Optional COM/bridge boundary; safe on non-Windows development hosts."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def doctor(self) -> dict[str, Any]:
        on_windows = platform.system().lower() == "windows"
        try:
            import win32com.client  # noqa: F401
            com_available = True
        except ImportError:
            com_available = False
        return {"platform": platform.system(), "windows": on_windows, "pywin32": com_available}

    def validate_backends(self, tools: dict[str, Any] | None = None, *, smoke: bool = False) -> dict[str, Any]:
        """Return a four-tool readiness matrix and optionally dispatch COM.

        A normal doctor call is read-only. ``smoke=True`` is an explicit
        Windows-host validation that dispatches each configured ProgID and
        immediately releases the COM reference; it does not run a solve.
        """
        tools = tools or {}
        descriptors = {
            "catia": {"prog_id": self.config.get("catia_prog_id", "CATIA.Application"), "executable": tools.get("catia", {}).get("executable", "")},
            "ansys": {"prog_id": self.config.get("ansys_prog_id", "Ansys.Application"), "executable": tools.get("ansys", {}).get("executable", "")},
            "adams": {"prog_id": self.config.get("adams_prog_id", "Adams.View"), "executable": tools.get("adams", {}).get("executable", "")},
            "vi-grade": {"prog_id": self.config.get("vi_grade_prog_id", "VIgrade.Application"), "executable": tools.get("vi-grade", {}).get("executable", "")},
        }
        result = {"platform": platform.system(), "smoke_requested": smoke, "tools": []}
        for name, descriptor in descriptors.items():
            executable = str(descriptor["executable"] or "")
            executable_found = bool(executable and (shutil.which(executable) or platform.system().lower() == "windows" and ("\\" in executable or "/" in executable)))
            item = {"tool": name, "prog_id": descriptor["prog_id"], "executable": executable, "executable_found": executable_found, "com_dispatch": "not_run", "status": "unavailable", "evidence": []}
            if platform.system().lower() != "windows":
                item["evidence"].append("WINDOWS_HOST_REQUIRED")
            elif not self.doctor()["pywin32"]:
                item["evidence"].append("PYWIN32_REQUIRED")
            elif smoke:
                try:
                    application = self.dispatch(str(descriptor["prog_id"]))
                    item["com_dispatch"] = "passed"
                    item["status"] = "ready"
                    item["evidence"].append(f"COM_DISPATCH_OK:{type(application).__name__}")
                    del application
                except Exception as exc:
                    item["com_dispatch"] = "failed"
                    item["evidence"].append(str(exc))
            elif executable_found or descriptor["prog_id"]:
                item["status"] = "configured"
                item["evidence"].append("SMOKE_NOT_RUN")
            result["tools"].append(item)
        result["ready_count"] = sum(item["status"] == "ready" for item in result["tools"])
        result["configured_count"] = sum(item["status"] in {"configured", "ready"} for item in result["tools"])
        return result

    def dispatch(self, prog_id: str) -> Any:
        if platform.system().lower() != "windows":
            raise RuntimeError("WINDOWS_COM_REQUIRES_WINDOWS_HOST")
        try:
            import win32com.client
        except ImportError as exc:
            raise RuntimeError("INSTALL_PYWIN32_FOR_COM") from exc
        return win32com.client.Dispatch(prog_id)

    def invoke(self, prog_id: str, method: str, *args: Any) -> Any:
        application = self.dispatch(prog_id)
        operation = getattr(application, method)
        return operation(*args)
