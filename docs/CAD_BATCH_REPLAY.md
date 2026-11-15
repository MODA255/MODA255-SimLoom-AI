# CAD 批次子步骤快照

`cad.batch_apply` 和 `cad.validate_and_repair` 仍然是一个原子事务，但现在会为每个实际步骤保存独立的 `before` 和 `after` JSON checkpoint。每个 checkpoint 包含：

- 执行的 operation 和步骤序号；
- 完整的统一 `CadDocument` 快照；
- 该步骤的语义 diff；
- 该步骤执行前后的独立几何验证摘要；
- 是否由自动修复重新规划产生。

快照位于本次事务的 `artifacts/runs/<run_id>/checkpoints/step-<nnn>-before/state.json` 和 `step-<nnn>-after/state.json`。返回值的 `step_snapshots`、`document.metadata.operation_history.steps` 同时记录相对路径和 SHA-256。最终 CAD 文件提交失败时，事务会回滚正式工程文件，但运行目录中的 checkpoint 和 journal 仍保留，便于分析失败步骤。

通过 `cad.batch_step_view` 可以只读读取和渲染一个步骤：

```json
{
  "project_dir": "/path/to/project",
  "run_id": "事务 ID",
  "step_index": 1,
  "side": "after"
}
```

该 operation 会重新验证 checkpoint 的结构和路径，返回统一对象、diff、验证结果和只读 viewer。原始工程文件、撤销游标和当前文件不会改变。包含无法在没有原始厂商文件时重建的 native DXF 实体时，viewer 会降级到语义 SVG，并明确留下 `CHECKPOINT_NATIVE_ENTITY_FALLBACK` 提示；这不影响 JSON 快照和验证结果。

Web 工作台的 2D Viewer 操作列表会读取这些 checkpoint 元数据。批次步骤按钮通过工作台的只读请求加载指定 `after` 快照，仍可点击“返回当前”恢复实时工程文件；普通单步操作继续只高亮变更实体。

浏览器回归入口为 `node tests/browser/cad_batch_replay.cjs`，它会实际点击两个批次步骤并确认只读状态和返回当前链路。运行需要 Playwright 和 Chromium。

## 自动修复计划

`cad.validate_and_repair` 会先保存原始验证结果，再生成带证据的修复计划。每一项至少包含：

- `operation`：实际要执行的 CAD operation；
- `source_error`：触发修复的验证错误码；
- `reason`：为什么这个修复是确定性的，或为什么只能采用删除；
- `target`：目标实体、图层或约束；
- operation 参数，例如 `entity_id`、`patch` 或 `index`。

计划会合并同一实体的字段修复，并让删除优先于此前的字段修复，避免同一实体被重复修改。当前可自动处理的扩展错误包括 spline 点数和阶次、文本高度、非有限坐标、重复几何、曲线/填充边界自交、HATCH 关联句柄丢失、DIMENSION 引用丢失以及约束引用丢失。无法从图纸推断设计意图的几何错误会删除问题实体，并把这个决策写入每一步的 `repair` 元数据。

每个自动修复仍然经过独立验证，并拥有自己的 `before`/`after` checkpoint、diff 和 history 记录；任何修复后错误都存在时，整个事务会回滚，不会发布部分有效的 CAD 文件。
