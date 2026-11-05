"""Persistent projects and curated starting points for the local workbench."""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .formats import write_json
from .offline_workflow import DEFAULT_RULES
from .queue import TaskQueue
from .script_gen import ModelSpec
from .transactions import RunTransaction


TEMPLATES = [
    {"id": "offline-vehicle", "name": "四软件离线体验", "tools": ["catia", "ansys", "adams", "vi-grade"],
     "prompt": "用 CATIA 建立悬架模型，经 Ansys 检查应力和位移，再用 Adams 和 VI-grade 分析整车操稳。"},
    {"id": "suspension-structure", "name": "悬架结构分析", "tools": ["catia", "ansys"],
     "prompt": "用 CATIA 建立悬架模型，导入 Ansys 做静力分析，检查最大应力和位移。"},
    {"id": "suspension-dynamics", "name": "悬架动力学", "tools": ["catia", "adams"],
     "prompt": "用 CATIA 建立悬架模型，导入 Adams 做多体动力学分析。"},
    {"id": "vehicle-handling", "name": "整车操稳", "tools": ["adams", "vi-grade"],
     "prompt": "用 Adams 和 VI-grade 做阶跃转向、操稳和圈速分析。"},
    {"id": "custom", "name": "自定义流程", "tools": [], "prompt": ""},
]


class Workspace:
    def __init__(self, queue: TaskQueue, root: str | Path | None = None):
        self.queue = queue
        self.root = Path(root or Path(queue.path).resolve().parent / "projects").resolve()

    def create_project(self, body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("请输入 1–100 个字符的项目名称")
        name = name.strip()
        template_id = body.get("template", "offline-vehicle")
        template = next((t for t in TEMPLATES if t["id"] == template_id), None)
        if template is None:
            raise ValueError("未知的项目模板")
        parameters = body.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("项目参数必须为对象")
        spec = asdict(ModelSpec())
        # File/vendor identifiers stay portable even when the display name is Chinese.
        spec["name"] = "model_" + uuid.uuid4().hex[:8]
        for key, default in (("wheelbase_mm", 2800), ("track_front_mm", 1600), ("chassis_height_mm", 120), ("force_n", 8500)):
            raw = parameters.get(key, default)
            try:
                value = float(raw)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{key} 必须为正数") from exc
            if isinstance(raw, bool) or not math.isfinite(value) or not 0 < value <= 1_000_000:
                raise ValueError(f"{key} 必须在 0 到 1,000,000 之间")
            if key == "force_n":
                spec["load_cases"] = [{"name": "vertical_load", "force_n": value}]
            else:
                spec["parameters"][key] = value
        slug = re.sub(r"[^\w-]+", "-", name, flags=re.UNICODE).strip("-_")[:40] or "project"
        requested = body.get("project_dir")
        if requested is not None and not isinstance(requested, str):
            raise ValueError("项目目录必须为文本")
        root = Path(requested).expanduser().resolve() if requested else self.root / f"{slug}-{uuid.uuid4().hex[:8]}"
        metadata = {"schema_version": "simloom-ai/project/v1", "name": name,
                    "template": template_id, "tools": template["tools"], "prompt": template["prompt"],
                    "project_dir": str(root), "model_spec": spec, "rules": DEFAULT_RULES}
        with RunTransaction(root) as transaction:
            transaction.begin({"operation": "workspace.create_project", "name": name})
            if (root / "simloom.project.json").exists():
                raise FileExistsError("该目录已有 SimLoom 项目，请从项目历史打开")
            staged = transaction.stage_dir / "simloom.project.json"
            write_json(staged, metadata)
            transaction.commit({"simloom.project.json": str(staged)}, destination_root=root)
            try:
                project_id = self.queue.create_project(name, str(root), metadata)
            except Exception:
                transaction.rollback("project registration failed")
                raise
        return self.queue.get_project(project_id)
