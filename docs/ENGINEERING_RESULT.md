# 统一工程结果契约

四款软件的 runner 以前分别返回 `verification`、`vendor_validation`、`output_manifest` 和不同的日志字段。为了让 Agent 不需要为每款软件写一套解析器，SimLoom 现在在这些字段旁边提供统一的 `engineering_result`：

```json
{
  "schema_version": "simloom-ai/engineering-result/v1",
  "tool": "ansys",
  "action": "solve_configured_model",
  "status": "completed",
  "verification": "artifact_and_vendor_evidence",
  "engineering_acceptance": "failed",
  "real_machine_verified": false,
  "metrics": {
    "equivalent_stress": {"value": 125.0, "unit": "MPa"}
  },
  "metric_verdicts": [
    {"name": "equivalent_stress", "passed": false}
  ],
  "observations": {"solution_status": "Done"},
  "engineering_context": {
    "schema_version": "simloom-ai/engineering-context/v1",
    "source": "explicit_vendor_export",
    "software": {
      "name": "ansys",
      "version": "2026 R1",
      "build": "2026.1",
      "license_status": "licensed"
    },
    "native_objects": [{"id": "analysis-1", "kind": "analysis", "name": "Static Structural"}],
    "coordinate_systems": [{"id": "cs-1", "kind": "cartesian", "origin": [0, 0, 0]}],
    "load_cases": [{"id": "lc-1", "name": "Gravity", "model_reference": "analysis-1"}],
    "model_references": [{"id": "mesh-1", "kind": "mesh", "path": "mesh.cdb"}]
  },
  "contract": {
    "schema_version": "simloom-ai/vendor-evidence/v2",
    "kind": "mechanical_solve",
    "report": "artifacts/report.json",
    "inputs": ["inputs/model.mechdat"],
    "outputs": ["artifacts/results.json", "artifacts/report.json"],
    "required_checks": [],
    "required_metrics": ["equivalent_stress"],
    "metric_units": {"equivalent_stress": "MPa"},
    "metric_rules": [{"name": "equivalent_stress", "operator": "<=", "target": 80, "unit": "MPa"}]
  },
  "lifecycle": {
    "schema_version": "simloom-ai/engineering-lifecycle/v1",
    "current_stage": "completed",
    "stages": {
      "preflight": {"status": "passed"},
      "execute": {"status": "passed"},
      "collect": {"status": "passed"},
      "validate": {"status": "passed"},
      "recover": {"status": "not_run"}
    },
    "recoverable": false,
    "next_action": null
  },
  "failure": {
    "schema_version": "simloom-ai/engineering-failure/v1",
    "category": "validation",
    "retryable": false,
    "requires_human": false,
    "recommended_action": "review_engineering_result",
    "confidence": "high",
    "error_code": "VENDOR_REPORT_INVALID",
    "message": "bad report"
  },
  "operation_contract": {
    "schema_version": "simloom-ai/software-operation-contract/v1",
    "tool": "ansys",
    "action": "solve_configured_model",
    "definition_digest": "...",
    "execution": {"mode": "bridge", "script": "scripts/ansys_runner.py"},
    "artifacts": {"inputs": ["inputs/model.mechdat"], "outputs": ["artifacts/results.json", "artifacts/report.json"]},
    "validation": {"kind": "mechanical_solve", "report": "artifacts/report.json"},
    "lifecycle": {"stages": ["preflight", "execute", "collect", "validate", "recover"]},
    "recovery": {"request_id": true, "reconnect": true, "workflow": true}
  },
  "artifacts": {
    "inputs": [{"path": "inputs/model.mechdat", "bytes": 100, "sha256": "..."}],
    "outputs": [{"path": "artifacts/results.json", "bytes": 200, "sha256": "..."}]
  },
  "evidence": {
    "request_id": "...",
    "scope": "file_identity_and_reported_observations",
    "verification": "artifact_and_vendor_evidence"
  }
}
```

## 字段语义

