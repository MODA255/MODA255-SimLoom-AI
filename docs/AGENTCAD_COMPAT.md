# AgentCAD operation compatibility

SimLoom keeps its canonical operation names under `cad.*`, while accepting the
flat operation names published by AgentCAD. For example, `add_line` resolves to
`cad.line_create`, `move` to `cad.entity_move`, and `add_linear_dim` to
`cad.dimension_linear_create`.

The alias is resolved before schema validation. AgentCAD-style spellings such
as `p1`/`p2`, `points`, `point`, `text_height`, `translation`, `factor`, and
`properties` are copied to the canonical SimLoom fields when the canonical
field was not supplied. If both spellings are present, the canonical SimLoom
field wins. MCP clients can either supply SimLoom's file-based document
context (`project_dir`, `input_file`, and `output_file` for writes) on every
call, or open a document once with `operation.cad.document_open`. The MCP
session then keeps an inspectable active context; later AgentCAD-compatible
operations can omit those file arguments and use an in-place output
automatically. Calling `engineering_cad_context` with `action=clear` removes
that session-only default. The operation still uses the normal transaction,
checkpoint, validator, diff, history and rollback path, and the context is
never persisted as project state.

The compatibility names are included in MCP discovery with
`compatibility.alias_for`, so an agent can discover the mapping instead of
guessing it. The alias does not claim a vendor-specific semantic that the
canonical operation cannot provide. For example, both AgentCAD radial and
diameter dimension names resolve to distinct vendor-neutral radial and diameter
operations; CATIA/Ansys/Adams/VI-grade behavior still requires Windows software
validation.

The mapping lives in `src/engineering_agent/agentcad_compat.py`. The registry
and Python operation gateway share the same resolver, which keeps direct,
HTTP, MCP and Web calls consistent.

## 工程数值和单位

CAD 数值字段同时接受 JSON number 和带单位字符串，例如 `25mm`、`1in`、
`3deg`、`0.5rad`；也接受 `{ "value": 25, "unit": "mm" }`。SimLoom 在进入
几何引擎前把长度归一为毫米、角度归一为度，并继续以有限浮点数保存统一对象。
百分比和无量纲比例也可写成 `25%`、`2.0`。未知单位、角度用于长度字段、长度用于角度
字段都会返回结构化参数错误，不会猜测调用者意图。

可用单位包括：长度 `mm`、`um`、`µm`、`cm`、`m`、`in`、`ft`、`mil`；角度 `deg`、`°`、
`rad`；无量纲 `1`、`%`。MCP 的 `tools/list` 会在数量字段上公开
`x-quantity-dimension` 和 `x-quantity-format`，因此客户端可以在生成参数时发现这项兼容能力。
普通文本、文件名、实体 ID、参数引用和标准件型号不会被转换。

## MCP 兼容工具入口

compact MCP toolset 现在提供五个与 AgentCAD 主工具习惯一致的入口：

- `apply_operations` 将 `op`/`operation` 和 AgentCAD 参数名映射到事务化的 `cad.batch_apply`。批次中的每一步仍保存 checkpoint、diff、验证和回放快照。
- `read_drawing` 读取当前图纸的统一工程对象。
- `measure_entities` 返回解析几何测量证据。
- `verify_drawing` 调用独立原生 DXF 审计和工程要求检查。
- `get_diff` 比较当前图纸和另一份图纸。

这些入口和 `engineering_operation` 共享同一个活动 CAD context、路径约束、error envelope、MCP policy 和事务实现。`apply_operations` 在 safe profile 中保持只读拒绝，full profile 才允许写入。它们是 AgentCAD 风格的调用面，不把本地 DXF 语义误报成 CATIA、Ansys、Adams 或 VI-grade 的原生操作。

`create_drawing`、`draw_from_spec`、`revise_drawing`、`validate_drawing`、`inspect_entities` 和 `apply_constraints` 也已在 compact MCP 中提供。前者通过 `cad.document_create` 创建带单位、标注比例和 sidecar 的空 DXF，后者通过 `cad.query` 复用过滤、分页和稳定 `EntityRef`。创建和规格修订操作仍受 full profile、路径约束和写事务保护。`apply_constraints` 的 check 模式只读，enforce 模式沿用写事务并在冲突时回滚。规格修订会按标签保留实体引用，并在提交后重新验证规格。
