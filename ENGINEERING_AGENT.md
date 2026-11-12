# Engineering Agent

这是一个面向 **Ansys、CATIA、Adams、VI-grade** 的软件操作与工程编排 runtime。
它把自然语言任务转换成有依赖关系的工具链计划，再通过四个适配器去驱动真实软件。
四款软件仍然负责原生 CAD、网格、求解和专业结果；SimLoom AI 负责把目标变成可审计的
软件动作，管理输入输出、任务状态、验证、版本和恢复。

当前实现刻意把“规划”和“执行”分开：规划阶段不会生成未经验证的厂商脚本；执行阶段只有在配置了对应的本地可执行文件或 HTTP bridge，并显式传入 `--execute` 后才会调用外部软件。离线替身只验证 SimLoom 自己的事务、文件和验证边界，不冒充真实软件结果。

## 快速开始

```bash
PYTHONPATH=src python -m engineering_agent.cli plan \
  "用 CATIA 建悬架模型，导入 Adams 做多体动力学，再用 VI-grade 做操稳分析"

PYTHONPATH=src python -m engineering_agent.cli run \
  "用 CATIA 建悬架模型，导入 Adams 做多体动力学，再用 VI-grade 做操稳分析"

PYTHONPATH=src python -m engineering_agent.cli doctor
```

`run` 默认是 dry-run。真正执行时使用 `--execute`，并先复制配置：

```bash
cp configs/engineering-agent.example.json configs/engineering-agent.local.json
```

在 `engineering-agent.local.json` 中填写四个软件的命令行入口，或者填写一个接收统一 bridge 请求的服务地址。例如：

```json
{
  "tools": {
    "ansys": {"executable": "ansyswb"},
    "catia": {"bridge_url": "http://windows-gateway:8080/catia"},
    "adams": {"executable": "adams"},
    "vi-grade": {"bridge_url": "http://windows-gateway:8080/vi-grade"}
  }
}
```

适配器使用参数数组调用进程，不拼接 shell 字符串。这样自然语言中的内容不会直接变成任意 shell 命令。厂商专用的 `.wbjn`、CATScript、Adams command、VI-grade 工况模板应由 bridge 或项目模板提供。

## 规划规则

- 几何、参数化、装配、STEP/IGES → CATIA
- 网格、应力、强度、模态、热分析 → Ansys
- 机构、悬架、运动学、多体动力学 → Adams
- 整车、操稳、底盘、轮胎、圈速、驾驶工况 → VI-grade

计划输出固定为 `ExecutionPlan`，以后接入 LLM planner 时仍可复用同一组适配器和执行审计。

## 已接入的八项能力

### 1. 四软件脚本生成

```bash
PYTHONPATH=src python -m engineering_agent.cli --project-dir ./demo generate --name suspension_demo
```

会生成 CATScript、Ansys Mechanical Python、Adams command、VI-grade scenario 和运行说明，统一放在 `artifacts/generated/`。生成的内容是可审计模板，生产环境应替换为团队已经验证的 CATPart、命名选择、Adams 模板和 VI-grade 工况模板。

### 2. CAD/仿真模型规格

模型参数由 `ModelSpec` 描述，可通过 Python API 传入 `wheelbase_mm`、悬架刚度、阻尼、载荷工况和材料。它让四个软件共享同一份输入，而不是各自维护互相漂移的参数。

### 2.1 无商业软件的四软件闭环

`offline-run` 把同一份 `ModelSpec` 依次送入 CATIA、Ansys、Adams、VI-grade
的离线替身，生成本地 STEP 几何、Ansys 结构结果 JSON、Adams 运动 CSV 和
VI-grade 指标 JSON：

```bash
PYTHONPATH=src python -m engineering_agent.cli \
  --project-dir ./examples/offline_demo offline-run \
  --model-spec '{"name":"offline_suspension"}'
```

该命令会把结构指标和车辆指标合并后交给独立规则验证器，并将报告、哈希和
所有产物写入事务 staging，再一次性提交到 `artifacts/generated/`。离线替身
用于验证跨软件对象、事务、版本和审计边界；它不等同于商业软件求解器，也不
伪造 CATIA、Ansys、Adams 或 VI-grade 的真实结果。接入 Windows bridge 后，
可以沿用同一报告和验证接口替换单个 stage。

### 3. Windows COM

`WindowsAutomation` 在 Windows + pywin32 环境下提供 COM Dispatch/invoke；在 macOS/Linux 上会返回清晰的环境诊断。复杂的软件操作建议放在 Windows bridge 中，由 bridge 调用 COM、厂商脚本或命令行。

### 4. 结果解析和工程指标

