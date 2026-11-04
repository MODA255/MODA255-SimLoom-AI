from __future__ import annotations

import platform
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