- `status` 表示 runner 生命周期：`planned`、`completed`、`failed` 或 `unavailable`。
- `verification` 表示控制端验证了什么。`artifact_integrity` 只证明文件和哈希；`artifact_and_vendor_evidence` 还通过了声明的 vendor report 和指标契约。
- `engineering_acceptance` 表示工程指标是否满足规则：`passed`、`failed`、`not_requested` 或 `unknown`。进程成功不自动等于工程验收通过。
- `real_machine_verified` 只有经过 Windows worker 的机器证据记录才可能为 `true`。本地 fixture、COM 探针和文件哈希不能填充这个字段。
- `metrics` 是已按契约单位规范化的数值，`metric_verdicts` 保存每项规则的目标、运算符和通过状态。
- `observations` 保存 runner 报告中的软件观察，例如 CATIA 参数回读、Ansys 解算状态或 Adams/VI-grade 返回码。
- `engineering_context` 是 `simloom-ai/engineering-context/v1` 的有界原生对象摘要。它可以包含软件版本/构建/许可证状态、CATIA 参数树或文档对象、Ansys/Adams/VI-grade 的坐标系、工况、场景对象和模型引用。runner 可以把它放在统一结果顶层、`vendor_validation.engineering_context`，或 `vendor_validation.observations.engineering_context`；控制端只复制 runner 或 validator 明确导出的字段，不从文件名、进程退出码或工具名称推断原生对象；缺少独立导出时字段为 `null`，不会伪造实机证据。
- `contract` 是本次请求实际使用的输入、输出和验收契约摘要。它包括 evidence schema、报告路径、输入/输出路径、必须检查的指标、期望单位和原始规则。该摘要有长度限制，适合 Agent 做失败归因、修复候选比较和重规划；它不会替代原始报告，也不会因为契约存在就把结果判为通过。
- `lifecycle` 是 `simloom-ai/engineering-lifecycle/v1` 阶段状态。五个阶段固定为 `preflight`、`execute`、`collect`、`validate`、`recover`。它记录控制端实际到达的阶段、阶段错误和是否可以用原任务 ID 恢复。`completed` 只在声明的输出已回收且验证契约通过后把 `validate` 标为 `passed`；断线或观测超时会把 `recover` 标为 `deferred`，不会把前面的阶段推断成成功。
- `failure` 是 `simloom-ai/engineering-failure/v1` 的保守分类，只在存在错误时出现。它把底层错误码归入配置、输入契约、传输、执行、回收、验证、恢复、并发、发布或未知类别，并给出 `retryable`、`requires_human` 和建议动作。未知错误默认要求人工复核；分类不会改变原始错误码，也不会把软件运行失败改成工程指标失败。
- `operation_contract` 是 `simloom-ai/software-operation-contract/v1` 的稳定动作定义。preflight、worker 请求、结果回收和工作流状态引用同一份 `definition_digest`；它只包含项目相对路径和声明式契约，不包含本机绝对路径。输入文件的实际 SHA-256 仍由 evidence contract 单独记录。
- `artifacts` 只包含项目相对路径、大小和 SHA-256，不包含本机绝对路径。

## 兼容策略

旧客户端仍可读取 `details.verification`、`details.vendor_validation` 和 `details.output_manifest`。新 Agent 应优先读取 `details.engineering_result`，只有在 `schema_version` 不支持时才回退到旧字段。`engineering_context` 缺失不影响旧结果解析，也不代表原生对象不存在；它只在 runner 明确导出后出现。该 envelope 不会把未运行的软件伪装成已验证，也不会把失败的 vendor report 误判为工程指标失败；报告本身无效时，`engineering_acceptance` 为 `unknown`。

## 入口和测试

统一结果会从 CATIA、Ansys、Adams 和 VI-grade 的 worker 响应及本地/远程 adapter 返回，并出现在工作流、CLI、Web 和 MCP 的结构化结果中。MCP operation schema 在 vendor operation 的 `details` 中声明了该 envelope。即使 worker 在启动前校验失败、runner 超时或输出契约不完整，也会返回 `status=failed` 的 envelope，并尽可能保留原始契约摘要，避免 Agent 只通过是否收到响应来判断状态。

MCP discovery 会展开 `engineering_result.contract` 的字段结构。Agent 可以在调用前发现报告路径、输入/输出路径、必需指标、单位和原始规则，不必等待一次真实执行后再猜测返回格式。
MCP discovery 同时展开 `engineering_result.lifecycle`，因此客户端可以根据 `current_stage`、`recoverable` 和
`next_action` 选择继续查询原任务、读取结果或转入人工复核。

```bash
.venv/bin/python -m pytest -q tests/test_engineering_result.py tests/test_four_vendor_runners.py tests/test_software_integration.py
```

该契约统一的是 Agent 读取结果的方式，不替代各软件自己的原生数据库读取器，也不替代 Windows 许可证环境中的真实验收。
