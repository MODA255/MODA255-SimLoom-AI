from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models import PlanStep, ToolName, ToolResult


class AdapterError(RuntimeError):
    pass


@dataclass
class AdapterContext:
    project_dir: Path
    dry_run: bool = True
    timeout_seconds: int = 600
    environment: dict[str, str] = field(default_factory=dict)

    def env(self) -> dict[str, str]:
        result = dict(os.environ)
        result.update(self.environment)
        return result


class ToolAdapter:
    name: ToolName
    capabilities: frozenset[str] = frozenset()

    def doctor(self) -> dict[str, Any]:
        return {
            "tool": self.name.value,
            "available": True,
            "capabilities": sorted(self.capabilities),
        }

    def execute(self, step: PlanStep, context: AdapterContext) -> ToolResult:
        raise NotImplementedError


class CommandAdapter(ToolAdapter):
    """Base adapter for a vendor executable or a remote gateway command.

    The agent never builds a shell string. Commands stay as an argv list so a
    natural-language prompt cannot turn into arbitrary shell execution.
    """

    executable_key = "executable"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.executable = config.get(self.executable_key)
        self.bridge_url = config.get("bridge_url")

    def _command(self, step: PlanStep, context: AdapterContext) -> list[str] | None:
        if not self.executable:
            return None
        command = [str(self.executable)]
        command.extend(str(value) for value in step.parameters.get("extra_args", []))
        return command

    def doctor(self) -> dict[str, Any]:
        executable = self.executable
        resolved = shutil.which(executable) if executable else None
        return {
            "tool": self.name.value,
            "available": bool(resolved or self.bridge_url),
            "mode": "bridge" if self.bridge_url else "command",
            "executable": executable,
            "resolved": resolved,
            "bridge_url": self.bridge_url,
            "capabilities": sorted(self.capabilities),
        }

    def execute(self, step: PlanStep, context: AdapterContext) -> ToolResult:
        if context.dry_run:
            return ToolResult(
                step_id=step.id,
                tool=self.name,
                status="planned",
                message=f"dry-run: {self.name.value} adapter accepted {step.action}",
                outputs=step.outputs,
                details={"doctor": self.doctor()},
            )

        command = self._command(step, context)
        if not command and not self.bridge_url:
            return ToolResult(
                step_id=step.id,
                tool=self.name,
                status="unavailable",
                message=f"configure {self.name.value}.executable or bridge_url before execution",
                outputs=[],
                details={"doctor": self.doctor()},
            )
        if self.bridge_url:
            return self._execute_bridge(step, context)

        assert command is not None
        try:
            completed = subprocess.run(
                command,
                cwd=context.project_dir,
                env=context.env(),
                capture_output=True,
                text=True,
                timeout=context.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ToolResult(
                step_id=step.id,
                tool=self.name,
                status="failed",
                message=f"{self.name.value} process failed: {exc}",
                details={"command": command},
            )
        status = "completed" if completed.returncode == 0 else "failed"
        return ToolResult(
            step_id=step.id,
            tool=self.name,
            status=status,
            message=f"{self.name.value} returned exit code {completed.returncode}",
            outputs=step.outputs if status == "completed" else [],
            details={
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            },
        )

    def _execute_bridge(self, step: PlanStep, context: AdapterContext) -> ToolResult:
        # The HTTP bridge is intentionally optional.  Vendor-specific gateways
        # can implement this same contract without adding vendor SDKs here.
        try:
            import httpx

            response = httpx.post(
                self.bridge_url,
                json={
                    "tool": self.name.value,
                    "step": {
                        "id": step.id,
                        "action": step.action,
                        "objective": step.objective,
                        "parameters": step.parameters,
                        "inputs": step.inputs,
                        "outputs": step.outputs,
                    },
                },
                timeout=context.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json() if response.content else {}
        except Exception as exc:  # bridge failures are reported to the caller
            return ToolResult(
                step_id=step.id,
                tool=self.name,
                status="failed",
                message=f"{self.name.value} bridge failed: {exc}",
                details={"bridge_url": self.bridge_url},
            )
        return ToolResult(
            step_id=step.id,
            tool=self.name,
            status=body.get("status", "completed"),
            message=body.get("message", f"{self.name.value} bridge accepted the step"),
            outputs=body.get("outputs", step.outputs),
            details=body,
        )
