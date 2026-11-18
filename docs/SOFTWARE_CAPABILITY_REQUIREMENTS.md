# 四软件动作能力要求

SimLoom 的一个 CATIA、Ansys、Adams 或 VI-grade 动作可以声明它需要的两类能力：适配器能力和 worker 特性。能力要求在同一份 `simloom-ai/software-operation-contract/v1` 中传递，preflight、执行入口和 worker 都能看到同一份声明。

这层契约解决的是配置和调度问题。它不会把“适配器声称支持某能力”或“worker 宣布某特性”当成商业软件已经安装、许可证可用、脚本语义正确或工程结果合格；这些仍然需要 Windows 实机和独立工程证据。

## 配置

在软件动作绑定中加入 `capability_requirements`：

```json
{
  "script": "scripts/ansys_runner.py",
  "command": ["C:/SimLoom/python/python.exe", "{script}", "{request_file}"],
  "capability_requirements": {
    "adapter_capabilities": ["static-structural", "results"],
    "worker_features": ["durable_jobs", "job_logs"]
  },
  "validation": {"kind": "mechanical_solve"}
}
```

`adapter_capabilities` 对应适配器公开的 vendor-neutral 能力声明。例如 Ansys adapter 当前声明 `mesh`、`static-structural`、`modal`、`thermal` 和 `results`；CATIA adapter 声明 `parametric-geometry`、`assembly`、`step-export` 和 `iges-export`。未知或缺失的能力会在本地执行前返回 `ADAPTER_CAPABILITY_MISSING`。

`worker_features` 对应远程 worker 的协议特性。当前 worker 会在 `/health` 和 `/capabilities` 中报告 `durable_jobs`、`job_logs`、`software_serialization`。本地命令没有 worker，因此声明 worker 特性会返回 `WORKER_FEATURE_REQUIRES_BRIDGE`。

两个数组都会去重、排序并限制为 128 个字符串，确保 operation contract 的 digest 在不同入口稳定。

## 生命周期

- **preflight**：检查适配器能力。远程 worker 未请求探测时，worker 特性标为 `not_probed`、设置 `probe_required=true`，并返回 `WORKER_CAPABILITY_PROBE_REQUIRED`；这样离线检查不会偷偷联网，也不会把未验证的动作标记为 `ready`。传入 `probe=true` 后会调用 worker 健康检查，并把缺失特性列入 `issues`。
- **execute**：本地动作在启动 runner 前检查适配器能力；远程动作还会探测 worker 并拒绝缺失的 worker 特性。失败不会启动软件进程。
- **contract**：`operation_contract.requirements` 保存规范化后的两类要求，并参与 `definition_digest`。worker 收到请求时会重新验证它与 payload 的要求一致，防止请求在传输前被篡改。
- **result**：统一 `engineering_result.operation_contract` 保留要求；它仍然只表示声明和协议检查，不改变 `real_machine_verified` 或工程验收结论。

环境检查的每个软件项还会返回 `action_requirements` 和 `ready_actions`。`ready_actions` 只包含当前已找到启动程序、适配器能力满足且 worker 要求已探测通过的动作；远程检查未探测时动作状态为 `not_probed`，不会被能力矩阵当成可执行。`actions` 仍表示配置中找到的命令，不能单独当作执行就绪证据。

Web 工作台的环境检查页会同时显示“已发现”“可执行”和“已验收”三个数量，并给每个动作标出可执行、能力缺失、待探测或实机已验收状态。这样工程师能直接区分配置存在、协议条件满足和真实软件证据。

## Preflight 的结构化下一步建议

从 `simloom-ai/software-preflight/v1` 的当前版本开始，preflight 会把每个阻塞问题翻译成
`next_actions`。这让 Agent 可以直接进入下一步处理，不需要根据中文错误消息猜测应该重试、改配置还是联系工程师。
建议码与 `docs/CAPABILITY_MATRIX.md` 中的能力矩阵共用同一套词汇：

| 建议码 | 适用情况 | Agent 可以做的事 |
| --- | --- | --- |
| `configure_action_binding` | 动作绑定、脚本路径、命令或 worker 动作登记缺失 | 检查配置文件、脚本和 worker action 名称，然后重新生成 preflight |
| `configure_capabilities` | 适配器能力或 worker 特性不满足 | 修改动作要求，或切换到真正声明了所需能力的适配器/worker |
| `probe_worker` | 远程 worker 尚未探测或当前不可达 | 认证并读取 worker health/capabilities；探测失败时查看连接诊断 |
| `inspect_diagnostics` | 未知或无法归类的 contract 检查失败 | 读取完整诊断，确认版本、路径和 contract 字段后再处理 |

