from __future__ import annotations

"""Deterministic offline vertical slice for the SimLoom engineering chain.

The offline workflow is deliberately explicit about being a surrogate. It
creates real local artifacts and exercises the same transaction, validation and
versioning boundaries as a Windows vendor run, without pretending to solve a
CATIA/Ansys/Adams/VI-grade model.
"""

import math
from pathlib import Path
from typing import Any

from .brep import BRepUnavailable, get_brep_backend
from .formats import write_csv, write_json, write_step
from .results import MetricRule, evaluate_metrics
from .script_gen import ModelSpec, ScriptGenerator
from .transactions import RunTransaction
from .validation import hash_file
from .execution import Progress, commit_outputs


DEFAULT_RULES = [
    {"name": "max_stress", "path": "metrics.max_stress_mpa", "operator": "<=", "target": 250.0, "unit": "MPa"},
    {"name": "max_displacement", "path": "metrics.max_displacement_mm", "operator": "<=", "target": 5.0, "unit": "mm"},
    {"name": "mesh_quality", "path": "metrics.mesh_quality", "operator": ">=", "target": 0.85, "unit": "ratio"},
    {"name": "understeer_gradient", "path": "metrics.understeer_gradient_deg_per_g", "operator": "<=", "target": 5.0, "unit": "deg/g"},
]


def _output(stage_root: Path, relative: str) -> Path:
    path = stage_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _model_dimensions(spec: ModelSpec) -> tuple[float, float, float]:
    parameters = spec.parameters
    length = max(float(parameters.get("wheelbase_mm", 2800.0)), 1.0)
    width = max(float(parameters.get("track_front_mm", 1600.0)), 1.0)
    height = max(float(parameters.get("chassis_height_mm", 120.0)), 1.0)
    return length, width, height


def _create_model(stage_root: Path, spec: ModelSpec) -> tuple[str, dict[str, Any]]:
    path = _output(stage_root, "offline/model.step")
    length, width, height = _model_dimensions(spec)
    backend = get_brep_backend()
    if backend.name == "opencascade":
        inspection = backend.create_primitive("box", {"length": length, "width": width, "height": height}, path)["inspection"]
        return str(path), {"backend": backend.name, "inspection": inspection}
    # Keep the workflow runnable on a clean machine. This is a conservative
    # STEP fixture, not a fabricated B-Rep claim.
    points = [(0, 0, 0), (length, 0, 0), (0, width, 0), (0, 0, height), (length, width, height)]
    write_step(path, points, spec.name)
    return str(path), {"backend": "conservative-step", "inspection": None}


def _ansys_surrogate(stage_root: Path, spec: ModelSpec) -> tuple[str, dict[str, Any]]:
    length, width, height = _model_dimensions(spec)
    total_force = sum(float(case.get("force_n", 0.0)) for case in spec.load_cases)
    section = max(width * height, 1.0)
    stress = total_force / section * 1000.0
    displacement = stress / 210000.0 * length
    result = {
        "schema_version": "simloom-ai/offline-result/v1",
        "backend": "offline-surrogate",
        "tool": "ansys",
        "analysis_type": "static",
        "model": spec.name,
        "metrics": {
            "max_stress_mpa": round(stress, 6),
            "max_displacement_mm": round(displacement, 6),
            "mesh_quality": 0.92,
            "converged": True,
        },
        "assumptions": ["linear elastic steel surrogate", "uniform rectangular section", "deterministic offline fixture"],
    }
    path = _output(stage_root, "offline/ansys_result.json")
    write_json(path, result)
    return str(path), result


def _adams_surrogate(stage_root: Path, spec: ModelSpec) -> tuple[str, list[dict[str, float]]]:
    parameters = spec.parameters
    duration = max(float(parameters.get("simulation_end_time_s", 2.0)), 0.1)
    step = max(float(parameters.get("simulation_step_s", 0.05)), 0.001)
    rows: list[dict[str, float]] = []
    index = 0
    while index <= int(duration / step + 1e-9):
        time_s = round(index * step, 9)
        steering = 5.0 * math.sin(2.0 * math.pi * time_s / duration)
        yaw_rate = 0.42 * steering + 0.03 * math.sin(4.0 * math.pi * time_s / duration)
        lateral_accel = 0.8 * abs(yaw_rate)
        rows.append({"time_s": time_s, "steering_deg": round(steering, 6), "yaw_rate_deg_s": round(yaw_rate, 6), "lateral_accel_m_s2": round(lateral_accel, 6)})
        index += 1
    path = _output(stage_root, "offline/adams_motion.csv")
    write_csv(path, rows, ["time_s", "steering_deg", "yaw_rate_deg_s", "lateral_accel_m_s2"])
    return str(path), rows


