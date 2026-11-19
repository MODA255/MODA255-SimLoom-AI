from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters import AdapterContext, ToolAdapter, build_adapters
from .agentcad_compat import normalize_operation
from .cad_model import CadDocument, validate_cad_document
from .cad_operations import CadOperationEngine
from .cad_units import normalize_cad_arguments
from .cad_history import CadHistory
from .ingest import ArtifactReader
from .image_inspection import inspect_image as inspect_raster_image, measure_image as measure_raster_image
from .formats import write_csv, write_dxf, write_json, write_pdf, write_step, write_svg
from .mesh import read_mesh, write_glb, write_obj
from .geometry_validator import validate_geometry_object
from .dxf import diff_documents, read_dxf, transform_document
from .brep import brep_doctor, get_brep_backend, solve_constraints
from .models import AgentRequest, ExecutionPlan, PlanStep, RunReport, ToolResult, ToolName
from .llm_planner import AutonomousPlanner
from .object_model import EngineeringProject
from .offline_workflow import run_offline_vehicle_chain
from .operations import OperationRegistry
from .results import MetricRule, evaluate_metrics, parse_result_file
from .script_gen import ModelSpec, ScriptGenerator
from .transactions import RunTransaction, confined_path
from .execution import Progress, commit_outputs
from .validation import validate_artifacts, validate_asset, validate_plan, validate_report
from .versioning import VersionStore
from .windows_control import WindowsAutomation
from .software_jobs_client import ReceiptStore, validate_state
from .transactions import file_hash
from .worker_protocol import SCHEMA as WORKER_SCHEMA, WorkerError, safe_file
from .cad_diff_view import visual_diff
from .software_preflight import preflight_action as run_action_preflight, preflight_plan
from .file_watch import FileWatchStore
from .dependencies import DependencyScanner
from .autocad_backend import AutoCADError, backend_status, run_core_console
from .dxf_validation import inspect_native
from .viewer import asset_view, render_asset_export_html
from .capability_matrix import build_capability_matrix
from .runtime_config import merge as merge_runtime_config


def _project_path(project_root: Path, value: str | Path, *, must_exist: bool = False,
                  require_file: bool = False) -> Path:
    """Resolve a user path inside a project while rejecting traversal/symlinks.

    Transaction destinations are allowed to be absolute for the Python API and
    CLI, but they still have to resolve below ``project_root``.  Passing the
    normalized relative path through :func:`confined_path` keeps the same
    symlink and reserved-path policy used by the transaction layer.
    """
    root = project_root.resolve()
    raw = Path(value)
    if ".." in raw.parts:
        raise ValueError("PATH_OUTSIDE_PROJECT")
    lexical = raw if raw.is_absolute() else root / raw
    if lexical == root or not lexical.is_relative_to(root):
        raise ValueError("PATH_OUTSIDE_PROJECT")
    cursor = lexical
    while cursor != root and root in cursor.parents:
        if cursor.is_symlink():
            raise ValueError("TRANSACTION_SYMLINK_PATH")
        cursor = cursor.parent
    candidate = lexical.resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise ValueError("PATH_OUTSIDE_PROJECT")
    relative = candidate.relative_to(root).as_posix()
    target = confined_path(root, relative)
    if must_exist and not target.exists():
        raise FileNotFoundError(target)
    if require_file and not target.is_file():
        raise ValueError("BREP_INPUT_NOT_A_FILE:" + str(target))
    return target


def _validate_brep_inspection(inspection: dict[str, Any], *, operation: str) -> dict[str, Any]:
    """Validate the persisted STEP shape independently of the writer result."""
    errors: list[str] = []
    if not isinstance(inspection, dict):
        errors.append("INSPECTION_NOT_OBJECT")
        return {"valid": False, "errors": errors}
    if inspection.get("is_null"):
        errors.append("NULL_SHAPE")
    if inspection.get("valid") is not True:
        errors.append("INVALID_SHAPE")
    topology = inspection.get("topology") or {}
    topology_count = sum(int(value or 0) for value in topology.values() if isinstance(value, (int, float)))
    if topology_count <= 0:
        errors.append("EMPTY_TOPOLOGY")
    solid_count = sum(int(topology.get(key, 0) or 0) for key in ("solid", "compsolid"))
    if solid_count <= 0:
        errors.append("NO_SOLID")
    try:
        volume = float(inspection.get("volume", 0.0))
        if volume < 0 or volume != volume or volume in {float("inf"), float("-inf")}:
            errors.append("INVALID_VOLUME")
    except (TypeError, ValueError):
        errors.append("INVALID_VOLUME")
    return {"valid": not errors, "errors": errors, "operation": operation,
            "topology_count": topology_count, "solid_count": solid_count, "inspection": inspection}


