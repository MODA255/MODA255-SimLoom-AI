# 修复比较与有界重规划报告

SimLoom AI 的修复功能用于操作已经配置好的 CATIA、Ansys、Adams 和 VI-grade 工程链。它不会替代这些软件的求解器，也不会让模型自由猜测载荷、边界条件或验收阈值。每次修复仍然必须来自软件动作配置中的 `repair_strategies`，并且以独立工程结果作为比较依据。

## 报告数据从哪里来

任务报告使用 `simloom-ai/task-report/v1`。当一次运行请求了有界修复时，报告的 `repair` 字段会从保存的运行结果中提取以下两类兼容输入：

1. `validation.auto_repair`：`EngineeringAgent.run(..., auto_repair=True)` 产生的标准入口。
2. `repair`、`repair_loop`：工作流修复接口直接返回的结果，便于 CLI、MCP 和 Web 共享同一结构。

报告只复制已保存的比较与决策，不重新读取当前项目文件，也不重新计算工程指标。这样可以避免工程师在任务完成后修改结果文件，导致历史报告被悄悄改变。

## `repair` 字段

```json
{
  "schema_version": "simloom-ai/task-repair-report/v1",
  "requested": true,
  "status": "committed",
  "parent_workflow_id": "父工程链编号",
  "child_workflow_id": "本次子工程链编号",
  "candidate": {
    "strategy_id": "reduce_declared_load",
    "step_id": "ansys_static",
    "reason": "切换工程师预先配置的载荷工况"
  },
  "comparison": {
    "schema_version": "simloom-ai/repair-comparison/v1",
    "outcome": "improved",
    "next_action": "evaluate_next_candidate",
    "metrics": []
  },
  "decision": {
    "status": "continue_allowed",
    "reason": "remaining_declared_candidates",
    "next_action": "evaluate_next_candidate"
  },
  "loop": {
    "schema_version": "simloom-ai/repair-loop/v1",
    "budget": 2,
    "attempt_count": 1,
    "attempts": []
  },
  "attempts": []
}
```

`comparison.metrics` 按 `step_id + metric.name` 对齐父子结果，保留父值、子值、`delta`、趋势、单位、规则和通过状态。`<` / `<=` 指标默认降低为改善，`>` / `>=` 指标默认升高为改善，等式指标比较到目标值的距离。单位不一致或数值缺失时，比较结果会保留 `comparable=false` 或 `trend=unknown`，不会猜测工程含义。

## Web 与 HTML 展示

任务详情会在工程指标之后展示“修复比较与有界重规划”卡片，包括：

- 父工程链与子工程链编号；
- 已声明候选策略及其原因；
- 父值、子值、变化量、趋势和父子通过状态；
- 每轮子工程链、策略、比较结果和停止原因；
- 最终动作，例如 `stop_success`、`evaluate_next_candidate` 或 `manual_review`；
- 可展开的原始 JSON，方便工程师审计、导出和继续排查。

HTML 工程报告同步保存这些信息，因此脱离工作台也能查看修复决策。没有保存修复证据的旧任务不会根据当前项目文件推断修复记录，报告中的 `repair` 保持为空。

## 大小边界

报告最多保存 8 轮修复尝试和 256 项指标比较。这个限制只影响展示层，不改变工作流引擎的预算、策略次数或停止条件；原始工作流状态仍按工作流存储规则保存。

## 验证范围

`tests/test_repair_reports.py` 验证标准自动修复、无证据旧任务和大小边界。它使用合成的统一工程结果，只证明报告、Web 和 HTML 的数据传递正确，不证明 CATIA、Ansys、Adams 或 VI-grade 在 Windows 上的实机求解结果。
