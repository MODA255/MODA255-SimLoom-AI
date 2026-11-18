# 动作绑定与 Windows worker profile

SimLoom AI 的工作职责是驱动已经安装的 CATIA、Ansys、Adams 和 VI-grade，收集原生输出，再执行独立校验。为了让工程师在连接 Windows 工作站前就能发现配置缺口，工作台提供了 `simloom-ai/action-profile/v1`。

## profile 解决什么问题

主配置中的一个软件动作通常同时包含脚本、启动命令、输入文件、输出文件、验证声明、能力要求和恢复策略。只看连接地址无法判断动作是否真正绑定；只看 worker 健康接口也无法判断控制端是否携带了正确脚本。profile 把这些声明按软件和动作整理在一起，页面可以逐项显示：

- 动作是否存在，以及脚本路径是否声明；
- 本机命令或 `windows_bridge.commands` 是否是非空 argv，是否包含 `{script}`；
- 脚本在当前项目中是否存在、为空或越界（如果请求带 `root`）；
- 输入、输出和验证声明是否已经写入动作；
- 适配器能力、worker 特性和恢复策略；
- 稳定的 `binding_digest`，用于发现动作声明被修改；
- 当前建议是补配置、执行 plan-level preflight，还是把检查交给 Windows worker。

它还生成可下载的 `simloom-worker-profile.json`。这个文件是脱敏的 worker 配置模板，便于在 Windows 机器上登记动作和命令。profile 不包含 bridge token、密码、API key 或其他密钥；令牌仍然只能由环境变量提供。

## 状态含义

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `missing` | 没有动作绑定 | 在主配置中补充 `tools.<tool>.actions.<action>` |
| `needs_configuration` | 脚本、命令、能力要求或恢复策略存在阻塞问题 | 按 `issues[]` 修正声明 |
| `ready` | 配置声明完整，控制端可以进入计划级检查 | 输入工程目标并执行 preflight |
| `deferred_to_windows_worker` | 命令格式正确，但程序路径必须在 Windows worker 上检查 | 在 Windows worker 上运行 preflight 或诊断 |

`ready` 和 `deferred_to_windows_worker` 都不是实机验收。它们不表示商业软件已安装、许可证有效、COM 可以创建对象、模型已经求解，或工程指标通过。真实验收仍需要 Windows worker 输出 `machine-evidence`，并由结果文件和独立验证器复核。

## 接口

工作台接口：

- `GET /api/settings`：返回运行时连接设置和嵌入式 `profile`；
- `POST /api/settings`：保存脱敏连接覆盖，并返回保存后的 `profile`；
- `GET /api/settings/profile`：下载 `simloom-worker-profile.json`；
- `POST /api/action-preflight`：对指定 `tool` + `action` 运行单动作 preflight；
- typed operation `project.action_preflight`：给 MCP、CLI 和 Python 客户端使用同一份单动作检查。

CLI 入口：

```bash
engineering-agent --config configs/my-engineering-agent.json \
  --project-dir ./demo action-preflight ansys solve_configured_model \
  --parameters '{"db_file":"inputs/model.mechdat","analysis_name":"Static Structural","native_output":"artifacts/solved.mechdat","result_output":"artifacts/results.json","report_output":"artifacts/evidence.json","required_metrics":["equivalent_stress"],"metric_units":{"equivalent_stress":"MPa"}}'
```

`GET /api/settings?root=/path/to/project` 会在脚本检查中使用该项目目录。没有 `root` 时只检查声明，不访问项目文件。profile 本身不会访问 Windows worker；需要健康检查或动作能力时，继续使用 `/api/doctor`、`/api/capabilities` 或带具体工程目标的 `/api/preflight`。

单动作 preflight 请求示例：

```json
{
  "project_dir": "C:/SimLoom/projects/suspension",
  "tool": "ansys",
  "action": "solve_configured_model",
  "parameters": {
    "db_file": "inputs/model.mechdat",
    "analysis_name": "Static Structural",
    "native_output": "artifacts/ansys/solved.mechdat",
    "result_output": "artifacts/ansys/results.json",
    "report_output": "artifacts/ansys/evidence.json",
    "required_metrics": ["equivalent_stress"],
    "metric_units": {"equivalent_stress": "MPa"}
  },
  "probe": false
}
```

返回的 `step.operation_contract` 与计划级 preflight 使用同一个
`simloom-ai/software-operation-contract/v1`。如果 typed runner 缺少参数，返回的 `issues[]` 会明确指出
契约错误；这不会启动软件，也不会因为脚本路径存在而跳过参数和输出校验。

## 与 plan-level preflight 的关系

profile 是配置级视图，适合快速找出“命令缺失”“脚本未声明”“worker 特性要求了 bridge”等问题。它没有一个具体的 `PlanStep`，因此不能推导 typed runner 的完整输入输出清单，也不会生成最终的 `operation_contract.definition_digest`。

当工程师输入工程目标后，plan-level preflight 会复用实际 adapter 的 `file_contract()`，补齐 typed runner 的输入、输出和验证 contract，再检查项目内文件、远程 worker 能力和动作声明。执行请求携带的 `definition_digest` 必须和这次 preflight 结果一致；profile 的 `binding_digest` 用来追踪主配置变化，不能替代 operation contract digest。

推荐顺序是：

1. 在设置页查看四款软件的动作 profile；
2. 下载脱敏 profile，交给 Windows worker 配置人员；
3. 在工作台输入真实工程目标，点击“检查启动条件”；
4. 需要远程证据时再探测 worker，并保存 preflight 输出；
5. 执行动作后收集原生文件、报告和 machine evidence。

## 配置示例

```json
{
  "tools": {
    "ansys": {
      "bridge_url": "http://192.168.1.20:8099",
      "actions": {
        "solve_configured_model": {
          "script": "examples/vendor_runners/ansys_mechanical_solve.py",
          "outputs": ["artifacts/ansys/results.json"],
          "capability_requirements": {
            "adapter_capabilities": ["static-structural", "results"],
            "worker_features": ["durable_jobs", "job_logs"]
          },
          "recovery_policy": {
            "on_timeout": "require_human",
            "on_disconnect": "reconnect",
            "requires_stop_confirmation": true
          }
        }
      }
    }
  },
  "windows_bridge": {
    "commands": {
      "ansys.solve_configured_model": [
        "C:/SimLoom/python/python.exe",
        "{script}",
        "{request_file}"
      ]
    }
  }
}
```

profile 会显示命令来自 `windows_bridge.commands`，并把程序路径检查标记为 `WINDOWS_CHECK_DEFERRED`。Windows worker 仍需实际解析程序、启动 runner、等待 Ansys 求解结束并验证输出；仅仅把命令写入 profile 不会产生成功记录。