class EngineeringAgent:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.planner = AutonomousPlanner(self.config)
        self.adapters: dict[Any, ToolAdapter] = build_adapters(self.config)
        self.windows = WindowsAutomation(self.config.get("windows", {}))
        self.reader = ArtifactReader()
        self.operations = OperationRegistry()
        self.cad = CadOperationEngine(self.config)

    def apply_runtime_config(self, override: dict[str, Any]) -> dict[str, Any]:
        """Apply validated connection overrides and rebuild dependent adapters.

        Runtime settings are intentionally limited to non-secret connection
        fields.  Rebuilding the planner, adapters and Windows helper makes a
        saved Web setting effective for the current process without requiring
        a restart; action bindings and policy remain in the original config.
        """
        self.config = merge_runtime_config(self.config, override)
        self.planner = AutonomousPlanner(self.config)
        self.adapters = build_adapters(self.config)
        self.windows = WindowsAutomation(self.config.get("windows", {}))
        self.cad = CadOperationEngine(self.config)
        return self.config

    def plan(self, prompt: str, *, project_dir: str = ".", parameters: dict[str, Any] | None = None) -> ExecutionPlan:
        return self.planner.plan(AgentRequest(prompt, project_dir, parameters or {}))

    def preflight(self, prompt: str, *, project_dir: str = ".", parameters: dict[str, Any] | None = None,
                  probe: bool = False) -> dict[str, Any]:
        """Check the selected vendor actions without starting a software process."""
        plan = self.plan(prompt, project_dir=project_dir, parameters=parameters)
        validation = validate_plan(plan, self.operations)
        result = preflight_plan(self.adapters, plan, project_dir, probe=probe, plan_validation=validation)
        result["ready"] = bool(validation["valid"]) and result["ready"]
        if not validation["valid"]:
            result["issues"] = [{"code": "PLAN_INVALID", "message": error} for error in validation["errors"]]
        return result

    def preflight_action(self, tool: str, action: str, *, project_dir: str = ".",
                         parameters: dict[str, Any] | None = None,
                         inputs: list[str] | None = None, outputs: list[str] | None = None,
                         probe: bool = False) -> dict[str, Any]:
        """Check one explicitly selected software action without executing it."""
        return run_action_preflight(self.adapters, tool, action, project_dir,
                                    parameters=parameters, inputs=inputs,
                                    outputs=outputs, probe=probe)

    def run(self, prompt: str, *, project_dir: str = ".", parameters: dict[str, Any] | None = None,
            dry_run: bool = True, on_event=None, auto_repair: bool = False,
            repair_budget: int | None = None) -> RunReport:
        progress = Progress(on_event)
        with progress.stage("planning", "生成并检查计划"):
            plan = self.plan(prompt, project_dir=project_dir, parameters=parameters)
            plan_validation = validate_plan(plan)
            progress.emit("plan", plan.to_dict())
        if (not dry_run and plan_validation["valid"] and len(plan.steps) > 1
                and any(getattr(self.adapters[s.tool], "bridge_url", None) for s in plan.steps)):
            from .workflows import DurableWorkflow
            workflow = DurableWorkflow(self, project_dir, on_event)
            report = workflow.start(plan)
            if auto_repair:
                repair = {"requested": True, "parent_workflow_id": report.workflow_id}
                if report.status != "committed":
                    repair.update({"status": "deferred", "reason": f"工程链状态为 {report.status}，等待稳定结果后再修复"})
                else:
                    try:
                        configured_budget = repair_budget
                        if configured_budget is None:
                            configured_budget = (self.config.get('repair') or {}).get('auto_budget', 1)
                        if type(configured_budget) is not int or configured_budget < 1 or configured_budget > 8:
                            raise WorkerError('REPAIR_BUDGET_INVALID', 'budget must be an integer from 1 to 8')
                        if configured_budget == 1:
                            repair.update(workflow.repair(report.workflow_id))
                        else:
                            repair.update(workflow.repair_loop(report.workflow_id, configured_budget))
                    except Exception as exc:
                        # Preserve the completed parent report. A repair error
                        # must be visible to the caller without pretending the
                        # original vendor run failed.
                        repair.update({"status": "blocked", "error": {
                            "code": getattr(exc, "code", "AUTO_REPAIR_FAILED"),
                            "message": str(exc)}})
                report.validation["auto_repair"] = repair
            return report
        with RunTransaction(project_dir) as txn:
            txn.begin(plan.to_dict())
            txn.checkpoint("before-execution", {"plan_valid": plan_validation["valid"]})
            report = RunReport(plan, [], {}, txn.run_id, "", {"plan": plan_validation}, [])
            if not plan_validation["valid"]:
                txn.rollback("invalid execution plan")
                report.status, report.timeline = "failed", txn.events()
                return report
            staged_files = {}
            with progress.stage("prepare", "准备软件输入"):
                if self.config.get("generate_scripts", True):
                    staged_files = ScriptGenerator(project_dir, txn.stage_dir / "generated").generate(
                        ModelSpec(**(parameters or {}).get("model_spec", {})))
            context = AdapterContext(project_dir=Path(project_dir).resolve(), dry_run=dry_run,
                                     timeout_seconds=int(self.config.get("timeouts", {}).get("step_seconds", 600)),
                                     environment=self.config.get("environment", {}),
                                     workspace_dir=txn.stage_dir / "software", on_event=progress.emit)
            completed = set()
            software_outputs = {}
            for step in plan.steps:
                with progress.stage("tool:" + step.id, step.objective or step.action, step.tool.value) as stage:
                    missing = [dep for dep in step.depends_on if dep not in completed]
                    result = (ToolResult(step.id, step.tool, "blocked", "依赖步骤未完成: " + ", ".join(missing))
                              if missing else self.adapters[step.tool].execute(step, context))
                    report.results.append(result)
                    stage.update(status=result.status, message=result.message, inputs=step.inputs, outputs=result.outputs)
                    if result.status == "completed":
                        collected = result.details.get("output_files", {})
                        software_outputs.update(collected)
                        context.input_files.update(collected)
                        txn.record("software_outputs_verified", {"step_id": step.id,
                                   "manifest": result.details.get("output_manifest", [])})
                    if result.status == "completed" or (dry_run and result.status == "planned"):
                        completed.add(step.id)
            if any(r.status != "completed" and not (dry_run and r.status == "planned") for r in report.results):
                txn.rollback("one or more execution steps failed")
                report.status, report.timeline = "rolled_back", txn.events()
                return report
            with progress.stage("publish", "保存产物和历史副本"):
                metadata = self._stage_collected_jobs(project_dir, report.results, txn)
                report.generated_files, report.artifact_manifest = commit_outputs(txn, staged_files, software_outputs, metadata)
            report.status, report.timeline = "committed", txn.events()
            report.validation = {"plan": plan_validation, "report": validate_report(report),
                                 "artifacts": validate_artifacts(report.generated_files)}
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

    def run_offline_vehicle_chain(self, project_dir: str = ".", model_spec: dict[str, Any] | None = None, rules: list[dict[str, Any]] | None = None, *, on_event=None) -> dict[str, Any]:
        """Run the deterministic four-tool fixture chain without vendor software."""
        return run_offline_vehicle_chain(project_dir, model_spec, rules, on_event=on_event)

    def ingest(self, path: str, kind: str | None = None) -> dict[str, Any]:
        return self.reader.read(path, kind).to_dict()

    def inspect_image(self, root: str | Path, path: str, region: list[int] | None = None,
                      max_size: int = 1024, rotate: int = 0) -> dict[str, Any]:
        """Read-only raster crop for agent visual inspection."""
        return inspect_raster_image(root, path, region, max_size, rotate)

    def measure_image(self, root: str | Path, path: str, *, region: list[int] | None = None,
                      target_color: list[int] | None = None, tolerance: int = 16,
                      alpha_threshold: int = 1, max_size: int = 1024, overlay: bool = True,
                      coordinate_system: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only deterministic color measurement with optional overlay."""
        return measure_raster_image(root, path, region=region, target_color=target_color,
                                    tolerance=tolerance, alpha_threshold=alpha_threshold,
                                    max_size=max_size, overlay=overlay,
                                    coordinate_system=coordinate_system)

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
            relative = destination.relative_to(project_root).as_posix()
            staged_files = {relative: str(staged)}
            if format_name.lower() == "dxf":
                cad_document = CadDocument.open(staged)
                validation = validate_cad_document(cad_document)
                if not validation["valid"]:
                    txn.rollback("CAD_VALIDATION_FAILED")
                    raise ValueError("CAD_VALIDATION_FAILED:" + json.dumps(validation["errors"], ensure_ascii=False))
                txn.checkpoint("after-cad-validation", {"valid": True, "entity_count": len(cad_document.entities)})
                sidecar = Path(str(staged) + ".cad.json")
                sidecar.write_text(json.dumps(cad_document.sidecar(staged), ensure_ascii=False, indent=2), encoding="utf-8")
                staged_files[relative + ".cad.json"] = str(sidecar)
            validator = None
            if format_name.lower() == "dxf":
                CadHistory(txn, destination).prepare_edit(staged_files, "artifact.write")
                def validator(_files):
                    persisted_validation = self.cad._validate_persisted(cad_document, destination)
                    if not persisted_validation["valid"]:
                        raise ValueError("CAD_PERSISTED_VALIDATION_FAILED:" + json.dumps(persisted_validation.get("errors", []), ensure_ascii=False))
                    txn.record("cad_persisted_validation", persisted_validation)
            committed = txn.commit(staged_files, destination_root=project_root, validate=validator)
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
        if format_name == "glb":
            return write_glb(path, payload.get("vertices", []), payload.get("faces", []),
                             colors=payload.get("colors"), scalar_name=payload.get("scalar_name"),
                             scalar_range=payload.get("scalar_range"), name=payload.get("name", "engineering_agent"))
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

    def _autocad_config(self) -> dict[str, Any]:
        """Resolve the optional AutoCAD backend config without requiring it."""
        configured = self.config.get("autocad")
        if isinstance(configured, dict):
            return configured
        bridge = self.config.get("windows_bridge")
        if isinstance(bridge, dict):
            nested = bridge.get("autocad")
            if isinstance(nested, dict):
                return nested
            return bridge
        return {}

    @staticmethod
    def _autocad_local_validation(path: Path) -> dict[str, Any]:
        if path.suffix.lower() in {".dxf", ".dxb"}:
            return inspect_native(path)
        return {"valid": None, "available": False, "errors": [],
                "warnings": ["DWG_LOCAL_AUDIT_REQUIRES_AUTOCAD"]}

    def _autocad_operation(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        config = self._autocad_config()
        if name == "autocad.status":
            return backend_status(arguments.get("config") if isinstance(arguments.get("config"), dict) else config)
        project_root = Path(arguments.get("project_dir", ".")).resolve()
        input_path = _project_path(project_root, arguments["input_file"], must_exist=True, require_file=True) if "input_file" in arguments else None
        if name == "autocad.audit":
            local = self._autocad_local_validation(input_path)
            native = {"requested": bool(arguments.get("native", False)), "status": "not_requested"}
            if native["requested"]:
                try:
                    native = {"requested": True, "status": "completed",
                              "evidence": run_core_console(input_path, action="audit", config=config,
                                                            timeout_seconds=int(arguments.get("timeout_seconds", 600)))}
                except AutoCADError as exc:
                    native = {"requested": True, "status": "failed", "code": exc.code,
                              "message": str(exc), "details": exc.details}
                    raise
            return {"schema_version": "simloom-ai/autocad-audit/v1",
                    "path": input_path.relative_to(project_root).as_posix(),
                    "sha256": file_hash(input_path), "local_validation": local, "native": native}
        if name == "autocad.convert":
            output_path = _project_path(project_root, arguments["output_file"])
            if input_path == output_path:
                raise AutoCADError("AUTOCAD_IN_PLACE_UNSUPPORTED", "AutoCAD conversion requires a distinct output path")
            if bool(arguments.get("dry_run", True)):
                return {"schema_version": "simloom-ai/autocad-convert/v1", "status": "planned",
                        "input": input_path.relative_to(project_root).as_posix(),
                        "output": output_path.relative_to(project_root).as_posix(),
                        "audit": bool(arguments.get("audit", True)),
                        "backend": backend_status(config), "real_machine_verified": False}
            txn = RunTransaction(project_root)
            txn.begin({"operation": name, "arguments": arguments})
            try:
                staged = txn.stage_dir / output_path.name
                evidence = run_core_console(input_path, action="convert", output_path=staged, config=config,
                                            timeout_seconds=int(arguments.get("timeout_seconds", 600)),
                                            audit=bool(arguments.get("audit", True)))
                staged_validation = self._autocad_local_validation(staged)
                txn.record("autocad_staged_validation", staged_validation)
                txn.checkpoint("after-autocad-convert", {"validation": staged_validation, "evidence": evidence})
                if staged_validation.get("valid") is False and staged_validation.get("errors"):
                    raise AutoCADError("AUTOCAD_OUTPUT_VALIDATION_FAILED", "converted output failed local validation",
                                       details={"validation": staged_validation})
                relative = output_path.relative_to(project_root).as_posix()
                persisted: dict[str, Any] = {}

                def validate_persisted(_committed: dict[str, str]):
                    report = self._autocad_local_validation(output_path)
                    persisted["validation"] = report
                    txn.record("autocad_persisted_validation", report)
                    if report.get("valid") is False and report.get("errors"):
                        raise AutoCADError("AUTOCAD_PERSISTED_VALIDATION_FAILED", "persisted output failed local validation",
                                           details={"validation": report})

                committed = txn.commit({relative: str(staged)}, destination_root=project_root, validate=validate_persisted)
                return {"schema_version": "simloom-ai/autocad-convert/v1", "status": "completed",
                        "output": committed[relative], "native": evidence,
                        "validation": staged_validation, "persisted_validation": persisted["validation"],
                        "run_id": txn.run_id, "real_machine_verified": False}
            except Exception as exc:
                txn.fail(str(exc))
                raise
        raise ValueError(f"UNKNOWN_AUTOCAD_OPERATION:{name}")

    def execute_operation(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        name, arguments, _was_compat_alias = normalize_operation(name, arguments)
        spec = self.operations.get(name)
        if name.startswith(("cad.", "brep.")):
            arguments = normalize_cad_arguments(arguments, schema=spec.parameters_schema)
        schema_errors = self.operations.validate_arguments(name, arguments)
        if schema_errors:
            # Preserve the CAD operation's stable domain error codes for
            # parameters whose geometric implementation performs a stricter
            # check (for example, a non-positive fillet radius).  The
            # published schema still rejects the value for generic callers,
            # while Python/MCP clients receive the actionable CAD diagnosis.
            if name in {"cad.entity_fillet", "cad.entity_chamfer"}:
                if any(error.startswith("tolerance:") for error in schema_errors):
                    raise ValueError("INVALID_CORNER_TOLERANCE")
                if name.endswith("entity_fillet") and any(error.startswith("radius:") for error in schema_errors):
                    raise ValueError("INVALID_FILLET_RADIUS")
                if name.endswith("entity_chamfer") and any(error.startswith("distance:") for error in schema_errors):
                    raise ValueError("INVALID_CHAMFER_DISTANCE")
            if name == "cad.entity_remove_duplicates" and any(error.startswith("tolerance:") for error in schema_errors):
                raise ValueError("DUPLICATE_TOLERANCE_INVALID")
            if name == "cad.edit_hatch" and any(error.startswith("pattern_scale:") for error in schema_errors):
                raise ValueError("HATCH_PATTERN_SCALE_INVALID")
            if name == "cad.set_annotation_scale" and any(error.startswith("scale:") for error in schema_errors):
                raise ValueError("ANNOTATION_SCALE_INVALID")
            if name == "cad.rectangle_create" and any(error.startswith(("width:", "height:")) for error in schema_errors):
                raise ValueError("RECTANGLE_SIZE_INVALID")
            if name == "cad.polygon_create":
                if any(error.startswith("sides:") for error in schema_errors):
                    raise ValueError("POLYGON_SIDES_INVALID")
                if any(error.startswith("radius:") for error in schema_errors):
                    raise ValueError("POLYGON_RADIUS_INVALID")
            if name == "cad.donut_create" and any(error.startswith(("inner_radius:", "outer_radius:")) for error in schema_errors):
                raise ValueError("DONUT_RADII_INVALID")
            raise ValueError("INVALID_OPERATION_ARGUMENTS:" + ",".join(schema_errors))
        if name == "results.parse":
            return self.ingest(arguments["result_file"], "result")
        if name == "results.evaluate":
            rules = [MetricRule(**rule) for rule in arguments["rules"]]
            return {"verdicts": self.evaluate(arguments["result_file"], rules)}
        if name == "project.inspect":
            return self.inspect_project(arguments.get("project_dir", "."))
        if name == "project.preflight":
            return self.preflight(arguments["prompt"], project_dir=arguments.get("project_dir", "."),
                                  parameters=arguments.get("parameters"), probe=bool(arguments.get("probe", False)))
        if name == "project.action_preflight":
            return self.preflight_action(arguments["tool"], arguments["action"],
                                         project_dir=arguments.get("project_dir", "."),
                                         parameters=arguments.get("parameters"),
                                         inputs=arguments.get("inputs"), outputs=arguments.get("outputs"),
                                         probe=bool(arguments.get("probe", False)))
        if name == "project.version":
            return self.create_version(arguments.get("project_dir", "."), arguments["label"], arguments.get("paths", []), arguments.get("parent"))
        if name == "project.version_export":
            return VersionStore(arguments.get("project_dir", ".")).export(arguments["version_id"], arguments["output_dir"])
        if name == "project.watch_start":
            return FileWatchStore(arguments.get("project_dir", ".")).start(arguments["paths"], label=arguments.get("label", ""))
        if name == "project.watch_poll":
            return FileWatchStore(arguments.get("project_dir", ".")).poll(arguments["watch_id"], since=arguments.get("since"))
        if name == "project.watch_status":
            return FileWatchStore(arguments.get("project_dir", ".")).status(arguments["watch_id"])
        if name == "project.watch_stop":
            return FileWatchStore(arguments.get("project_dir", ".")).stop(arguments["watch_id"])
        if name == "project.dependency_scan":
            return DependencyScanner(arguments.get("project_dir", ".")).scan(arguments.get("paths"))
        if name == "project.diff":
            return self.diff_versions(arguments.get("project_dir", "."), arguments["left"], arguments["right"])
        if name == "project.visual_diff":
            store = VersionStore(arguments.get("project_dir", "."))
            left_version, right_version = store.load(arguments["left"]), store.load(arguments["right"])
            def drawing(version):
                for asset in version.get("objects", []):
                    geometry = asset.get("geometry") or {}
                    if str(geometry.get("format", "")).upper() == "DXF" and geometry.get("entities") is not None:
                        return geometry
                return None
            left_geometry, right_geometry = drawing(left_version), drawing(right_version)
            if left_geometry is None or right_geometry is None:
                raise ValueError("VERSION_DXF_REQUIRED")
            return {"left": arguments["left"], "right": arguments["right"],
                    "diff": store.diff(arguments["left"], arguments["right"]),
                    "visual_diff": visual_diff(left_geometry, right_geometry,
                                                width=max(320, min(int(arguments.get("width", 900)), 2400)),
                                                height=max(240, min(int(arguments.get("height", 560)), 1600)))}
        if name == "project.extract_requirements":
            from .requirements import extract_requirements
            root = Path(arguments.get("project_dir", ".")).resolve()
            document = _project_path(root, arguments["document"], must_exist=True, require_file=True)
            return extract_requirements(document, overrides=arguments.get("overrides"))
        if name == "project.asset_export":
            project_root = Path(arguments.get("project_dir", ".")).resolve()
            input_path = _project_path(project_root, arguments["input_file"], must_exist=True, require_file=True)
            output_path = _project_path(project_root, arguments["output_file"])
            if output_path.suffix.lower() not in {".html", ".htm"}:
                raise ValueError("VIEW_EXPORT_OUTPUT_MUST_BE_HTML")
            input_relative = input_path.relative_to(project_root).as_posix()
            output_relative = output_path.relative_to(project_root).as_posix()
            input_before = file_hash(input_path)
            view = asset_view(project_root, input_relative)
            if file_hash(input_path) != input_before:
                raise RuntimeError("VIEW_EXPORT_INPUT_CHANGED")
            html = render_asset_export_html(
                input_relative,
                view,
                title=arguments.get("title"),
                include_metadata=bool(arguments.get("include_metadata", True)),
            )
            if "simloom-ai/view-export/v1" not in html or "<svg" not in html:
                raise ValueError("VIEW_EXPORT_RENDER_INVALID")
            if str(project_root) in html:
                raise ValueError("VIEW_EXPORT_LEAKED_PROJECT_PATH")
            # The SVG namespace is intrinsic XML syntax; actual network-backed
            # resource attributes are forbidden so the file remains portable.
            import re
            if re.search(r"(?is)(?:src|href)\s*=\s*['\"]https?://", html):
                raise ValueError("VIEW_EXPORT_EXTERNAL_RESOURCE")
            transaction = RunTransaction(project_root)
            transaction.begin({"operation": name, "arguments": arguments,
                               "input_sha256": input_before})
            try:
                staged = transaction.stage_dir / output_path.name
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_text(html, encoding="utf-8", newline="\n")
                staged_hash = file_hash(staged)
                validation = {
                    "valid": True,
                    "schema_version": "simloom-ai/view-export/v1",
                    "has_svg": "<svg" in html,
                    "has_timeline": "修改记录" in html,
                    "has_metadata": bool(arguments.get("include_metadata", True)) and "simloom-export" in html,
                    "self_contained": True,
                    "input_sha256": input_before,
                    "output_sha256": staged_hash,
                }
                transaction.record("viewer_staged_validation", validation)
                transaction.checkpoint("after-view-export", validation)
                persisted: dict[str, Any] = {}

                def validate_persisted(committed_files: dict[str, str]):
                    persisted_path = Path(committed_files[output_relative])
                    content = persisted_path.read_text(encoding="utf-8")
                    if file_hash(persisted_path) != staged_hash:
                        raise RuntimeError("VIEW_EXPORT_OUTPUT_CHANGED")
                    if "simloom-ai/view-export/v1" not in content or "<svg" not in content:
                        raise ValueError("VIEW_EXPORT_PERSISTED_INVALID")
                    if str(project_root) in content:
                        raise ValueError("VIEW_EXPORT_LEAKED_PROJECT_PATH")
                    persisted.update({"valid": True, "sha256": file_hash(persisted_path), "bytes": persisted_path.stat().st_size})
                    transaction.record("viewer_persisted_validation", persisted)

                committed = transaction.commit({output_relative: str(staged)}, destination_root=project_root,
                                               validate=validate_persisted,
                                               expected_before={output_relative: file_hash(output_path)})
                return {"status": "completed", "output": committed[output_relative],
                        "bytes": Path(committed[output_relative]).stat().st_size,
                        "sha256": file_hash(Path(committed[output_relative])),
                        "validation": {**validation, "persisted": persisted},
                        "run_id": transaction.run_id}
            except Exception as exc:
                transaction.fail(str(exc))
                raise
        if name.startswith("autocad."):
            return self._autocad_operation(name, arguments)
        if name == "artifact.write":
            return {"path": self.write_asset(arguments["path"], arguments["format"], arguments["payload"], project_dir=arguments.get("project_dir"))}
        if name.startswith("cad."):
            return self.cad.apply(name, arguments)
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
            input_path = arguments["input_file"]
            if arguments.get("project_dir") is not None:
                input_path = _project_path(Path(arguments["project_dir"]).resolve(), input_path,
                                           must_exist=True, require_file=True)
            return {"brep": get_brep_backend().inspect_step(input_path)}
        if name == "brep.tessellate_step":
            project_root = Path(arguments.get("project_dir", ".")).resolve()
            input_path = _project_path(project_root, arguments["input_file"], must_exist=True, require_file=True)
            output_path = _project_path(project_root, arguments["output_file"])
            if output_path.suffix.lower() not in {".obj", ".glb"}:
                raise ValueError("BREP_MESH_OUTPUT_MUST_BE_OBJ_OR_GLB")
            backend = get_brep_backend()
            txn = RunTransaction(project_root)
            txn.begin({"operation": name, "arguments": arguments})
            try:
                staged = txn.stage_dir / output_path.name
                mesh = backend.tessellate_step(input_path, float(arguments.get("deflection", 0.5)))
                vertices = mesh.get("vertices") or []
                faces = mesh.get("faces") or []
                vertex_values = ([point.get("x", 0), point.get("y", 0), point.get("z", 0)] for point in vertices)
                if output_path.suffix.lower() == ".glb":
                    write_glb(staged, vertex_values, faces, name="simloom-step-tessellation")
                else:
                    write_obj(staged, vertex_values, faces, name="simloom-step-tessellation")
                staged_mesh = read_mesh(staged)
                validation = validate_geometry_object({"kind": "geometry", "geometry": staged_mesh})
                txn.record("mesh_staged_validation", validation)
                txn.checkpoint("after-step-tessellation", {"validation": validation, "mesh": {"vertex_count": staged_mesh.get("vertex_count"), "face_count": staged_mesh.get("face_count")}})
                if not validation["valid"]:
                    raise ValueError("BREP_MESH_VALIDATION_FAILED:" + ",".join(validation["errors"]))
                relative = output_path.relative_to(project_root).as_posix()
                persisted: dict[str, Any] = {}

                def validate_persisted(committed_files: dict[str, str]):
                    persisted_mesh = read_mesh(committed_files[relative])
                    persisted_validation = validate_geometry_object({"kind": "geometry", "geometry": persisted_mesh})
                    persisted.update(mesh=persisted_mesh, validation=persisted_validation)
                    txn.record("mesh_persisted_validation", persisted_validation)
                    if not persisted_validation["valid"]:
                        raise ValueError("BREP_MESH_PERSISTED_VALIDATION_FAILED:" + ",".join(persisted_validation["errors"]))

                committed = txn.commit({relative: str(staged)}, destination_root=project_root, validate=validate_persisted)
                return {"output": committed[relative], "mesh": persisted["mesh"], "validation": validation,
                        "persisted_validation": persisted["validation"], "run_id": txn.run_id}
            except Exception as exc:
                txn.fail(str(exc))
                raise
        if name in {"brep.boolean", "brep.create_primitive", "brep.build_feature_graph"}:
            project_root = Path(arguments.get("project_dir", ".")).resolve()
            output_path = _project_path(project_root, arguments["output_file"])
            input_paths: dict[str, Path] = {}
            if name == "brep.boolean":
                input_paths = {
                    "left_file": _project_path(project_root, arguments["left_file"], must_exist=True, require_file=True),
                    "right_file": _project_path(project_root, arguments["right_file"], must_exist=True, require_file=True),
                }
            txn = RunTransaction(project_root)
            txn.begin({"operation": name, "arguments": arguments})
            try:
                staged = txn.stage_dir / output_path.name
                backend = get_brep_backend()
                if name == "brep.boolean":
                    result = backend.boolean(arguments["operation"], input_paths["left_file"], input_paths["right_file"], staged)
                elif name == "brep.create_primitive":
                    result = backend.create_primitive(arguments["primitive"], arguments.get("parameters", {}), staged)
                else:
                    result = backend.build_feature_graph(arguments.get("features") or [], arguments.get("parameters") or {}, arguments.get("constraints") or [], staged)
                staged_inspection = result.get("inspection")
                staged_validation = _validate_brep_inspection(staged_inspection, operation=name)
                txn.record("brep_staged_validation", staged_validation)
                txn.checkpoint("after-brep-write", staged_validation)
                if not staged_validation["valid"]:
                    raise ValueError("BREP_VALIDATION_FAILED:" + ",".join(staged_validation["errors"]))
                relative = output_path.relative_to(project_root).as_posix()
                staged_files = {relative: str(staged)}
                if name == "brep.build_feature_graph":
                    manifest = Path(str(staged) + ".brep.json")
                    manifest.write_text(json.dumps({"schema_version": "simloom-ai/brep-feature-graph/v1", "features": result.get("features", []), "constraints": result.get("constraints", {}), "final_feature": result.get("final_feature")}, ensure_ascii=False, indent=2), encoding="utf-8")
                    staged_files[relative + ".brep.json"] = str(manifest)
                else:
                    # A previous feature-graph sidecar must not survive when
                    # the same STEP path is replaced by a primitive/boolean.
                    staged_files[relative + ".brep.json"] = None
                persisted: dict[str, Any] = {}

                def validate_persisted(committed_files: dict[str, str]):
                    inspection = backend.inspect_step(committed_files[relative])
                    validation = _validate_brep_inspection(inspection, operation=name)
                    txn.record("brep_persisted_validation", validation)
                    persisted.update({"inspection": inspection, "validation": validation})
                    if not validation["valid"]:
                        raise ValueError("BREP_PERSISTED_VALIDATION_FAILED:" + ",".join(validation["errors"]))

                committed = txn.commit(staged_files, destination_root=project_root, validate=validate_persisted)
                result["output"] = committed[relative]
                if relative + ".brep.json" in committed:
                    result["feature_graph"] = committed[relative + ".brep.json"]
                result["inspection"] = persisted["inspection"]
                result["persisted_validation"] = persisted["validation"]
                return result
            except Exception as exc:
                txn.fail(str(exc))
                raise
        if name == "geometry.dxf_transform":
            edited = self.cad.apply("cad.batch_apply", {
                **arguments,
                "operations": [{"operation": "cad.entity_transform", "all_entities": True,
                                "translate": arguments.get("translate", (0, 0, 0)),
                                "scale": float(arguments.get("scale", 1.0)),
                                "angle": float(arguments.get("rotate_deg", 0.0))}],
            })
            geometry = dict(edited["document"])
            geometry["format"] = "DXF"
            return {"path": edited["path"], "geometry": geometry, "diff": edited["diff"], "validation": edited["validation"], "persisted_validation": edited.get("persisted_validation"), "timeline": edited["timeline"]}
        action_alias = {"catia.create_model": "create_or_update_model", "vi-grade.run_scenario": "run_vehicle_dynamics"}
        step = PlanStep("operation", spec.tool, action_alias.get(name, name.split(".", 1)[1]), spec.description,
                        inputs=arguments.get("inputs", []), outputs=arguments.get("outputs", []), parameters=arguments)
        context = AdapterContext(project_dir=Path(arguments.get("project_dir", ".")).resolve(), dry_run=bool(arguments.get("dry_run", True)), timeout_seconds=int(self.config.get("timeouts", {}).get("step_seconds", 600)), environment=self.config.get("environment", {}))
        if context.dry_run:
            return self.adapters[spec.tool].execute(step, context).to_dict()
        with RunTransaction(context.project_dir) as txn:
            txn.begin({"operation": name, "arguments": arguments})
            context.workspace_dir = txn.stage_dir / "software"
            result = self.adapters[spec.tool].execute(step, context)
            if result.status != "completed":
                txn.rollback(result.message)
            else:
                metadata = self._stage_collected_jobs(context.project_dir, [result], txn)
                files, receipts = commit_outputs(txn, {}, result.details.get("output_files", {}), metadata)
                result.details.update(published_files=files, artifact_manifest=receipts)
            result.details.update(run_id=txn.run_id, transaction_status=txn.state)
            return result.to_dict()

    @staticmethod
    def _stage_collected_jobs(project_dir, results, transaction):
        store = ReceiptStore(project_dir)
        staged = {}
        for result in results:
            remote = result.details.get("remote_job")
            if result.status == "completed" and remote:
                staged.update(store.stage_collected(remote["request_id"], transaction,
                                                    result.details.get("output_manifest", [])))
        return staged

    def workflow(self, action: str = "list", workflow_id: str | None = None, *, project_dir: str = ".",
                 on_event=None, repair_budget: int | None = None):
        from .workflows import DurableWorkflow, WorkflowStore
        if action == "resume":
            return DurableWorkflow(self, project_dir, on_event).resume(workflow_id).to_dict()
        if action == "repair":
            workflow = DurableWorkflow(self, project_dir, on_event)
            budget = repair_budget
            if budget is None:
                budget = (self.config.get('repair') or {}).get('auto_budget', 1)
            if type(budget) is not int or budget < 1 or budget > 8:
                raise WorkerError('REPAIR_BUDGET_INVALID', 'budget must be an integer from 1 to 8')
            return workflow.repair(workflow_id) if budget == 1 else workflow.repair_loop(workflow_id, budget)
        store = WorkflowStore(project_dir)
        if action not in {"list", "status"}:
            raise WorkerError("UNKNOWN_WORKFLOW_OPERATION")
        return {"workflows": store.list()} if action == "list" else store.summary(store.read(workflow_id))

    def software_job(self, tool: str, action: str = "status", request_id: str | None = None,
                     *, project_dir: str = ".", **options) -> dict[str, Any]:
        """Inspect/reconnect software jobs without resubmitting an engineering operation."""
        adapter = self.adapters[ToolName(tool)]
        if not getattr(adapter, "bridge_url", None):
            raise WorkerError("REMOTE_WORKER_NOT_CONFIGURED", "请先配置该软件的远程工作站")
        if action == "receipts":
            return {"jobs": [r for r in ReceiptStore(project_dir).list() if r["tool"] == tool]}
        if action not in {"list", "status", "logs", "events", "cancel", "release", "collect"}:
            raise WorkerError("UNKNOWN_JOB_OPERATION")
        if action == "collect":
            return self._collect_software_job(adapter, project_dir, request_id)
        allowed = {"limit", "cursor", "after", "offset", "stream", "confirmed_stopped", "reason"}
        payload = {"schema_version": WORKER_SCHEMA, "operation": "job." + action,
                   "request_id": request_id, "tool": tool, **{k: v for k, v in options.items() if k in allowed}}
        return adapter._post(payload, 10)

    def _collect_software_job(self, adapter, project_dir, request_id):
        store = ReceiptStore(project_dir)
        with RunTransaction(project_dir) as txn:
            txn.begin({"operation": "software.job.collect", "request_id": request_id})
            receipt = store.read(request_id)
            if receipt.get("workflow_id") and not receipt.get("collected"):
                raise WorkerError("WORKFLOW_COLLECTION_REQUIRED", "请恢复整条工程链：" + receipt["workflow_id"])
            if receipt["tool"] != adapter.name.value or receipt["bridge_url"] != adapter.bridge_url:
                raise WorkerError("JOB_CONNECTION_CHANGED")
            if receipt.get("collected"):
                for output in receipt.get("output_manifest", []):
                    if file_hash(safe_file(store.project, output["path"])) != output["sha256"]:
                        raise WorkerError("PUBLISHED_RESULT_CHANGED", output["path"])
                txn.rollback("Job result was already collected")
                return {"status": "already_collected", "request_id": request_id,
                        "run_id": receipt.get("publication_run_id"), "outputs": receipt["outputs"]}
            state = adapter._post({"schema_version": WORKER_SCHEMA, "operation": "job.status", "request_id": request_id}, 10)
            validate_state(state, receipt)
            if state["status"] != "completed":
                raise WorkerError("JOB_NOT_COMPLETED", "任务尚未成功完成，无法回收结果：" + state["status"])
            for name, before in receipt["before"].items():
                if file_hash(safe_file(store.project, name)) != before:
                    raise WorkerError("PROJECT_OUTPUT_CHANGED", "项目文件在任务提交后已修改，未覆盖：" + name)
            body = adapter._post({"schema_version": WORKER_SCHEMA, "operation": "job.result", "request_id": request_id}, 30)
            if body.get("request_digest") != receipt["request_digest"]:
                raise WorkerError("WORKER_RESPONSE_MISMATCH")
            step = PlanStep("collect", adapter.name, receipt["action"], "Collect an existing software job", outputs=receipt["outputs"],
                            parameters={"validation": receipt.get("validation")} if receipt.get("validation") else {})
            workspace = txn.stage_dir / "collected"
            workspace.mkdir()
            result = adapter.collect_response(step, body, request_id, receipt["outputs"], workspace, receipt.get("validation"))
            if result.status != "completed":
                raise WorkerError("JOB_RESULT_FAILED", result.message)
            metadata = store.stage_collected(request_id, txn, result.details["output_manifest"])
            files, manifest = commit_outputs(txn, {}, result.details["output_files"], metadata)
            return {"status": "collected", "request_id": request_id, "run_id": txn.run_id,
                    "generated_files": files, "artifact_manifest": manifest,
                    "vendor_validation": result.details.get("vendor_validation", "not_run"),
                    "verification": result.details.get("verification", "artifact_integrity")}

    @staticmethod
    def evaluate(result_file: str, rules: list[MetricRule]) -> list[dict[str, Any]]:
        data = parse_result_file(result_file)
        return [verdict.to_dict() for verdict in evaluate_metrics(data, rules)]

    def doctor(self, *, probe: bool = True) -> list[dict[str, Any]]:
        """Inspect configured backends, optionally probing remote workers.

        ``probe=False`` is useful for offline discovery and never sends a
        health request to a configured Windows worker. It still reports the
        local binding and marks worker-dependent actions as ``not_probed``.
        """
        return [self.adapters[name].doctor(probe=probe) for name in self.adapters] + [{"tool": "windows-com", **self.windows.doctor()}, {"tool": "windows-backends", **self.windows.validate_backends(self.config.get("tools", {}))}, {"tool": "step-brep", **brep_doctor()}]

    def capabilities(self, *, probe: bool = True) -> dict[str, Any]:
        """Return a readiness matrix without launching vendor software.

        ``probe`` controls only remote worker health/capability discovery; it
        never starts CATIA, Ansys, Adams or VI-grade. Keeping it explicit lets
        a disconnected MCP client inspect local declarations offline.
        """
        diagnostics = self.doctor(probe=probe)
        by_tool = {item.get("tool"): item for item in diagnostics if item.get("tool")}
        windows = by_tool.get("windows-com", {})
        brep = by_tool.get("step-brep", {})
        llm = self.config.get("llm", {})
        software = [by_tool.get(name, {}) for name in ("catia", "ansys", "adams", "vi-grade")]
        connected = any(item.get("reachable") or item.get("execution_ready") for item in software)
        executable_ready = any(item.get("execution_ready") for item in software)
        capability_matrix = build_capability_matrix(
            diagnostics, tool_order=("catia", "ansys", "adams", "vi-grade"), probe=probe)
        return {
            "schema_version": "simloom-ai/capabilities/v1",
            "capability_matrix": capability_matrix,
            "modes": [
                {"id": "offline", "ready": True, "label": "离线体验", "reason": "不需要商业软件"},
                {"id": "rehearsal", "ready": True, "label": "排练模式", "reason": "生成脚本并验证计划，不启动外部软件"},
                {"id": "connected", "ready": connected, "label": "软件连接", "reason": "按软件检查本机命令或远程 worker；控制端无需 Windows"},
                {"id": "execute", "ready": executable_ready, "label": "软件执行", "reason": "至少一个动作已有脚本绑定和可用启动程序；许可证与工程操作仍需实机验收"},
            ],
            "components": {
                "llm": {"enabled": bool(llm.get("enabled")), "provider": llm.get("provider", "deterministic")},
                "step_brep": {"available": bool(brep.get("available")), "backend": brep.get("backend", brep.get("name", "unavailable"))},
                "windows": {"available": bool(windows.get("windows") and windows.get("pywin32")), "platform": windows.get("platform")},
                "software_connection": {"available": connected, "real_machine_verified": False},
            },
            "tools": {name: by_tool.get(name, {}) for name in ("catia", "ansys", "adams", "vi-grade")},
        }

    def validate_windows_backends(self, *, smoke: bool = False) -> dict[str, Any]:
        return self.windows.validate_backends(self.config.get("tools", {}), smoke=smoke)

    def worker_evidence(self, tool: str, action: str = "list", *, request_id: str | None = None,
                        project_dir: str = ".", **metadata: Any) -> dict[str, Any]:
        """Read or record attested evidence on a configured Windows worker.

        Recording is intentionally delegated to the worker.  The controller
        cannot mark a local fixture or a COM probe as a real-machine result.
        """
        try:
            tool_name = ToolName(tool)
        except ValueError as exc:
            raise WorkerError("INVALID_SOFTWARE_ACTION", tool) from exc
        adapter = self.adapters.get(tool_name)
        if adapter is None or not getattr(adapter, "bridge_url", None):
            raise WorkerError("WINDOWS_WORKER_REQUIRED", tool)
        payload: dict[str, Any] = {"schema_version": WORKER_SCHEMA,
                                   "operation": "evidence.list" if action == "list" else "evidence.record"}
        if action == "list":
            if metadata.get("vendor_action"):
                payload["action"] = metadata["vendor_action"]
            payload["tool"] = tool
            if metadata.get("limit") is not None:
                payload["limit"] = metadata["limit"]
        elif action == "record":
            if not request_id:
                raise WorkerError("MACHINE_EVIDENCE_REQUEST_REQUIRED")
            payload.update({"evidence_schema": "simloom-ai/machine-evidence/v1", "request_id": request_id,
                            "tool": tool, "action": metadata.get("vendor_action") or metadata.get("action"),
                            "operator": metadata.get("operator"), "software_version": metadata.get("software_version"),
                            "license_status": metadata.get("license_status"), "smoke_test_id": metadata.get("smoke_test_id"),
                            "notes": metadata.get("notes", "")})
            payload = {key: value for key, value in payload.items() if value is not None}
        else:
            raise WorkerError("INVALID_EVIDENCE_ACTION", action)
        return adapter._post(payload, int(self.config.get("timeouts", {}).get("step_seconds", 600)))

    @staticmethod
    def load_config(path: str | Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
