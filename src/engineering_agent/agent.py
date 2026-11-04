from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters import AdapterContext, ToolAdapter, build_adapters
from .ingest import ArtifactReader
from .formats import write_csv, write_dxf, write_json, write_pdf, write_step, write_svg
from .dxf import diff_documents, read_dxf, transform_document
from .models import AgentRequest, ExecutionPlan, PlanStep, RunReport, ToolResult
from .llm_planner import AutonomousPlanner
from .object_model import EngineeringProject
from .operations import OperationRegistry
from .results import MetricRule, evaluate_metrics, parse_result_file
from .script_gen import ModelSpec, ScriptGenerator
from .transactions import RunTransaction
from .validation import validate_artifacts, validate_asset, validate_plan, validate_report
from .versioning import VersionStore
from .windows_control import WindowsAutomation


class EngineeringAgent:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.planner = AutonomousPlanner(self.config)
        self.adapters: dict[Any, ToolAdapter] = build_adapters(self.config)
        self.windows = WindowsAutomation(self.config.get("windows", {}))
        self.reader = ArtifactReader()
        self.operations = OperationRegistry()

    def plan(self, prompt: str, *, project_dir: str = ".", parameters: dict[str, Any] | None = None) -> ExecutionPlan:
        return self.planner.plan(AgentRequest(prompt, project_dir, parameters or {}))

    def run(
        self,
        prompt: str,
        *,
        project_dir: str = ".",
        parameters: dict[str, Any] | None = None,
        dry_run: bool = True,
    ) -> RunReport:
        plan = self.plan(prompt, project_dir=project_dir, parameters=parameters)
        plan_validation = validate_plan(plan)
        txn = RunTransaction(project_dir)
        txn.begin(plan.to_dict())
        txn.checkpoint("before-execution", {"plan_valid": plan_validation["valid"]})
        generated_files = {}
        staged_files = {}
        if self.config.get("generate_scripts", True):
            spec_data = (parameters or {}).get("model_spec", {})
            staged_files = ScriptGenerator(project_dir, txn.stage_dir / "generated").generate(ModelSpec(**spec_data))
            generated_files = staged_files
        context = AdapterContext(
            project_dir=Path(project_dir).resolve(),
            dry_run=dry_run,
            timeout_seconds=int(self.config.get("timeouts", {}).get("step_seconds", 600)),
            environment=self.config.get("environment", {}),
        )
        results: list[ToolResult] = []
        completed: set[str] = set()
        for step in plan.steps:
            missing = [dependency for dependency in step.depends_on if dependency not in completed]
            if missing:
                result = ToolResult(
                    step_id=step.id,
                    tool=step.tool,
                    status="blocked",
                    message=f"依赖步骤未完成: {', '.join(missing)}",
                )
            else:
                result = self.adapters[step.tool].execute(step, context)
            results.append(result)
            if result.status in {"planned", "completed"}:
                completed.add(step.id)
            elif result.status in {"failed", "unavailable", "blocked"}:
                # Preserve dependency semantics for later steps.
                completed.discard(step.id)
        report = RunReport(plan, results, generated_files, txn.run_id, "", {"plan": plan_validation}, txn.events())
        if not plan_validation["valid"]:
            txn.rollback("invalid execution plan")
            report.status = "failed"
            report.timeline = txn.events()
            return report
        if any(result.status in {"failed", "blocked", "unavailable"} for result in results):
            txn.rollback("one or more execution steps failed")
            report.status = "rolled_back"
            report.generated_files = {}
            report.timeline = txn.events()
            return report
        if staged_files:
            report.generated_files = txn.commit(staged_files)
        else:
            txn.record("commit", {"files": {}})
            txn.state = "committed"
        report.status = "committed"
        report.timeline = txn.events()
        report.validation = {"plan": plan_validation, "report": validate_report(report), "artifacts": validate_artifacts(report.generated_files)}
        return report

    def generate(self, project_dir: str = ".", model_spec: dict[str, Any] | None = None) -> dict[str, str]:
        return ScriptGenerator(project_dir).generate(ModelSpec(**(model_spec or {})))

    def ingest(self, path: str, kind: str | None = None) -> dict[str, Any]:
        return self.reader.read(path, kind).to_dict()

    def validate_asset(self, path: str, required_metrics: list[str] | None = None) -> dict[str, Any]:
        return validate_asset(self.ingest(path), required_metrics)

    def write_asset(self, path: str, format_name: str, payload: dict[str, Any]) -> str:
        format_name = format_name.lower()
        if format_name == "json":
            return write_json(path, payload)
        if format_name == "csv":
            return write_csv(path, payload.get("rows", []), payload.get("columns"))
        if format_name == "step":
            return write_step(path, payload.get("points", []), payload.get("name", "engineering_agent"))
        if format_name == "dxf":
            return write_dxf(
                path,
                payload.get("lines", []),
                payload.get("circles", []),
                polylines=payload.get("polylines", []),
                points=payload.get("points", []),
                arcs=payload.get("arcs", []),
                texts=payload.get("texts", []),
                dimensions=payload.get("dimensions", []),
                entities=payload.get("entities", []),
            )
        if format_name == "svg":
            return write_svg(path, lines=payload.get("lines", []), circles=payload.get("circles", []), title=payload.get("title", "Engineering Agent"))
        if format_name == "pdf":
            return write_pdf(path, payload.get("text", ""))
        raise ValueError(f"UNSUPPORTED_FORMAT:{format_name}")

    def inspect_project(self, project_dir: str = ".") -> dict[str, Any]:
        return self.reader.project(project_dir).to_dict()

    def validate_artifacts(self, files: dict[str, str] | list[str]) -> dict[str, Any]:
        return validate_artifacts(files)

    def create_version(self, project_dir: str, label: str, paths: list[str], parent: str | None = None) -> dict[str, Any]:
        return VersionStore(project_dir).ingest_and_create(label, paths, parent)

    def list_versions(self, project_dir: str) -> list[dict[str, Any]]:
        return VersionStore(project_dir).list()

    def diff_versions(self, project_dir: str, left: str, right: str) -> dict[str, Any]:
        return VersionStore(project_dir).diff(left, right)

    def execute_operation(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = arguments or {}
        spec = self.operations.get(name)
        missing = self.operations.validate(name, arguments)
        if missing:
            raise ValueError("MISSING_PARAMETER:" + ",".join(missing))
        if name == "results.parse":
            return self.ingest(arguments["result_file"], "result")
        if name == "results.evaluate":
            rules = [MetricRule(**rule) for rule in arguments["rules"]]
            return {"verdicts": self.evaluate(arguments["result_file"], rules)}
        if name == "project.inspect":
            return self.inspect_project(arguments.get("project_dir", "."))
        if name == "project.version":
            return self.create_version(arguments.get("project_dir", "."), arguments["label"], arguments.get("paths", []), arguments.get("parent"))
        if name == "project.diff":
            return self.diff_versions(arguments.get("project_dir", "."), arguments["left"], arguments["right"])
        if name == "artifact.write":
            return {"path": self.write_asset(arguments["path"], arguments["format"], arguments["payload"])}
        if name == "geometry.dxf_inspect":
            document = read_dxf(arguments["input_file"])
            return {"geometry": document}
        if name == "geometry.dxf_diff":
            return diff_documents(read_dxf(arguments["left_file"]), read_dxf(arguments["right_file"]))
        if name == "geometry.validate":
            return {"validation": self.validate_asset(arguments["path"])}
        if name == "geometry.dxf_transform":
            project_root = Path(arguments.get("project_dir", ".")).resolve()
            input_path = Path(arguments["input_file"]).resolve()
            output_path = Path(arguments["output_file"]).resolve()
            if project_root not in output_path.parents and output_path != project_root:
                raise ValueError("PATH_OUTSIDE_PROJECT")
            transformed = transform_document(read_dxf(input_path), translate=arguments.get("translate", (0, 0, 0)), scale=float(arguments.get("scale", 1.0)), rotate_deg=float(arguments.get("rotate_deg", 0.0)))
            txn = RunTransaction(project_root)
            txn.begin({"operation": name, "input_file": str(input_path), "output_file": str(output_path)})
            staged = txn.stage_dir / output_path.name
            write_dxf(staged, entities=transformed["entities"])
            relative = str(output_path.relative_to(project_root))
            committed = txn.commit({relative: str(staged)})
            return {"path": committed[relative], "geometry": transformed}
        action_alias = {"catia.create_model": "create_or_update_model", "vi-grade.run_scenario": "run_vehicle_dynamics"}
        step = PlanStep("operation", spec.tool, action_alias.get(name, name.split(".", 1)[1]), spec.description, parameters=arguments)
        context = AdapterContext(project_dir=Path(arguments.get("project_dir", ".")).resolve(), dry_run=bool(arguments.get("dry_run", True)), timeout_seconds=int(self.config.get("timeouts", {}).get("step_seconds", 600)), environment=self.config.get("environment", {}))
        return self.adapters[spec.tool].execute(step, context).to_dict()

    @staticmethod
    def evaluate(result_file: str, rules: list[MetricRule]) -> list[dict[str, Any]]:
        data = parse_result_file(result_file)
        return [verdict.to_dict() for verdict in evaluate_metrics(data, rules)]

    def doctor(self) -> list[dict[str, Any]]:
        return [self.adapters[name].doctor() for name in self.adapters] + [{"tool": "windows-com", **self.windows.doctor()}]

    @staticmethod
    def load_config(path: str | Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
