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
- `artifacts` 只包含项目相对路径、大小和 SHA-256，不包含本机绝对路径。

## 兼容策略

旧客户端仍可读取 `details.verification`、`details.vendor_validation` 和 `details.output_manifest`。新 Agent 应优先读取 `details.engineering_result`，只有在 `schema_version` 不支持时才回退到旧字段。该 envelope 不会把未运行的软件伪装成已验证，也不会把失败的 vendor report 误判为工程指标失败；报告本身无效时，`engineering_acceptance` 为 `unknown`。

## 入口和测试

统一结果会从 CATIA、Ansys、Adams 和 VI-grade 的本地/远程 adapter 返回，并出现在工作流、CLI、Web 和 MCP 的结构化结果中。MCP operation schema 在 vendor operation 的 `details` 中声明了该 envelope。

```bash
.venv/bin/python -m pytest -q tests/test_engineering_result.py tests/test_four_vendor_runners.py tests/test_software_integration.py
```

该契约统一的是 Agent 读取结果的方式，不替代各软件自己的原生数据库读取器，也不替代 Windows 许可证环境中的真实验收。
