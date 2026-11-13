# 工程依赖扫描

`project.dependency_scan` 为软件任务准备阶段生成一个保守的依赖图。它扫描项目内可读取的
文本引用、可选的 `simloom.dependencies.json` 显式清单和已知配套文件（例如 `model.dxf` 与
`model.dxf.cad.json`），输出文件节点、关系、缺失目标、外部目标、哈希和解析状态。

清单示例：

```json
{
  "dependencies": [
    {"source": "assembly.xml", "targets": ["parts/body.CATPart"], "relation": "assembly_link"},
    {"source": "vehicle.sim", "target": "results/motion.csv", "relation": "result_output"}
  ]
}
```

CATPart、CATProduct、Workbench、Mechanical、Adams 和 VI-grade 的专有二进制内容会标成
`parse_status: "opaque"`，仍然记录大小和 SHA-256；扫描器不会从猜测中声称装配拓扑或软件内部
依赖已经解析。路径指向项目外部时进入 `external`，项目内但不存在的目标进入 `missing`。
`complete` 只有在没有缺失项和清单错误时才为真，仍需在 Windows 实机上让对应软件打开并回读
原生工程作为最终验收。

Python、CLI、HTTP 和 MCP 入口：

```python
graph = agent.execute_operation("project.dependency_scan", {"project_dir": project})
```

命令行使用 `dependencies --root <project> [paths...]`；HTTP 使用
`GET /api/dependencies?root=...&path=...`；MCP 可调用 `operation.project.dependency_scan` 或读取
`engineering://dependencies?root=...`。
