# 可选 AutoCAD 后端

SimLoom 现在有一个可选的 AutoCAD Core Console 边界。它服务于 DWG/DXF 的转换和原生 AUDIT，
不把 AutoCAD 当成 SimLoom 自己的几何内核；没有 Windows 或没有授权软件时，调用会返回明确的
`AUTOCAD_REQUIRES_WINDOWS` 或 `AUTOCAD_CORE_CONSOLE_NOT_FOUND`，不会把 fixture 结果标成实机通过。

## 能力

- `autocad.status`：只读检查当前平台、Core Console 可执行文件和能力，不启动 AutoCAD。
- `autocad.audit`：始终可以对 DXF 做 SimLoom 的独立原生检查；设置 `native=true` 后，才会在
  Windows 上调用 Core Console 的 `AUDIT`。
- `autocad.convert`：在事务 staging 目录中调用 Core Console 读入源文件、执行可选 AUDIT、
  `SAVEAS` 到目标文件，再独立回读并提交目标。默认 `dry_run=true`。

Core Console 路径可以写在配置的 `autocad.accoreconsole`，也可以通过
`SIMLOOM_ACCORECONSOLE`、`AGENTCAD_ACCORECONSOLE` 或 `AUTOCAD_ACCORECONSOLE` 指定。转换证据
包含命令、退出码、截断后的 stdout/stderr、耗时、输入哈希和输出哈希。

## 示例

```json
{
  "autocad": {
    "accoreconsole": "C:/Program Files/Autodesk/AutoCAD 2027/accoreconsole.exe"
  }
}
```

```json
{
  "operation": "autocad.convert",
  "arguments": {
    "project_dir": "C:/SimLoom/project",
    "input_file": "inputs/plate.dxf",
    "output_file": "artifacts/plate.dwg",
    "audit": true,
    "dry_run": false
  }
}
```

这只证明后端协议和文件契约已经具备。完成 AgentCAD 级别的 DWG 能力还需要在 Windows 交互式
会话中，用固定版本的 AutoCAD 做真实往返、AUDIT 差异、文件锁冲突、中文路径和失败恢复验收。
