# 外部 MCP 客户端接入

SimLoom 的 MCP server 使用标准 stdio 传输。安装项目后，可以用一条 CLI 命令生成客户端配置，
不需要手工拼接 Python 路径、配置文件和 profile 参数。

## 生成配置

通用 JSON（也可用于支持 `mcpServers` 的客户端）：

```bash
PYTHONPATH=src python -m engineering_agent.cli \
  --config configs/my-engineering-agent.json \
  mcp-config --client generic --profile safe
```

Claude 风格的 `.mcp.json`：

```bash
PYTHONPATH=src python -m engineering_agent.cli \
  --config configs/my-engineering-agent.json \
  mcp-config --client claude --profile safe --write --output .mcp.json
```

Codex 风格的 TOML 片段：

```bash
PYTHONPATH=src python -m engineering_agent.cli \
  --config configs/my-engineering-agent.json \
  mcp-config --client codex --profile safe
```

也可以让命令直接合并到默认配置位置：

```bash
PYTHONPATH=src python -m engineering_agent.cli \
  --config configs/my-engineering-agent.json \
  mcp-config --client codex --profile safe --write
```

这会更新 `~/.codex/config.toml` 中名为 `simloom_ai` 的 MCP server；已有其他配置会保留。
JSON 客户端默认写入当前目录的 `.mcp.json`。指定 `--output` 可以选择其他文件。

命令默认使用当前 Python 解释器。Windows 工作站上应在 SimLoom 虚拟环境中运行，或显式指定：

```bat
engineering-agent.exe --config C:\SimLoom\worker.json mcp-config ^
  --client generic --python C:\SimLoom\SimLoom-AI\.venv\Scripts\python.exe
```

默认 profile 是 `safe`，只暴露只读能力。需要执行软件任务、写入 CAD 或登记真实 Windows
证据时，必须显式使用 `--profile full`，并同时配置 MCP allow-list/deny-list 以缩小工具范围。
`--read-only` 可以强制生成 safe 配置；`--allow-scripts` 会把受控 CAD 脚本开关传给 server，
但不会绕过 safe profile 或 operation 策略。

如果 MCP 客户端会把所有工具 schema 一次性放入上下文，可以使用精简发现模式：

```bash
PYTHONPATH=src python -m engineering_agent.cli \
  --config configs/my-engineering-agent.json \
  mcp-config --client generic --profile full --toolset compact
```

`compact` 模式只发布通用入口和 operation 索引。客户端先调用
`engineering_operations` 获取轻量清单，再用 `engineering_operation_schema` 请求需要的
完整参数契约，最后通过 `engineering_operation` 调用具体 operation。底层仍使用相同的
事务、权限、schema version、验证和 rollback；默认 `all` 模式保持每个 typed operation
都可直接发现。

除了工具入口，MCP 资源也支持按需发现 operation：

- `engineering://operations?detail=false&category=ansys&limit=50` 返回按软件筛选的轻量目录；
- 把 `detail=true` 加入同一资源可以返回完整参数和输出 schema；
- `engineering://operation?name=ansys.solve_configured_model` 只读取一个完整 operation schema；
- `engineering://operation?name=add_line` 也支持 AgentCAD 兼容别名，并返回 `compatibility.alias_for`。

资源目录和 `tools/list` 使用同一个 operation registry。读取 schema 只做发现，不会启动脚本、连接
Windows worker 或改变工程文件；真正执行前仍需通过 MCP profile、operation policy 和对应的
preflight 检查。

工作流也可以通过资源接口按需观察，适合不希望把完整任务报告放入上下文的 Agent：

- `engineering://workflows?root=<project>&limit=50&cursor=<cursor>` 返回项目内保存的工程链列表，使用与其他 MCP 资源相同的游标分页。
- `engineering://workflow?root=<project>&workflow_id=<id>` 返回单条工程链摘要，包括步骤状态、原 request ID、生命周期、失败分类和 `failure_evidence`。
- 在同一 URI 加上 `detail=failures` 只返回失败步骤及其受限失败证据、日志路径、建议动作和修复循环决策。

这些 URI 是只读观察入口；资源读取不会恢复、重试或启动软件。要继续工程链，使用 full profile 的
`engineering_workflow` 工具并传 `action=resume`；恢复后可重新读取同一个 workflow resource。对
`engineering_workflow` 的 `resume` 或 `repair` 调用会向已订阅且根目录匹配的工作流资源发送
`notifications/resources/updated`，客户端再读取 URI 获取最新状态。失败证据只来自已保存的 worker
或 runner 结果，不会从当前项目文件补造历史任务信息。

生成器只写 MCP 客户端配置，不会启动 server，也不会修改工程文件。`--output` 使用临时文件替换
目标文件，父目录不存在时会创建。

