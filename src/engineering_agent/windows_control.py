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
        """Report configuration evidence; COM dispatch alone is not a vendor task test."""
        tools = tools or {}
        result = {"platform": platform.system(), "smoke_requested": smoke, "tools": []}
        for name in ("catia", "ansys", "adams", "vi-grade"):
            cfg = tools.get(name, {})
            prog_id = self.config.get(name.replace("-", "_") + "_prog_id", "")
            executable = str(cfg.get("executable") or "")
            found = bool(executable and shutil.which(executable))
            configured = bool(prog_id or executable or cfg.get("actions") or cfg.get("bridge_url"))
            item = {"tool": name, "prog_id": prog_id, "executable": executable,
                    "executable_found": found, "com_dispatch": "not_run",
                    "status": "configured" if configured else "unavailable",
                    "real_machine_verified": False, "evidence": ["VENDOR_OPERATION_NOT_VERIFIED"]}
            if smoke and prog_id:
                try:
                    application = self.dispatch(prog_id)
                    item["com_dispatch"] = "passed"
                    item["evidence"].append("COM_DISPATCH_ONLY")
                    del application
                except Exception as exc:
                    item["com_dispatch"] = "failed"
                    item["evidence"].append(str(exc))
            result["tools"].append(item)
        result["ready_count"] = 0  # Requires separately recorded real operation evidence.
        result["configured_count"] = sum(i["status"] == "configured" for i in result["tools"])
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
