from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ToolName(str, Enum):
    ANSYS = "ansys"
    CATIA = "catia"
    ADAMS = "adams"
    VI_GRADE = "vi-grade"


class AssetKind(str, Enum):
    GEOMETRY = "geometry"
    TABLE = "table"
    RESULT = "result"
    SCRIPT = "script"
    PROJECT = "project"
    UNKNOWN = "unknown"


@dataclass
class AgentRequest:
    prompt: str
    project_dir: str = "."
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanStep:
    id: str
    tool: ToolName
    action: str
    objective: str
    depends_on: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    operation: str = ""
    operation_version: str = "1.0"


@dataclass
class ExecutionPlan:
    request: AgentRequest
    steps: list[PlanStep]
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": asdict(self.request),
            "steps": [asdict(step) | {"tool": step.tool.value} for step in self.steps],
            "assumptions": self.assumptions,
        }


@dataclass
class ToolResult:
    step_id: str
    tool: ToolName
    status: str
    message: str
    outputs: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"tool": self.tool.value}


@dataclass
class RunReport:
    plan: ExecutionPlan
    results: list[ToolResult]
    generated_files: dict[str, str] = field(default_factory=dict)
    run_id: str = ""
    status: str = ""
    validation: dict[str, Any] = field(default_factory=dict)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    artifact_manifest: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "results": [result.to_dict() for result in self.results],
            "generated_files": self.generated_files,
            "run_id": self.run_id,
            "status": self.status,
            "validation": self.validation,
            "timeline": self.timeline,
            "artifact_manifest": self.artifact_manifest,
        }
