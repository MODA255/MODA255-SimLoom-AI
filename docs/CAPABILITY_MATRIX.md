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
      "reason": "动作绑定、适配器能力和已探测的 worker 要求均满足"
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

MCP 客户端使用 `engineering_capabilities`。Web 客户端读取
`GET /api/capabilities`；三者的 `capability_matrix.schema_version` 和动作记录
字段相同。

## 边界和验收

该矩阵统一的是发现、配置和协议证据，不是厂商软件语义。它不能证明 CATIA
参数树、Ansys 求解数据库、Adams 曲线或 VI-grade 场景已经正确执行。四款软件的
真实闭环仍需 Windows、对应版本、许可证和真实工程文件逐款验收。

回归测试：

```bash
.venv/bin/python -m pytest -q tests/test_capability_matrix.py tests/test_capability_requirements.py
```

