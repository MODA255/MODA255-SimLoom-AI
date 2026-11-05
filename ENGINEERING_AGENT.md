# Engineering Agent

这是一个面向 **Ansys、CATIA、Adams、VI-grade** 的 AgentCAD 风格原型。它把自然语言任务转换成一个有依赖关系的工具链计划，再通过四个可替换适配器执行。

当前实现刻意把“规划”和“执行”分开：规划阶段不会生成未经验证的厂商脚本；执行阶段只有在配置了对应的本地可执行文件或 HTTP bridge，并显式传入 `--execute` 后才会调用外部软件。

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

浏览器访问 `/` 可生成计划和提交任务；API 包括 `/api/plan`、`/api/tasks`、`/api/projects`、`/api/doctor` 和 `/api/health`。

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

ASCII DXF 现在会归一为可操作的实体对象，保留 LINE、CIRCLE、ARC、POINT、LWPOLYLINE、TEXT、DIMENSION、图层、坐标和包围盒。写入接口也支持这些实体类型，因此可以在没有 CAD 软件的环境里完成几何检查和可重复的变换：

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

### 9. STEP B-Rep 后端和参数约束

STEP 现在有可选的 OpenCascade/OCP B-Rep 后端。安装 `cadquery-ocp`（它提供 `OCP` Python 模块）或 `pythonocc-core` 后，Agent 可以读取 STEP solid、计算体积/表面积、创建 box/cylinder，并执行 fuse/cut/common；未安装内核时 operation 会返回 `OPENCASCADE_BACKEND_UNAVAILABLE`，不会把实体计数误报成 B-Rep 成功。

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

参数约束层目前支持 `equal`、`distance`、`min`、`max`、`range`，可作为后续 CATIA 参数化 feature graph 的输入。

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

工具包括 `engineering_plan`、`engineering_run`、`engineering_generate`、`engineering_doctor`、`engineering_operations`、DXF/OBJ 几何 operation 和项目版本 operation。MCP 支持 `tools/list`、`resources/list`、`resources/templates/list` 和 `prompts/list` 分页，支持资源订阅/取消订阅，并在 initialize、工具 schema 和资源读取中返回 schema version。资源读取失败、参数错误、锁冲突和未知 operation 都统一返回 error envelope。Web API 还提供 artifact 列表、文本/JSON 预览、DXF/OBJ SVG 预览和任务事件时间线。

## 当前边界

这是一个可运行的端到端 MVP，不包含四个商业软件的授权、安装和团队内部已经验证的生产模板。接入真实环境时，需要在 Windows 工作站配置软件路径、pywin32/厂商 API、输入文件映射和结果字段；随后才能把模板脚本替换成实际项目脚本，并让 bridge 返回真实产物和指标。