```bash
PYTHONPATH=src python -m engineering_agent.cli evaluate result.json rules.json
```

支持 JSON、CSV 和文本结果，规则支持 `<`、`<=`、`=`、`>=`、`>`，输出每项指标的值、阈值、单位、通过状态和原因。

### 5. Web 控制台和 API

```bash
PYTHONPATH=src python -m engineering_agent.cli serve --port 8787
```

浏览器访问 `/` 可进入工程工作台：选择悬架结构、悬架动力学、整车操稳或自定义
模板，填写自然语言工程目标，生成 AI 计划，查看 CATIA → Ansys → Adams → VI-grade
流程画布，并提交任务或直接运行离线闭环。工作台还包含验证指标卡片、任务时间线、
工程资产扫描和后端诊断。API 包括 `/api/plan`、`/api/tasks`、`/api/projects`、
`/api/templates`、`/api/offline-run`、`/api/artifacts`、`/api/asset-view`、
`/api/timeline`、`/api/doctor` 和 `/api/health`。

桌面/浏览器统一入口：

```bash
PYTHONPATH=src python -m engineering_agent.cli desktop --mode browser
PYTHONPATH=src python -m engineering_agent.cli desktop --mode native
```

`native` 模式使用可选的 `pywebview`；没有安装时，`auto` 会自动回退到浏览器。工作台
项目向导会把模板、模型参数、规则和项目目录保存为 `simloom.project.json`，SQLite
只保存项目索引、任务状态和事件，因此应用重启后仍能打开同一个工程。

“模型与结果”页面调用 `/api/asset-view`：STEP 在 OpenCascade 可用时先对实际 B-Rep
面进行三角化，展示自动归一化的交互式网格；OBJ 使用相同的 WebGL viewer，CSV 显示
数值曲线，JSON 结果显示指标图。预览位于 sandboxed iframe，原始文件通过 `/api/download`
下载。

### 6. 大模型自主规划

配置中设置 `llm.enabled=true` 后，Agent 会请求 OpenAI 兼容的 `/chat/completions` 接口，并对返回的工具白名单、步骤 ID、依赖关系进行校验。模型服务不可用或返回非法计划时，自动回退到确定性规划器。

### 7. 任务队列和历史项目

`TaskQueue` 使用 SQLite 保存项目、任务、状态和事件；可以通过 `enqueue` 提交，`worker` 消费，Web API 查询历史。任务状态包括 `queued`、`running`、`completed` 和 `failed`。

```bash
PYTHONPATH=src python -m engineering_agent.cli enqueue --db engineering-agent.db "用 CATIA 建悬架并做 Adams 动力学"
PYTHONPATH=src python -m engineering_agent.cli worker --db engineering-agent.db
PYTHONPATH=src python -m engineering_agent.cli project-create --db engineering-agent.db suspension-demo
PYTHONPATH=src python -m engineering_agent.cli projects --db engineering-agent.db
```

### 8. DXF 语义读写、变换和 Viewer

ASCII DXF 现在会归一为可操作的实体对象，保留 LINE、CIRCLE、ARC、POINT、LWPOLYLINE、TEXT、DIMENSION、图层、坐标和包围盒。常见样式字段（线型、颜色、线宽、厚度、所有者引用）以及未知/代理实体的原始 group code 也会保留，写入接口支持这些实体类型，因此可以在没有 CAD 软件的环境里完成几何检查和可重复的变换：

```python
from engineering_agent import EngineeringAgent

agent = EngineeringAgent({"tools": {}})
print(agent.execute_operation("geometry.dxf_inspect", {"input_file": "part.dxf"}))
print(agent.execute_operation("geometry.dxf_diff", {"left_file": "a.dxf", "right_file": "b.dxf"}))
agent.execute_operation("geometry.dxf_transform", {
    "project_dir": ".",
    "input_file": "part.dxf",
    "output_file": "part-moved.dxf",
    "translate": [100, 0, 0],
    "scale": 1.0,
    "rotate_deg": 0,
})
```

`geometry.validate` 会检查空实体、零长度线、非法半径、过短闭合折线、非有限坐标和包围盒；Web 的 `/api/asset-view` 对 DXF 返回 2D SVG 语义预览，对结果文件返回指标图。

### 8.1 统一 CAD 文档编辑层

