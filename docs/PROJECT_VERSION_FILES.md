# 工程原始文件版本

项目历史页的「保存当前项目版本」现在同时保存原始文件与解析后的工程对象。
每个版本记录相对路径、文件大小和 SHA-256，原始字节存放在
`.engineering-agent/versions/<version_id>/files/`；版本清单仍为同目录下的
`<version_id>.json`。原始文件、清单和时间线在同一个项目事务中提交。

## 日常使用

1. 打开项目，在「项目历史」填写版本名称并保存。
2. 点击「查看快照」，切换其中的资产。DXF 使用保存的原生图纸预览并禁用编辑；
   CSV 使用完整历史文件绘图，STEP/OBJ/结果场沿用现有预览器的支持范围。
3. 「下载」读取对应版本的文件，即使当前文件已被修改或删除，下载内容仍来自快照。
4. 填写一个新的项目子文件夹，点击「导出完整版本」。导出保留相对目录结构，
   重新检查每个文件的哈希，可用于后续软件任务的输入。

CATIA、Ansys、Adams、VI-grade 的原生文件可以按原始字节保存、下载和导出。
不具备本地解析器的格式仅保存文件和来源信息；保存成功不代表对应软件能打开、求解
或认可其工程内容。测试中的厂商扩展名文件是合成二进制 fixture，不是厂商实机证据。

DXF 的 `.cad.json`、STEP 的 `.brep.json` 和出图文件的 `.plot.json` 会随主文件一起保存。
原本不存在的配套文件也会记录；若后来被意外添加到快照内，预览和导出会拒绝使用该快照。
只在已知关系内收集这些配套文件；装配外部引用、工程数据库依赖、字体、材料库和许可证
不会自动打包。调用方应显式纳入需要保留的工程文件。

默认 Web 保存会选择可见的常见工程格式；其他文件可通过 Python/MCP/CLI 显式指定。
工作区数据库、事务日志、历史副本和项目内部元数据不会被默认收集。文件都必须位于项目
目录，内部路径、目录穿越以及文件和中间目录的符号链接会被拒绝。

## Python、MCP 和 CLI

```python
saved = agent.execute_operation("project.version", {
    "project_dir": "./project", "label": "求解前输入",
    "paths": ["geometry/part.CATPart", "analysis/model.wbpj", "results/curve.csv"],
})
exported = agent.execute_operation("project.version_export", {
    "project_dir": "./project", "version_id": saved["id"],
    "output_dir": "exports/versions/solve-input",
})
```

Python/MCP 的相对文件路径相对于 `project_dir`；CLI `version` 的输入文件参数保持原有
工作目录相对规则，`version-export` 的目标目录相对于项目根目录：

```bash
python -m engineering_agent.cli version baseline project/model.step project/results.csv --root project
python -m engineering_agent.cli version-export VERSION_ID exports/versions/baseline --root project
```

MCP 使用 `operation.project.version` / `operation.project.version_export`，需要 `full`
profile。`safe` profile 在实际调用层拒绝写入和执行操作，即使客户端绕过工具列表直接
提供名称也不能触发；可使用计划、读取和检查工具。`engineering_run` 即使排练也会保存
运行产物，因此也需要 `full`。

HTTP 提供 `POST /api/version`、`GET /api/version-view`、`GET /api/version-file`
和 `POST /api/version-export`。读取使用 `project_id`（或 `root`）、`version_id`；
单文件下载还需要快照中的相对 `path`，导出需要项目相对 `output_dir`。

## 一致性与旧版本

读取源文件、复制、解析与提交均持有项目锁。解析读取暂存副本；复制前后以及提交后的
源哈希都要一致，外部软件改写或新增/删除配套文件会使整次保存失败。
落盘快照验证失败或进程中断时，现有事务恢复机制恢复整个文件组和时间线。
遵守项目锁的写入不会交错；外部软件不遵守该锁时仍建议在保存完成后创建版本。

导出目标文件夹必须不存在，每个目标文件还带有「原本不存在」的提交前提，
防止外部程序在检查之后创建同名文件而被覆盖。导出中途失败时恢复整个文件组；
可能留下空目录，但不会留下被当作成功版本的部分文件。

旧版清单继续用于语义 diff 和摘要预览，界面标明「旧版仅保存解析摘要」。
它们没有完整原始文件，不能下载或导出；不能用当前文件补造历史原件。
低层 `VersionStore.create()` 以及空路径的命名 checkpoint 仍保留摘要语义。
新版本采用清单 schema 2，保留完整 CSV 行、JSON 非数值字段和未知格式的二进制内容。
解析失败或非有限数值会在对象元数据中标记 `parse_status: unavailable`，原始失败产物仍然
保存。渲染失败或超过预览限制时显示明确提示，保留下载与导出入口；哈希损坏和路径错误
仍会拒绝读取，不会用预览降级掩盖文件完整性问题。

每个版本保存完整副本，事务日志也保留恢复数据；目前没有去重、自动清理或存储配额。
本地预览受现有格式支持和大小上限约束，历史存储本身不宣称具备原生软件语义校验。

## 验证

```bash
python -m pytest tests/test_version_files.py tests/test_engineering_workspace.py -q
node tests/browser/project_versions.cjs
```

覆盖原件修改/删除后读取、超过样本数量的 CSV、非数值 JSON、二进制字节、配套元数据、
源文件并发变化、快照损坏、路径/符号链接、进程中断、导出失败恢复和不覆盖现有文件。
浏览器测试还覆盖真实下载、旧版降级提示、只读 CAD 视图和版本切换后的过期响应。