计划级错误也使用同一建议机制：`PLAN_INVALID` 会提示读取 `plan_validation.errors` 并重新生成计划；
包含 `clarify_request` 的计划会返回 `CLARIFICATION_REQUIRED`，提示补充工程目标或输入约束。即使没有
任何可执行步骤，Agent 也能得到明确的下一步，而不会把空的 `next_actions` 误解成“没有问题”。

步骤级建议的形状如下：

```json
{
  "step_id": "ansys-static",
  "tool": "ansys",
  "action": "solve_static",
  "ready": false,
  "issues": [
    {"code": "WORKER_CAPABILITY_PROBE_REQUIRED", "message": "执行前必须探测远程 worker 能力"}
  ],
  "next_actions": [
    {
      "action": "probe_worker",
      "reason": "先探测并认证 Windows worker 的能力",
      "issue_codes": ["WORKER_CAPABILITY_PROBE_REQUIRED"]
    }
  ]
}
```

计划级 `next_actions` 会按步骤合并相同建议，并附带 `step_ids` 与 `issue_codes`，例如：

```json
{
  "next_actions": [
    {
      "action": "configure_capabilities",
      "reason": "补齐适配器声明的能力，或改用当前适配器支持的动作",
      "step_ids": ["catia-model", "ansys-static"],
      "issue_codes": ["ADAPTER_CAPABILITY_MISSING"]
    },
    {
      "action": "probe_worker",
      "reason": "先探测并认证 Windows worker 的能力",
      "step_ids": ["ansys-static"],
      "issue_codes": ["WORKER_CAPABILITY_PROBE_REQUIRED"]
    }
  ]
}
```

聚合顺序是确定的，步骤内优先给出配置修复，其次是能力补齐、worker 探测和人工诊断；计划级列表按建议码排序，便于 CLI、Web 和 MCP 做稳定 diff。未知错误码永远回退到 `inspect_diagnostics`，避免新增检查因为忘记更新映射而被误判为可执行。

这些建议只提供调度信息，不授予执行权限。Agent 仍必须重新运行 preflight，确认 `ready=true`，再把同一份
`operation_contract` 传给执行入口；`next_actions` 不会绕过事务、锁、停止确认、实机证据或独立结果验证。

## 典型结果

动作缺少适配器能力时：

```json
{
  "code": "ADAPTER_CAPABILITY_MISSING",
  "message": "required-capability"
}
```

本地动作声明 worker 特性时：

```json
{
  "code": "WORKER_FEATURE_REQUIRES_BRIDGE",
  "message": "durable_jobs"
}
```

远程 worker 已连接但没有特性时：

```json
{
  "code": "WORKER_FEATURE_MISSING",
  "message": "artifact_streaming"
}
```

这些错误会进入统一 failure envelope，并被归为 `configuration`；工程 Agent 可以修正绑定或换用满足要求的 worker，而不是重试同一个不可能成功的请求。

## 调用和测试

能力要求通过 Python、CLI、Web 和 MCP 的 preflight/operation contract 传播。CLI 示例：

```bash
engineering-agent --config ./configs/vendor-actions.example.json \
  --project-dir ./project preflight --probe \
  "CATIA更新参数，Ansys求解，Adams多体，VI-grade操稳"
```

本地回归覆盖：

```bash
.venv/bin/python -m pytest -q tests/test_capability_requirements.py tests/test_software_integration.py
```

其中 `test_preflight_does_not_call_an_unprobed_worker_action_ready` 验证未探测 worker 时返回
`probe_worker`，`test_preflight_groups_multiple_issues_into_stable_next_actions` 验证多个问题会合并为
确定性的计划级建议。MCP 的 `engineering_preflight` output schema 也会声明步骤级和计划级字段，外部
客户端无需读取实现代码即可发现这个契约。

这些测试使用受控 runner 和本地 HTTP worker，只证明 SimLoom 的能力协商、拒绝和 contract 完整性；不证明四款商业软件的安装、许可证、建模、求解或结果验收。
