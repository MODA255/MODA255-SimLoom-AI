from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters import AdapterContext, ToolAdapter, build_adapters
from .ingest import ArtifactReader
from .formats import write_csv, write_dxf, write_json, write_pdf, write_step, write_svg
from .mesh import write_obj
from .dxf import diff_documents, read_dxf, transform_document
from .brep import brep_doctor, get_brep_backend, solve_constraints
from .models import AgentRequest, ExecutionPlan, PlanStep, RunReport, ToolResult
from .llm_planner import AutonomousPlanner
from .object_model import EngineeringProject
from .offline_workflow import run_offline_vehicle_chain
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
            txn._write_state()
            txn.release_lock()
        report.status = "committed"
        report.timeline = txn.events()
        report.validation = {"plan": plan_validation, "report": validate_report(report), "artifacts": validate_artifacts(report.generated_files)}
        return report

    def generate(self, project_dir: str = ".", model_spec: dict[str, Any] | None = None) -> dict[str, str]:
        """Generate vendor inputs through the same staged transaction path as runs."""
        txn = RunTransaction(project_dir)
        txn.begin({"operation": "engineering_generate", "model_spec": model_spec or {}})
        try:
            staged = ScriptGenerator(project_dir, txn.stage_dir / "generated").generate(ModelSpec(**(model_spec or {})))
            return txn.commit(staged)
        except Exception as exc:
            txn.fail(str(exc))
            raise

    def run_offline_vehicle_chain(self, project_dir: str = ".", model_spec: dict[str, Any] | None = None, rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Run the deterministic four-tool fixture chain without vendor software."""
        return run_offline_vehicle_chain(project_dir, model_spec, rules)

    def ingest(self, path: str, kind: str | None = None) -> dict[str, Any]:
        return self.reader.read(path, kind).to_dict()

    def validate_asset(self, path: str, required_metrics: list[str] | None = None) -> dict[str, Any]:
        return validate_asset(self.ingest(path), required_metrics)

    def write_asset(self, path: str, format_name: str, payload: dict[str, Any], *, project_dir: str | Path | None = None) -> str:
        destination = Path(path)
        project_root = Path(project_dir or (destination.parent if destination.is_absolute() else ".")).resolve()
        destination = (project_root / destination if not destination.is_absolute() else destination).resolve()
        if project_root not in destination.parents and destination != project_root:
            raise ValueError("PATH_OUTSIDE_PROJECT")
        txn = RunTransaction(project_root)
        txn.begin({"operation": "artifact.write", "path": str(destination), "format": format_name})
        try:
            staged = txn.stage_dir / "asset" / destination.name
            self._write_asset_file(staged, format_name, payload)
            relative = str(destination.relative_to(project_root))
            committed = txn.commit({relative: str(staged)}, destination_root=project_root)
            return committed[relative]
        except Exception as exc:
            txn.fail(str(exc))
            raise

    def _write_asset_file(self, path: str, format_name: str, payload: dict[str, Any]) -> str:
        format_name = format_name.lower()
        if format_name == "json":
            return write_json(path, payload)
        if format_name == "csv":
            return write_csv(path, payload.get("rows", []), payload.get("columns"))
        if format_name == "step":
            return write_step(path, payload.get("points", []), payload.get("name", "engineering_agent"))
        if format_name == "obj":
            return write_obj(path, payload.get("vertices", []), payload.get("faces", []), name=payload.get("name", "engineering_agent"))
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
                hatches=payload.get("hatches", []),
                inserts=payload.get("inserts", []),
                entities=payload.get("entities", []),
                blocks=payload.get("blocks", []),
                coordinate_systems=payload.get("coordinate_systems", []),
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
            return {"path": self.write_asset(arguments["path"], arguments["format"], arguments["payload"], project_dir=arguments.get("project_dir"))}
        if name == "geometry.dxf_inspect":
            document = read_dxf(arguments["input_file"])
            return {"geometry": document}
        if name == "geometry.dxf_diff":
            return diff_documents(read_dxf(arguments["left_file"]), read_dxf(arguments["right_file"]))
        if name == "geometry.validate":
            return {"validation": self.validate_asset(arguments["path"])}
        if name == "geometry.mesh_inspect":
            return {"mesh": self.ingest(arguments["input_file"], "geometry")["geometry"]}
        if name == "geometry.mesh_validate":
            return {"validation": self.validate_asset(arguments["input_file"])}
        if name == "brep.solve_constraints":
            result = solve_constraints(arguments["parameters"], arguments["constraints"], tolerance=float(arguments.get("tolerance", 1e-8)), max_iterations=int(arguments.get("max_iterations", 100)))
            return result.to_dict()
        if name == "brep.inspect_step":
            return {"brep": get_brep_backend().inspect_step(arguments["input_file"])}
        if name in {"brep.boolean", "brep.create_primitive"}:
            project_root = Path(arguments.get("project_dir", ".")).resolve()
            output_path = Path(arguments["output_file"]).resolve()
            if project_root not in output_path.parents and output_path != project_root:
                raise ValueError("PATH_OUTSIDE_PROJECT")
            txn = RunTransaction(project_root)
            txn.begin({"operation": name, "arguments": arguments})
            try:
                staged = txn.stage_dir / output_path.name
                backend = get_brep_backend()
                if name == "brep.boolean":
                    result = backend.boolean(arguments["operation"], arguments["left_file"], arguments["right_file"], staged)
                else:
                    result = backend.create_primitive(arguments["primitive"], arguments.get("parameters", {}), staged)
                relative = str(output_path.relative_to(project_root))
                committed = txn.commit({relative: str(staged)})
                result["output"] = committed[relative]
                return result
            except Exception as exc:
                txn.fail(str(exc))
                raise
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
        return [self.adapters[name].doctor() for name in self.adapters] + [{"tool": "windows-com", **self.windows.doctor()}, {"tool": "windows-backends", **self.windows.validate_backends(self.config.get("tools", {}))}, {"tool": "step-brep", **brep_doctor()}]

    def capabilities(self) -> dict[str, Any]:
        """Return a user-facing readiness matrix without probing vendor solves."""
        diagnostics = self.doctor()
        by_tool = {item.get("tool"): item for item in diagnostics if item.get("tool")}
        windows = by_tool.get("windows-com", {})
        backends = by_tool.get("windows-backends", {})
        brep = by_tool.get("step-brep", {})
        llm = self.config.get("llm", {})
        connected = bool(windows.get("windows") and windows.get("pywin32"))
        executable_ready = int(backends.get("configured_count", 0)) == 4
        return {
            "schema_version": "simloom-ai/capabilities/v1",
            "modes": [
                {"id": "offline", "ready": True, "label": "离线体验", "reason": "不需要商业软件"},
                {"id": "rehearsal", "ready": True, "label": "排练模式", "reason": "生成脚本并验证计划，不启动外部软件"},
                {"id": "connected", "ready": connected, "label": "Windows 连接", "reason": "需要 Windows 和 pywin32"},
                {"id": "execute", "ready": executable_ready and connected, "label": "真实执行", "reason": "需要四个软件的 worker 或可执行文件"},
            ],
            "components": {
                "llm": {"enabled": bool(llm.get("enabled")), "provider": llm.get("provider", "deterministic")},
                "step_brep": {"available": bool(brep.get("available")), "backend": brep.get("backend", brep.get("name", "unavailable"))},
                "windows": {"available": connected, "platform": windows.get("platform")},
            },
            "tools": {name: by_tool.get(name, {}) for name in ("catia", "ansys", "adams", "vi-grade")},
        }

    def validate_windows_backends(self, *, smoke: bool = False) -> dict[str, Any]:
        return self.windows.validate_backends(self.config.get("tools", {}), smoke=smoke)

    @staticmethod
    def load_config(path: str | Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
