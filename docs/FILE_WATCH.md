# 外部工程文件监测

CATIA、Ansys、Adams 和 VI-grade 仍然负责打开、求解和保存自己的原生工程文件。SimLoom AI
提供 `project.watch_start` / `project.watch_poll` / `project.watch_status` / `project.watch_stop`
作为结果回收边界：它保存项目内文件的 SHA-256、大小、修改时间和稳定性，并在后续轮询时报告
新增、修改、仅时间戳变化、删除和仍在写入的文件。

监测状态存放在 `.engineering-agent/watchers/`，每次轮询都会更新持久游标和最多 200 个事件，
因此 worker、Web 服务或 MCP 客户端重启后可以继续读取同一个 `watch_id`。轮询使用项目锁；文件在
读取前后大小或修改时间不一致时返回 `unstable`，避免把厂商软件尚未写完的结果误报为完整结果。
监测器只读工程文件，不会修改或解析它们，也不会把文件变化当成求解成功；结果仍需通过对应的
vendor evidence 和指标验证器判断。

Python 示例：

```python
watch = agent.execute_operation("project.watch_start", {
    "project_dir": project,
    "paths": ["results/solver.rst", "results/motion.csv"],
    "label": "solver outputs",
})
events = agent.execute_operation("project.watch_poll", {
    "project_dir": project,
    "watch_id": watch["watch_id"],
})
```

CLI 对应命令是 `watch-start`、`watch-poll`、`watch-status` 和 `watch-stop`。HTTP 使用
`POST /api/project-watch`，`action` 取 `start`、`poll`、`status` 或 `stop`；状态也可以通过
`GET /api/project-watch?root=...&watch_id=...` 读取。Web 工作台的“结果文件监测”卡片也使用同一
接口。完整 MCP profile 会发现这些操作；safe
profile 隐藏会写入监测状态的操作。