def _vigrade_surrogate(stage_root: Path, spec: ModelSpec, motion: list[dict[str, float]]) -> tuple[str, dict[str, Any]]:
    peak_accel = max((row["lateral_accel_m_s2"] for row in motion), default=0.0)
    result = {
        "schema_version": "simloom-ai/offline-result/v1",
        "backend": "offline-surrogate",
        "tool": "vi-grade",
        "scenario": "step_steer",
        "model": spec.name,
        "metrics": {
            "yaw_rate_gain": 0.42,
            "understeer_gradient_deg_per_g": 2.1,
            "lateral_acceleration_m_s2": round(peak_accel, 6),
            "lap_time_s": 92.5,
        },
        "assumptions": ["deterministic motion fixture", "no tire or road model", "offline validation only"],
    }
    path = _output(stage_root, "offline/vigrade_metrics.json")
    write_json(path, result)
    return str(path), result


def run_offline_vehicle_chain(project_dir: str | Path, model_spec: dict[str, Any] | None = None, rules: list[dict[str, Any]] | None = None, *, on_event=None) -> dict[str, Any]:
    """Run a complete, deterministic CATIA→Ansys→Adams→VI-grade fixture chain."""
    progress = Progress(on_event)
    project_root = Path(project_dir).resolve()
    spec = ModelSpec(**(model_spec or {}))
    rule_data = rules if rules is not None else DEFAULT_RULES
    txn = RunTransaction(project_root)
    txn.begin({"operation": "workflow.offline_vehicle_chain", "model_spec": model_spec or {}, "rules": rule_data})
    try:
        stage_root = txn.stage_dir / "generated"
        progress.emit("plan", {"steps": [
            {"id": tool, "tool": tool, "action": "offline_surrogate", "depends_on": [previous] if previous else []}
            for tool, previous in (("catia", None), ("ansys", "catia"), ("adams", "ansys"), ("vi-grade", "adams"))]})
        with progress.stage("prepare", "准备软件输入"):
            generated = ScriptGenerator(project_root, stage_root).generate(spec)
        with progress.stage("catia", "生成离线 STEP 模型", "catia") as stage:
            model_path, geometry = _create_model(stage_root, spec)
            stage["outputs"] = ["offline/model.step"]
        with progress.stage("ansys", "生成离线结构示例", "ansys") as stage:
            ansys_path, ansys = _ansys_surrogate(stage_root, spec)
            stage["outputs"] = ["offline/ansys_result.json"]
        with progress.stage("adams", "生成离线运动曲线", "adams") as stage:
            motion_path, motion = _adams_surrogate(stage_root, spec)
            stage["outputs"] = ["offline/adams_motion.csv"]
        with progress.stage("vi-grade", "生成离线操稳指标", "vi-grade") as stage:
            vigrade_path, vigrade = _vigrade_surrogate(stage_root, spec, motion)
            stage["outputs"] = ["offline/vigrade_metrics.json"]
        # Rules describe the complete cross-tool engineering objective.  Keep
        # each vendor result in its own artifact, then expose a read-only
        # merged view to the independent validator so rules can reference
        # structural metrics from Ansys and vehicle metrics from VI-grade in
        # one report.
        combined = {
            "metrics": {
                **ansys.get("metrics", {}),
                **vigrade.get("metrics", {}),
            },
            "stages": {
                "ansys": ansys,
                "adams": {"motion_row_count": len(motion)},
                "vi-grade": vigrade,
            },
        }
        with progress.stage("validate", "检查工程指标"):
            verdicts = [verdict.to_dict() for verdict in evaluate_metrics(combined, [MetricRule(**item) for item in rule_data])]
        all_staged = {
            **generated,
            "offline/model.step": model_path,
            "offline/ansys_result.json": ansys_path,
            "offline/adams_motion.csv": motion_path,
            "offline/vigrade_metrics.json": vigrade_path,
        }
        report_path = _output(stage_root, "offline/report.json")
        report = {
            "schema_version": "simloom-ai/offline-workflow/v1",
            "workflow": "offline_vehicle_chain",
            "status": "validated",
            "simulation_backend": "offline-surrogate",
            "validation_input": "merged cross-tool metrics",
            "geometry": geometry,
            "stages": [
                {"tool": "catia", "status": "completed", "artifact": "offline/model.step"},
                {"tool": "ansys", "status": "completed", "artifact": "offline/ansys_result.json", "metrics": ansys["metrics"]},
                {"tool": "adams", "status": "completed", "artifact": "offline/adams_motion.csv", "row_count": len(motion)},
                {"tool": "vi-grade", "status": "completed", "artifact": "offline/vigrade_metrics.json", "metrics": vigrade["metrics"]},
            ],
            "verdicts": verdicts,
            "artifacts": {key: {"bytes": Path(value).stat().st_size, "sha256": hash_file(value)} for key, value in all_staged.items()},
        }
        write_json(report_path, report)
        all_staged["offline/report.json"] = str(report_path)
        with progress.stage("publish", "保存产物和历史副本"):
            committed, manifest = commit_outputs(txn, all_staged)
        report["status"] = "committed"
        return {"run_id": txn.run_id, "artifact_manifest": manifest, "workflow": report["workflow"], "status": report["status"], "simulation_backend": report["simulation_backend"], "geometry": geometry, "stages": report["stages"], "verdicts": verdicts, "artifacts": committed, "report": committed["offline/report.json"]}
    except Exception as exc:
        txn.fail(str(exc))
        raise
