from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import PlanStep, ToolName


@dataclass(frozen=True)
class OperationSpec:
    name: str
    tool: ToolName
    description: str
    version: str = "1.0"
    required_parameters: tuple[str, ...] = ()
    output_kinds: tuple[str, ...] = ()
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    side_effects: tuple[str, ...] = ()
    safety: str = "safe"

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tool": self.tool.value,
            "description": self.description,
            "version": self.version,
            "required_parameters": list(self.required_parameters),
            "output_kinds": list(self.output_kinds),
            "parameters_schema": self.parameters_schema,
            "output_schema": self.output_schema,
            "side_effects": list(self.side_effects),
            "safety": self.safety,
        }


OPERATION_SPECS: tuple[OperationSpec, ...] = (
    OperationSpec("catia.create_model", ToolName.CATIA, "Create or update a parametric CATIA model", parameters_schema={"model_spec": {"type": "object"}}, output_schema={"files": {"type": "object"}}, output_kinds=("catpart", "step"), side_effects=("write_artifacts",), safety="write"),
    OperationSpec("catia.export_step", ToolName.CATIA, "Export a CATIA model to STEP", required_parameters=("input_model",), parameters_schema={"input_model": {"type": "string"}}, output_schema={"step": {"type": "string"}}, output_kinds=("step",), side_effects=("write_artifacts",), safety="write"),
    OperationSpec("ansys.mesh_and_solve", ToolName.ANSYS, "Mesh and solve a structural, thermal, or modal analysis", parameters_schema={"analysis_type": {"type": "string", "enum": ["static", "modal", "thermal"]}}, output_schema={"result": {"type": "object"}}, output_kinds=("result", "report"), side_effects=("compute", "write_artifacts"), safety="execute"),
    OperationSpec("adams.build_and_run_multibody", ToolName.ADAMS, "Build and solve a multibody dynamics model", parameters_schema={"end_time": {"type": "number"}, "step_size": {"type": "number"}}, output_schema={"motion": {"type": "string"}}, output_kinds=("motion", "report"), side_effects=("compute", "write_artifacts"), safety="execute"),
    OperationSpec("vi-grade.run_scenario", ToolName.VI_GRADE, "Run a vehicle dynamics scenario and collect metrics", required_parameters=("scenario",), parameters_schema={"scenario": {"type": "string"}}, output_schema={"metrics": {"type": "object"}}, output_kinds=("metrics", "report"), side_effects=("compute", "write_artifacts"), safety="execute"),
    OperationSpec("results.parse", ToolName.ANSYS, "Parse and normalize a result artifact", required_parameters=("result_file",), parameters_schema={"result_file": {"type": "string"}}, output_schema={"asset": {"type": "object"}}, output_kinds=("metrics",), safety="read"),
    OperationSpec("results.evaluate", ToolName.ANSYS, "Evaluate engineering metrics against rules", required_parameters=("result_file", "rules"), parameters_schema={"result_file": {"type": "string"}, "rules": {"type": "array"}}, output_schema={"verdicts": {"type": "array"}}, output_kinds=("verdict",), safety="read"),
    OperationSpec("project.inspect", ToolName.CATIA, "Read all supported artifacts into the canonical object model", parameters_schema={"project_dir": {"type": "string"}}, output_schema={"project": {"type": "object"}}, output_kinds=("project",), safety="read"),
    OperationSpec("project.version", ToolName.CATIA, "Create an immutable project snapshot", required_parameters=("label",), parameters_schema={"label": {"type": "string"}, "paths": {"type": "array"}}, output_schema={"version": {"type": "object"}}, output_kinds=("version",), side_effects=("write_metadata",), safety="write"),
    OperationSpec("project.diff", ToolName.CATIA, "Compare two project snapshots", required_parameters=("left", "right"), parameters_schema={"left": {"type": "string"}, "right": {"type": "string"}}, output_schema={"changes": {"type": "array"}}, output_kinds=("diff",), safety="read"),
    OperationSpec("artifact.write", ToolName.CATIA, "Write a canonical asset to a local format", required_parameters=("path", "format", "payload"), parameters_schema={"path": {"type": "string"}, "format": {"type": "string", "enum": ["step", "csv", "json", "dxf", "svg", "pdf"]}, "payload": {"type": "object"}}, output_schema={"path": {"type": "string"}}, output_kinds=("artifact",), side_effects=("write_artifacts",), safety="write"),
    OperationSpec("geometry.dxf_inspect", ToolName.CATIA, "Parse DXF entities, layers and bounds into the canonical geometry model", required_parameters=("input_file",), parameters_schema={"input_file": {"type": "string"}}, output_schema={"geometry": {"type": "object"}}, output_kinds=("geometry",), safety="read"),
    OperationSpec("geometry.dxf_transform", ToolName.CATIA, "Transform DXF geometry and write a new semantic DXF artifact", required_parameters=("input_file", "output_file"), parameters_schema={"input_file": {"type": "string"}, "output_file": {"type": "string"}, "translate": {"type": "array", "items": {"type": "number"}}, "scale": {"type": "number"}, "rotate_deg": {"type": "number"}}, output_schema={"path": {"type": "string"}, "geometry": {"type": "object"}}, output_kinds=("geometry", "artifact"), side_effects=("write_artifacts",), safety="write"),
    OperationSpec("geometry.dxf_diff", ToolName.CATIA, "Compare DXF entities, layers and bounds", required_parameters=("left_file", "right_file"), parameters_schema={"left_file": {"type": "string"}, "right_file": {"type": "string"}}, output_schema={"changes": {"type": "array"}}, output_kinds=("diff",), safety="read"),
    OperationSpec("geometry.validate", ToolName.CATIA, "Run independent geometry validation on a local artifact", required_parameters=("path",), parameters_schema={"path": {"type": "string"}}, output_schema={"validation": {"type": "object"}}, output_kinds=("validation",), safety="read"),
)


class OperationRegistry:
    def __init__(self, specs: tuple[OperationSpec, ...] = OPERATION_SPECS):
        self.specs = {spec.name: spec for spec in specs}

    def list(self) -> list[dict[str, Any]]:
        return [self.specs[name].schema() for name in sorted(self.specs)]

    def get(self, name: str) -> OperationSpec:
        try:
            return self.specs[name]
        except KeyError as exc:
            raise ValueError(f"UNKNOWN_OPERATION:{name}") from exc

    def validate(self, name: str, parameters: dict[str, Any] | None = None) -> list[str]:
        spec = self.get(name)
        parameters = parameters or {}
        return [key for key in spec.required_parameters if not parameters.get(key)]

    def validate_step(self, step: PlanStep) -> list[str]:
        operation = f"{step.tool.value}.{step.action}"
        if operation not in self.specs:
            # Existing planners use friendly actions; map them to typed names.
            aliases = {
                "catia.create_or_update_model": "catia.create_model",
                "ansys.mesh_and_solve": "ansys.mesh_and_solve",
                "adams.build_and_run_multibody": "adams.build_and_run_multibody",
                "vi-grade.run_vehicle_dynamics": "vi-grade.run_scenario",
            }
            operation = aliases.get(operation, operation)
        try:
            missing = self.validate(operation, step.parameters)
        except ValueError as exc:
            return [str(exc)]
        return [f"MISSING_PARAMETER:{key}" for key in missing]
