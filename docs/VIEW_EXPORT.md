# 独立离线工程预览

`project.asset_export` 将项目内的 DXF、STEP、CSV、JSON 或网格资产导出为一个可直接分享的 HTML 文件。导出文件内嵌 SVG 预览、修改记录和可选的 JSON 元数据，不依赖 SimLoom 服务、网络、外部脚本或静态资源。

导出属于普通写 operation：输入文件先取 SHA-256，HTML 在事务 staging 目录生成，随后经过 schema 标识、SVG、时间线、路径泄漏和外部资源检查；提交后再次读取目标文件并核对哈希。任何失败都会保留事务证据并恢复目标文件。

## CLI

```bash
engineering-agent --project-dir ./project asset-export drawing.dxf exports/drawing.html --title "悬架支架"
```

使用 `--no-metadata` 可以省略 HTML 中的嵌入式 JSON 元数据。

## Python / MCP

```python
agent.execute_operation("project.asset_export", {
    "project_dir": "./project",
    "input_file": "drawing.dxf",
    "output_file": "exports/drawing.html",
    "title": "悬架支架",
    "include_metadata": True,
})
```

MCP full profile 中的工具名是 `operation.project.asset_export`。safe profile 仍只允许读取和观察，不允许导出写入。

Web 工作台提供 `POST /api/asset-export`，生成后可通过已有的 `/api/download` 下载。

该文件用于 Agent 和工程师快速查看、分享和审计本地资产；它不代表 CATIA、Ansys、Adams 或 VI-grade 的原生模型、求解结果或实机验收。
