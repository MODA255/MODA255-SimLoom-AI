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
        def tool_parameters(name: str) -> dict:
            nested = request.parameters.get(name)
            if isinstance(nested, dict):
                return {**request.parameters, **nested}
            return request.parameters

        catia_parameters = tool_parameters("catia")
        ansys_parameters = tool_parameters("ansys")
        adams_parameters = tool_parameters("adams")
        vi_grade_parameters = tool_parameters("vi_grade")
        if intent.geometry:
            typed_catia = all(key in catia_parameters for key in
                              ("input_model", "native_output", "step_output", "report_output", "parameter_updates"))
            catia_action = "update_parameters" if typed_catia else "create_or_update_model"
            catia_outputs = ([catia_parameters["native_output"], catia_parameters["step_output"],
                              catia_parameters["report_output"]]
                             if typed_catia else ["artifacts/model.step"])
            catia_inputs = [catia_parameters["input_model"]] if typed_catia else []
            steps.append(PlanStep(
                id="catia_model",
                tool=ToolName.CATIA,
                action=catia_action,
                objective="建立或更新参数化几何/装配模型",
                inputs=catia_inputs,
                outputs=catia_outputs,
                parameters={**catia_parameters, "source_prompt": request.prompt},
            ))
        if intent.structural:
            dependencies = ["catia_model"] if intent.geometry else []
            typed_ansys = all(key in ansys_parameters for key in
                              ("db_file", "analysis_name", "native_output", "result_output", "report_output",
                               "required_metrics", "metric_units"))
            ansys_action = "solve_configured_model" if typed_ansys else "mesh_and_solve"
            inputs = ([ansys_parameters["db_file"]] if typed_ansys else
                      (["artifacts/model.step"] if intent.geometry else []))
            ansys_outputs = ([ansys_parameters["native_output"], ansys_parameters["result_output"],
                              ansys_parameters["report_output"]]
                             if typed_ansys else ["artifacts/ansys/result.h5", "artifacts/ansys/report.json"])
            steps.append(PlanStep(
                id="ansys_solve",
                tool=ToolName.ANSYS,
                action=ansys_action,
                objective="建立网格并执行结构/热/模态分析",
                depends_on=dependencies,
                inputs=inputs,
                outputs=ansys_outputs,
                parameters={**ansys_parameters, "source_prompt": request.prompt},
            ))
        if intent.multibody:
            dependencies = ["catia_model"] if intent.geometry else []
            typed_adams = all(key in adams_parameters for key in
                              ("command_file", "result_output", "report_output", "required_metrics", "metric_units"))
            inputs = ([adams_parameters["command_file"]] if typed_adams else
                      (["artifacts/model.step"] if intent.geometry else []))
            adams_outputs = ([adams_parameters["result_output"], adams_parameters["report_output"]]
                             if typed_adams else ["artifacts/adams/motion.csv", "artifacts/adams/report.json"])
            steps.append(PlanStep(
                id="adams_simulation",
                tool=ToolName.ADAMS,
                action="build_and_run_multibody",
                objective="建立机构/多体模型并执行运动学与动力学分析",
                depends_on=dependencies,
                inputs=inputs,
                outputs=adams_outputs,
                parameters={**adams_parameters, "source_prompt": request.prompt},
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
            typed_vi_grade = all(key in vi_grade_parameters for key in
                                 ("scenario_file", "metrics_output", "report_output", "required_metrics", "metric_units"))
            if typed_vi_grade:
                inputs = list(dict.fromkeys(inputs + [vi_grade_parameters["scenario_file"]]))
                vi_outputs = [vi_grade_parameters["metrics_output"], vi_grade_parameters["report_output"]]
            else:
                vi_outputs = ["artifacts/vi-grade/metrics.json", "artifacts/vi-grade/report.html"]
            steps.append(PlanStep(
                id="vigrade_vehicle_run",
                tool=ToolName.VI_GRADE,
                action="run_vehicle_dynamics",
                objective="执行车辆动力学/操稳工况并汇总指标",
                depends_on=dependencies,
                inputs=inputs,
                outputs=vi_outputs,
                parameters={**vi_grade_parameters, "source_prompt": request.prompt},
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