`CadDocument` 位于 `cad_model.py`，为 DXF 提供稳定的 `EntityRef`、`SelectionSet`、
`Layer`、`Block` 和 `Constraint` 对象。`cad_operations.py` 将 71 个 CAD operation
统一到同一个执行路径：读取文档副本、应用操作、checkpoint、独立验证、生成 diff，
验证失败时生成可审计的 repair/replan operation，再通过 `RunTransaction` 提交 DXF
和 `.cad.json` 元数据；提交后还会重新读取最终文件，验证实体数量、类型、稳定 ID、
图层、sheet、viewport 和几何完整性。外部软件只需要读取最终 DXF 或通过 Windows bridge 接管后续厂商操作。

新增的 Web `POST /api/cad/operation` 和 MCP `operation.cad.*` 共享这套执行器；DXF
Viewer 支持实体选择、按图层显隐、逐实体回放和选择集保存，并提供移动、旋转、删除和文字属性编辑。
编辑请求通过父工作台 `postMessage` 回到同一个 CAD operation endpoint，继续经过事务、
checkpoint、独立验证、diff、落盘重读和 rollback；选择集和编辑历史存放在 sidecar 中。

### 9. STEP B-Rep 后端和参数约束

STEP 现在有可选的 OpenCascade/OCP B-Rep 后端。安装 `cadquery-ocp`（它提供 `OCP` Python 模块）或 `pythonocc-core` 后，Agent 可以读取 STEP solid、计算体积/表面积、创建 box/cylinder/sphere/cone，并执行 fuse/cut/common；未安装内核时 operation 会返回 `OPENCASCADE_BACKEND_UNAVAILABLE`，不会把实体计数误报成 B-Rep 成功。

```bash
./.venv/bin/python -m pip install -e '.[brep]'
```

```python
agent.execute_operation("brep.inspect_step", {"input_file": "part.step"})
agent.execute_operation("brep.boolean", {
    "project_dir": ".", "operation": "fuse",
    "left_file": "a.step", "right_file": "b.step", "output_file": "fused.step",
})
agent.execute_operation("brep.solve_constraints", {
    "parameters": {"width": 20},
    "constraints": [{"type": "min", "left": "width", "value": 10}],
})
```

参数约束层目前支持 `equal`、`distance`、`min`、`max`、`range`，可直接作为 STEP
feature graph 的参数输入。`brep.build_feature_graph` 可以按特征顺序重建 box、cylinder、
sphere、cone 和 fuse/cut/common，并在输出旁边保存 `.brep.json` 特征历史与约束结果：

```python
agent.execute_operation("brep.build_feature_graph", {
    "project_dir": "./demo",
    "output_file": "artifacts/bracket.step",
    "parameters": {"length": 80},
    "constraints": [{"type": "min", "left": "length", "value": 20}],
    "features": [
        {"id": "base", "type": "box", "parameters": {"length": "length", "width": 40, "height": 10}},
        {"id": "hole", "type": "cylinder", "parameters": {"radius": 4, "height": 10}},
        {"id": "result", "type": "cut", "left": "base", "right": "hole"},
    ],
})
```

三个 B-Rep 写操作都经过同一条可靠性边界：`project_dir` 下的输入和输出路径会先做项目目录
校验；内核写入 staging 后，SimLoom 先检查暂存 STEP，再在事务提交阶段从实际目标文件重新读取
并检查 `valid`、非空拓扑、有限且非负体积。落盘检查结果会写入事务 journal 和返回值；如果
目标文件读回失败，事务会恢复提交前的文件。覆盖 primitive 或 boolean 输出时，旧的
`.brep.json` feature graph sidecar 会一起清理，避免工程历史与几何文件不匹配。这个检查针对
本地 OpenCascade 文件，不能替代 CATIA、Ansys、Adams 或 VI-grade 的真实后端验收。

也可以直接从 CLI 探测或调用 B-Rep 后端：

```bash
PYTHONPATH=src python -m engineering_agent.cli brep-doctor
PYTHONPATH=src python -m engineering_agent.cli brep-inspect part.step
PYTHONPATH=src python -m engineering_agent.cli --project-dir ./demo brep-primitive box artifacts/box.step --parameters '{"length":10,"width":20,"height":5}'
PYTHONPATH=src python -m engineering_agent.cli --project-dir ./demo brep-boolean fuse a.step b.step artifacts/fused.step
```

### 10. 离线网格读取和 Viewer

OBJ 网格可以在没有商业 CAD 或渲染库的环境中读取、写入和校验。Agent 会提取顶点、面、三角形数、表面积和包围盒，并检查面索引、重复顶点和非有限坐标。Viewer 返回一个可嵌入 Web 页面中的等轴线框 SVG：

```python
agent.execute_operation("geometry.mesh_inspect", {"input_file": "mesh.obj"})
agent.execute_operation("geometry.mesh_validate", {"input_file": "mesh.obj"})
agent.write_asset("mesh-copy.obj", "obj", {"vertices": vertices, "faces": faces})
```

