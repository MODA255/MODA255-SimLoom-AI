# 工程链断线恢复

SimLoom 操作工作站上的工程软件。控制端退出或观察超时不代表 CATIA 操作、Ansys 求解等已经停止；
恢复必须查询原任务，不能重新生成计划后从头执行。

## 使用方式

实际执行的计划包含两个或更多步骤，且至少一个步骤使用远程 worker 时，会自动保存工程链。
`run` 的报告及任务事件提供 `workflow_id`。纯本机执行计划和单步 operation 保持原有入口；
单个远程任务仍使用 `software-job ... collect` 回收。

```sh
engineering-agent --config workstation.json --project-dir ./project workflow list
engineering-agent --config workstation.json --project-dir ./project workflow status <workflow_id>
engineering-agent --config workstation.json --project-dir ./project workflow resume <workflow_id>
```

`resume` 是执行操作：它会读取已完成步骤的原结果、查询原远程任务，并继续原计划中的剩余步骤。
它不重新调用大模型生成另一份计划。改变工程目标或参数应创建新任务；重复调用 `run` 会创建新任务。

工作台的“环境检查”页提供“继续未完成的工程链”。队列任务详情也提供“恢复并继续工程链”。
工作台恢复队列所属的工程链时，会更新同一个任务的事件和结果。显示的进度是已保存记录；
仍有控制端持有项目锁时，第二个恢复请求会返回 `PROJECT_LOCKED`，不会同时执行。

Python 使用 `agent.workflow('resume', workflow_id, project_dir=...)`；HTTP 使用
`POST /api/workflows`，参数为 `project_id`、`action` 和 `workflow_id`。
队列任务也可使用 `POST /api/tasks/<task_id>/resume`。
MCP 的 `engineering_workflow` 在 safe profile 中仅有 `list/status`，full profile 才允许 `resume`。

## 恢复依据

保存目录是项目内 `.engineering-agent/workflows/<workflow_id>/`：

- 原执行计划及摘要；按依赖顺序执行，允许计划列表本身未排序。
- 原输入和 runner 脚本快照及 SHA-256；控制端动作绑定、连接地址和环境配置的摘要。
- 每步在调用软件前保存的请求、请求摘要和稳定任务编号。
- 每步原始输出响应及哈希；恢复时重新解码文件并按原证据契约校验。
- 原目标文件哈希、步骤状态、失败原因及最终发布记录。

恢复允许更新连接凭据和观察超时；输入、脚本、动作绑定或连接地址改变会阻止继续。
控制端不会用当前配置中的新阈值替换已提交任务的旧阈值。工作站自身的软件版本、许可证、
环境和启动命令仍需部署管理与实机验收，控制端配置摘要不能证明这些保持不变。

下游读取本轮已验证、尚未发布的上游结果。计划中重复输出路径、输出覆盖 runner、
读取另一操作的产物却未声明依赖，会在调用软件前拒绝。
全部步骤完成后，结果、历史副本、各步回收回执和工程链完成记录在同一项目事务中发布。
若中途失败，不发布部分工程链；已完成的软件操作及其原结果仍保留。
因此工程链中的单步回执不能单独 collect，需恢复工程链统一回收。

最终发布再次比较原目标哈希。工程师在等待期间修改的文件不会被恢复操作覆盖。
完成后的重复 resume 只核对已发布文件和历史副本，不会执行任何软件步骤。

## 状态与处理

| 状态 | 含义 | 处理 |
| --- | --- | --- |
| ready / running | 保存的准备或执行记录；不是当前进程存活证明 | 查询工作站；原控制端已退出时可恢复 |
| waiting | 观察时间结束，远程任务可能仍在运行 | 使用原工程链恢复，继续查询原编号 |
| blocked | 输入、配置、缓存、目标冲突，或本机操作状态不明 | 查看具体原因，先核实状态；不能当作可自动重跑 |
| failed | 已取得明确失败结果 | 保留失败结果与日志，不自动重执行 |
| publishing | 完成记录已写入但整组发布尚未确认 | 等待原事务或由恢复流程处理 |
| completed | 所有步骤结果和记录已提交 | 查看结果及独立的工程验收结论 |

远程 worker 已确认存在的任务后来返回 `JOB_NOT_FOUND`，会报 `REMOTE_JOB_HISTORY_LOST`，
不会悄悄重新提交求解。已持久接收但尚未启动的任务可在查询时继续启动；运行过或监督进程
丢失的任务不走这条恢复路径。worker 必须保留其任务数据库；数据库丢失、尤其尚未收到确认
时的历史丢失，不具备跨存储损坏的“恰好一次”保证。

混合计划允许本机命令步骤。若控制端在本机步骤执行后、保存响应前退出，无法可靠判断软件
副作用，恢复会报 `LOCAL_STEP_STATE_UNKNOWN`。需要实地检查，不能自动重跑。
已保存响应的本机步骤可校验并复用结果。远程取消和软件占用核实仍在软件任务入口处理；
此版本没有自动取消整条工程链或自动修复终止失败步骤。

## 验证范围

`tests/test_workflow_resume.py` 使用实际子进程和受控 runner 测试：控制端在求解期间、步骤之间、
发布过程中退出；恢复时计数确认每步只运行一次；原输入传递、文件冲突、缓存损坏、
配置改变、已知任务丢失、终止失败、本机不确定状态、原子发布和队列恢复。
`tests/browser/software_jobs.cjs` 通过真实浏览器操作工程链恢复、回收结果并检查队列状态。

这些是执行基础设施的证据，不是 CATIA / Ansys / Adams / VI-grade 的软件或工程实机验收。


## 基于证据的修复与重规划

完成的工程链如果保存了 `vendor_validation.metric_verdicts`，工作台可以显示“按失败证据尝试修复”。
CLI 使用：

```sh
engineering-agent --project-dir ./project workflow repair <workflow_id>
```

MCP full profile 使用 `engineering_workflow` 的 `action=repair`；safe profile 仍只读。修复策略必须
写在对应软件动作的 `repair_strategies` 中，例如：

```json
{
  "id": "reduce_declared_load",
  "when": {"metric": "equivalent_stress", "failed": true},
  "parameter_patch": {"load_case": "reduced_load_case"},
  "reason": "切换到工程师已配置的低载荷工况",
  "max_attempts": 1
}
```

策略只能修改该步骤原本已存在且不属于输入、输出、单位、阈值、验证契约或软件命令的参数。
系统不会通过放宽阈值来制造通过，也不会猜测新载荷、命令或边界条件。每次尝试创建新的子工程链，
将所有产物写入 `artifacts/repairs/<父工程链>/<尝试编号>/`；父工程链、失败指标和原始产物继续保留。
没有匹配的显式策略时返回 `needs_review`，不启动软件。
