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

生成器只写 MCP 客户端配置，不会启动 server，也不会修改工程文件。`--output` 使用临时文件替换
目标文件，父目录不存在时会创建。