## Runtime 保护层

每次运行都会生成一个 `run_id`，并在 `artifacts/runs/<run_id>/` 保存执行 journal、checkpoint、staged 文件和 manifest。所有生成脚本先写入 staging，只有整条 dry-run/执行链成功才提交到 `artifacts/generated/`；提交中途失败会恢复已经写入的文件，异常退出留下的运行可被 `recover_stale` 标记为 abandoned 并清理 staging，过期的项目锁也会自动回收。

类型化 operation registry 和独立 validator 位于 `operations.py`、`validation.py`。它们会检查工具白名单、步骤 ID、依赖关系、环依赖和结果状态。

CAD validator 还会检查实体引用、文字高度、闭合轮廓面积、重复几何、尺寸数值和可选的尺寸
关联要求。默认重复几何作为警告保留现场；在工程要求中设置 `reject_duplicate_geometry`
或 `require_dimension_association` 后，才会按错误阻止发布。

带有 `pid_symbol` 和 `process_line` 语义的图纸可以通过 `cad.pid_audit` 只读回读为节点/边，
检查孤立符号、悬空接点、必需符号和必需连接；这是一层工程关系验证，仍需要真实 P&ID
标准库和厂商软件中的回读验收。

`cad.process_route_create` 会把起点、终点和可选 waypoints 写成带 `process_line` / `process_route`
元数据的正交 `LWPOLYLINE`；默认自动生成一个拐角，也会拒绝非正交路线。`cad.pid_valve_cut_pipe`
可以在带语义的 `LINE`、`LWPOLYLINE` 或 `POLYLINE` 上按 `point`、`fraction` 或 `distance`
切口，生成两段管线、阀门符号及 inlet/outlet 连接段。返回结果包含阀门 ID、连接段 ID、分割
管线 ID 和阀门中心；重新读取 DXF 后，`cad.pid_audit` 会把四段连接关系读回为同一张图。
P&ID 符号可通过 `ports` 参数声明命名端口、方向、坐标和方向向量；审计结果会单独返回端口，
并检查连接段引用的阀门和端口是否存在。
这仍是工程对象层的输入准备和关系验证，阀门图形、端口方向、规格和最终符号标准需在目标
P&ID/CAD 软件中确认。

## 工程对象、读取、版本和 bridge

```bash
# 读取真实 STEP/CSV/JSON
PYTHONPATH=src python -m engineering_agent.cli ingest ./artifacts/model.step

# 扫描项目资产
PYTHONPATH=src python -m engineering_agent.cli inspect --root ./demo

# 创建版本并比较
PYTHONPATH=src python -m engineering_agent.cli version baseline ./artifacts/model.step ./artifacts/metrics.json --root ./demo
PYTHONPATH=src python -m engineering_agent.cli version tuned ./artifacts/model.step ./artifacts/metrics.json --root ./demo --parent VERSION_ID
PYTHONPATH=src python -m engineering_agent.cli diff VERSION_A VERSION_B --root ./demo

# Windows 工作站启动 bridge
PYTHONPATH=src python -m engineering_agent.cli bridge --bridge-config configs/engineering-agent.example.json
```

`ingest.py` 把 STEP 的实体计数、Cartesian point 包围盒、CSV 数值列统计和 JSON 数值指标归一到 `EngineeringObject`。`versioning.py` 将这些对象保存为不可变快照并生成字段级 diff。Web API 的 `/api/asset-view` 会返回 STEP 包围盒或结果指标的 SVG 预览。

`OperationRegistry.validate_arguments()` 现在会在 Python、HTTP 和 MCP 共用的执行入口检查已发布
operation schema 的类型、枚举、数值边界、数组长度和对象属性数量；Python API 传入的 `Path`
会按 JSON 字符串参数兼容处理。CAD 圆角、倒角等操作仍保留自己的几何错误码，便于工程师定位
具体失败原因。

Windows bridge 默认使用 `shell=False`、项目根目录沙箱、命令白名单和 COM 方法白名单。真实 Windows 机器上再把命令模板替换成已验证的 CATIA/Ansys/Adams/VI-grade 启动方式。

Windows 验收探针：

```bash
PYTHONPATH=src python -m engineering_agent.cli windows-validate
PYTHONPATH=src python -m engineering_agent.cli windows-validate --smoke
```

第一条只检查平台、pywin32、配置和可执行文件；第二条才会在 Windows 上对四个 ProgID 执行 COM dispatch。它不会启动求解，也不会修改工程文件。输出包含每个软件的 `configured`、`ready` 或 `unavailable` 状态及证据。

