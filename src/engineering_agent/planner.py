from __future__ import annotations

import re
from dataclasses import dataclass

from .models import AgentRequest, ExecutionPlan, PlanStep, ToolName


@dataclass(frozen=True)
class _Intent:
    geometry: bool = False
    structural: bool = False
    multibody: bool = False
    vehicle: bool = False


class EngineeringPlanner:
    """Small deterministic planner for the first usable MVP.

    It gives the agent stable behavior without pretending that a vendor script
    can be safely invented from a sentence. A future LLM planner can emit the
    same ``ExecutionPlan`` contract and keep all adapters unchanged.
    """

    _geometry = re.compile(r"catia|几何|建模|参数化|装配|零件|曲面|step|iges", re.I)
    _structural = re.compile(r"ansys|网格|结构|应力|强度|模态|热|静力|有限元|疲劳", re.I)
    _multibody = re.compile(r"adams|多体|机构|运动学|动力学|悬架|连杆|运动分析", re.I)
    _vehicle = re.compile(r"vi[- ]?grade|vigrade|车辆|整车|操稳|驾驶|底盘|轮胎|横摆|圈速|ride.?handling", re.I)

    def classify(self, prompt: str) -> _Intent:
        return _Intent(
            geometry=bool(self._geometry.search(prompt)),
            structural=bool(self._structural.search(prompt)),
            multibody=bool(self._multibody.search(prompt)),
            vehicle=bool(self._vehicle.search(prompt)),
        )

    def plan(self, request: AgentRequest) -> ExecutionPlan:
        intent = self.classify(request.prompt)
        # Explicit tool names are enough to request that tool even when the
        # Chinese description contains no domain keyword.
        if "ansys" in request.prompt.lower():
            intent = _Intent(intent.geometry, True, intent.multibody, intent.vehicle)
        if "catia" in request.prompt.lower():
            intent = _Intent(True, intent.structural, intent.multibody, intent.vehicle)
        if "adams" in request.prompt.lower():
            intent = _Intent(intent.geometry, intent.structural, True, intent.vehicle)
        if re.search(r"vi[- ]?grade|vigrade", request.prompt, re.I):
            intent = _Intent(intent.geometry, intent.structural, intent.multibody, True)

        steps: list[PlanStep] = []
        if intent.geometry:
            steps.append(PlanStep(
                id="catia_model",
                tool=ToolName.CATIA,
                action="create_or_update_model",
                objective="建立或更新参数化几何/装配模型",
                outputs=["artifacts/model.step"],
                parameters={"source_prompt": request.prompt},
            ))
        if intent.structural:
            dependencies = ["catia_model"] if intent.geometry else []
            inputs = ["artifacts/model.step"] if intent.geometry else []
            steps.append(PlanStep(
                id="ansys_solve",
                tool=ToolName.ANSYS,
                action="mesh_and_solve",
                objective="建立网格并执行结构/热/模态分析",
                depends_on=dependencies,
                inputs=inputs,
                outputs=["artifacts/ansys/result.h5", "artifacts/ansys/report.json"],
                parameters={"source_prompt": request.prompt},
            ))
        if intent.multibody:
            dependencies = ["catia_model"] if intent.geometry else []
            inputs = ["artifacts/model.step"] if intent.geometry else []
            steps.append(PlanStep(
                id="adams_simulation",
                tool=ToolName.ADAMS,
                action="build_and_run_multibody",
                objective="建立机构/多体模型并执行运动学与动力学分析",
                depends_on=dependencies,
                inputs=inputs,
                outputs=["artifacts/adams/motion.csv", "artifacts/adams/report.json"],
                parameters={"source_prompt": request.prompt},
            ))
        if intent.vehicle:
            dependencies = []
            inputs = []
            if intent.multibody:
                dependencies.append("adams_simulation")
                inputs.append("artifacts/adams/motion.csv")
            if intent.structural:
                dependencies.append("ansys_solve")
                inputs.append("artifacts/ansys/result.h5")
            if intent.geometry and not intent.multibody and not intent.structural:
                dependencies.append("catia_model")
                inputs.append("artifacts/model.step")
            steps.append(PlanStep(
                id="vigrade_vehicle_run",
                tool=ToolName.VI_GRADE,
                action="run_vehicle_dynamics",
                objective="执行车辆动力学/操稳工况并汇总指标",
                depends_on=dependencies,
                inputs=inputs,
                outputs=["artifacts/vi-grade/metrics.json", "artifacts/vi-grade/report.html"],
                parameters={"source_prompt": request.prompt},
            ))

        if not steps:
            steps.append(PlanStep(
                id="clarify",
                tool=ToolName.CATIA,
                action="clarify_request",
                objective="补充目标软件、输入文件、工况和输出指标",
                outputs=[],
                parameters={"reason": "未识别到四个工具的明确任务"},
            ))

        assumptions = [
            "默认使用 Vi-Grade（VI-grade）作为 vigrade 的含义。",
            "默认先生成执行计划；只有显式传入 --execute 且工具已配置才会调用外部软件。",
        ]
        return ExecutionPlan(request=request, steps=steps, assumptions=assumptions)
