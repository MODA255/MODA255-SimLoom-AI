# 统一能力矩阵

SimLoom 的四款软件入口都需要回答同一个问题：某个动作现在能不能交给
runner 执行？“配置文件里写了动作”只能说明动作被声明过，不能说明命令存在、
Windows worker 在线、商业软件已安装或结果已经通过工程验收。

从本版本开始，`EngineeringAgent.capabilities()` 返回一个嵌入式的
`capability_matrix`。CLI `capabilities`、Web `GET /api/capabilities` 和 MCP
`engineering_capabilities` 都直接复用这一份结构。原有的 `tools[*].actions`、
`ready_actions` 和 `action_requirements` 保留，用于兼容旧客户端；新 Agent 应优先
读取 `capability_matrix`。

## 契约

矩阵的版本是 `simloom-ai/capability-matrix/v1`：

```json
{
  "schema_version": "simloom-ai/capability-matrix/v1",
  "probe": true,
  "matrix_digest": "<sha256 of the canonical matrix without matrix_digest>",
  "summary": {
    "total": 1,
    "ready": 1,
    "missing": 0,
    "not_probed": 0,
    "unavailable": 0,
    "discovered": 1,
    "real_machine_verified": 0
  },
  "actions": [
    {
      "tool": "catia",
      "name": "update_parameters",
      "status": "ready",
      "ready": true,
      "requirements": {
        "adapter_capabilities": ["parametric-geometry"],
        "worker_features": ["durable_jobs"]
      },
      "missing": {
        "adapter_capabilities": [],
        "worker_features": []
      },
      "discovered": true,
      "real_machine_verified": false,
      "evidence": {
        "level": "worker_reachable",
        "worker_reachable": true,
        "real_machine_verified": false
      },
      "reason": "动作绑定、适配器能力和已探测的 worker 要求均满足",
      "next_action": "run_preflight"
    }
  ],
  "tools": {
    "catia": {
      "actions": [
        {
          "tool": "catia",
          "name": "update_parameters",
          "status": "ready",
          "ready": true,
          "requirements": {"adapter_capabilities": ["parametric-geometry"], "worker_features": ["durable_jobs"]},
          "missing": {"adapter_capabilities": [], "worker_features": []},
          "discovered": true,
          "real_machine_verified": false,
          "evidence": {"level": "worker_reachable", "worker_reachable": true, "real_machine_verified": false},
          "reason": "动作绑定、适配器能力和已探测的 worker 要求均满足"
        }
      ],
      "summary": {
        "total": 1,
        "ready": 1,
        "missing": 0,
        "not_probed": 0,
        "unavailable": 0,
        "discovered": 1,
        "real_machine_verified": 0
      }
    }
  }
}
```

`actions` 是跨软件的扁平列表，`tools[tool].actions` 是同一记录按软件分组的
视图。两处数据必须相同；客户端不需要再根据 `doctor()` 的不同字段自行推导。
`summary` 只做计数，不能替代逐动作判断。

`next_action` 是给 Agent 的保守下一步提示，不是自动执行授权：

| `next_action` | 含义 |
| --- | --- |
| `run_preflight` | 动作满足当前发现条件，先运行项目级 preflight 再决定是否执行 |
| `probe_worker` | 动作依赖远程 worker 能力，先进行认证探测 |
| `configure_capabilities` | 补齐动作声明的适配器能力或 worker 特性 |
| `configure_action_binding` | 检查脚本、命令、桥接地址或动作绑定 |
| `inspect_diagnostics` | 读取完整 doctor 诊断后再决定处理方式 |

客户端仍必须遵守 `ready`、operation contract 和独立结果验证；`next_action` 不会绕过
权限、事务、锁、实机证据或 preflight。

`matrix_digest` 是把矩阵去掉自身摘要字段后，按 UTF-8、排序键和紧凑 JSON 编码计算的
SHA-256。它不包含时间戳，因此相同配置和同一探测模式会得到稳定值；worker 能力、动作
绑定、实机 evidence 或 `probe` 模式改变时，摘要会改变。客户端可以把它和保存的计划或
operation contract 一起记录，发现执行前置条件已经漂移时重新做 preflight。

## 状态和证据等级

| 字段 | 含义 |
| --- | --- |
| `ready` | 当前声明的适配器能力满足，启动命令已找到；远程动作还必须完成本次 worker 能力探测 |
| `missing` | 绑定存在，但缺少 `adapter_capabilities` 或 `worker_features` |
| `not_probed` | 动作需要远程 worker 特性，本次检查没有探测 worker；不能当作可执行 |
| `unavailable` | 动作已声明，但命令、桥接或其他绑定条件不可用 |
| `declared` | 只有动作声明，尚未找到本地启动程序或 worker |
| `executable_found` | 本地启动程序已找到，或远程动作已发现对应命令 |
| `worker_reachable` | 远程 worker 健康检查和能力发现成功；这仍不是商业软件验收 |
| `real_machine_attested` | worker 为这个动作保存了带请求编号的实机证据；证据内容仍需按工程规则审核 |

`real_machine_verified` 只在 worker 的 evidence 记录明确包含该动作时为 `true`。
fixture runner、fake COM、命令存在和 worker 在线都不会提升这个字段。

## 探测策略

默认的 `probe=true` 会对已配置的远程 worker 做认证后的 health/capabilities 请求，
但不会启动任何商业软件。需要离线查看本地动作声明时使用 `probe=false`：

```bash
engineering-agent --config ./configs/vendor-actions.example.json \
  capabilities --no-probe
```

MCP 工具 `engineering_capabilities` 和 `engineering_doctor` 接收布尔参数 `probe`；
能力资源也支持 `engineering://capabilities?probe=false`。Web 使用
`/api/capabilities?probe=false` 或 `/api/doctor?probe=false`。未探测时，依赖 worker
特性的动作返回 `status=not_probed`，不会被计入 `ready`。

## Agent 调用规则

Agent 在准备执行时应按以下顺序处理：

1. 读取 `capability_matrix.actions`，按 `tool` 和 `name` 找到目标动作。
2. `status=ready` 才进入 preflight 或 execute；`not_probed` 先要求远程探测，
   `missing` 根据 `missing` 字段修正配置，`unavailable` 转人工处理。
3. 把 `requirements` 原样放入 operation contract，避免不同入口各自猜测要求。
4. 只有 `real_machine_verified=true` 才能向用户描述“这个动作已有实机验收证据”；
   `ready=true` 只表示 SimLoom 的启动条件满足。
5. 执行失败时继续使用统一 error envelope 和 `engineering_result`，不要把状态
   计数当作结果指标。

CLI 示例：

```bash
engineering-agent --config ./configs/vendor-actions.example.json \
  capabilities
```

MCP 客户端可以调用 `engineering_capabilities`，也可以读取只读资源
`engineering://capabilities`。后者适合先做发现、再决定是否调用动作；资源模板也会在
`resources/templates/list` 中出现。Web 客户端读取 `GET /api/capabilities`；三者的
`capability_matrix.schema_version` 和动作记录字段相同。

## 边界和验收

该矩阵统一的是发现、配置和协议证据，不是厂商软件语义。它不能证明 CATIA
参数树、Ansys 求解数据库、Adams 曲线或 VI-grade 场景已经正确执行。四款软件的
真实闭环仍需 Windows、对应版本、许可证和真实工程文件逐款验收。

回归测试：

```bash
.venv/bin/python -m pytest -q tests/test_capability_matrix.py tests/test_capability_requirements.py
```