`/api/asset-view` 现在除了 SVG 预览，还会返回自包含的 WebGL viewer。OBJ 网格支持拖拽旋转和滚轮缩放；如果资产提供 `geometry.scalars` 或 `geometry.point_values`，顶点会按蓝-黄-红映射显示结果云图。

本地 MCP stdio 入口：

```bash
PYTHONPATH=src python -m engineering_agent.cli mcp
```

工具包括 `engineering_plan`、`engineering_run`、`engineering_generate`、`engineering_doctor`、`engineering_operations`、`engineering_asset_view`、`engineering_inspect_image`、`engineering_measure_image`、DXF/OBJ 几何 operation 和项目版本 operation。MCP 支持 `tools/list`、`resources/list`、`resources/templates/list` 和 `prompts/list` 分页，支持资源订阅/取消订阅，并在 initialize、工具 schema 和资源读取中返回 schema version。`engineering_asset_view` 与 `engineering://asset` 会同时返回结构化资产数据和 SVG image content，`engineering_inspect_image` 会返回按源像素裁切的 PNG image content，`engineering_measure_image` 会返回颜色区域的边界、质心和 PNG overlay，便于 agent 检查图纸与结果细节。资源读取失败、参数错误、锁冲突和未知 operation 都统一返回 error envelope。Web API 还提供 artifact 列表、文本/JSON 预览、DXF/OBJ SVG 预览和任务事件时间线。

## 当前边界

这是一个可运行的端到端 MVP，不包含四个商业软件的授权、安装和团队内部已经验证的生产模板。接入真实环境时，需要在 Windows 工作站配置软件路径、pywin32/厂商 API、输入文件映射和结果字段；随后才能把模板脚本替换成实际项目脚本，并让 bridge 返回真实产物和指标。


## 工作台任务报告

`tasks.TaskRunner` 为 Web 和 CLI worker 统一原子认领、输入快照、阶段事件和失败记录。
`execution.Progress` 使用实际时间测量阶段耗时；`reports.build_report` 只读取持久化
任务与事件，不读取当前模型来补造历史指标。HTML 导出对用户文本转义并禁止脚本。

新报告入口为 `/api/tasks/<id>/report?format=json|html`，`download=1` 返回下载附件。
重试入口 `/api/tasks/<id>/retry` 复制原快照并关联 `retry_of`，运行仍调用
`/api/tasks/<id>/run`。旧数据库按需增加 `mode`、`input_json`、`retry_of` 字段。

`execution.commit_outputs` 在原事务中提交最新产物与按 run_id 分隔的历史副本。
报告保存历史副本相对路径、字节数和生成时 SHA-256。每次运行保留副本会增加磁盘用量，
当前没有自动清理。执行失败、指标不通过、未判定和缺失证据在界面中分别展示。

## CAD 持久历史与事务验证

`cad.history`、`cad.undo`、`cad.redo` 使用 `input_file` 和可选 `project_dir`，
通过 Python、Web 的 `/api/cad/operation`、MCP operation 工具及 CLI 访问。
撤销和重做逐字节恢复 DXF 与 `.cad.json`，并与历史游标一起提交。
`artifact.write` 的 DXF 创建也进入相同历史。

`cad.visual_diff` 是只读对比入口，输入两个 DXF 后按实体 ID 对齐，返回语义 diff 和可嵌入
页面的 SVG 叠加图。新增、删除、修改和未变化实体分别使用绿色、红色、橙色和灰色标记。

`cad.script_apply` 是受控的 CAD 辅助脚本入口。它默认关闭；在配置或本次 operation 中
显式设置 `allow_scripts=true` 后，脚本会收到一个请求 JSON（`sys.argv[1]`），其中包含
事务工作区内的 `input_file`、白名单 `output_file` 和调用参数。脚本只能发布声明的 DXF
输出，SimLoom 会在发布前重新读取文件、独立验证、生成 diff 并加入同一套历史。脚本有
静态导入/调用策略、最长执行时间和输出大小限制；这是一层工程策略保护，生产环境仍应
在 Windows worker 上使用独立账号或更强的操作系统隔离。

所有这些修改的落盘验证都在项目锁内执行；提交过程中崩溃后，下一次
项目读取或修改会先恢复未完成的文件组。故障恢复遇到外部修改或损坏快照
会保留现场并报告错误，不会用旧文件覆盖未知内容。

参见 [恢复契约](docs/CAD_RELIABILITY.md)。逐实体高亮不是历史版本回放；
复杂图纸保真、独立尺寸验证、纸空间出图及 MCP 完整互通仍未完成验收。
