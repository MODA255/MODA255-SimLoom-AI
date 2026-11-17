# Windows worker 诊断

SimLoom 的控制端可以在 Mac/Linux 上运行，四款商业软件仍在 Windows worker
桌面会话中执行。为了让 Agent 和工程师区分“配置错误”和“真实软件未验收”，worker
提供只读诊断接口：

```text
GET  /diagnostics
POST operation=diagnostics
```

请求需要和其他 worker 接口相同的 `X-Bridge-Token`。诊断不会启动 runner、COM
对象或厂商软件，也不会修改工程文件。

## 返回内容

响应版本为 `simloom-ai/worker-diagnostics/v1`，主要字段如下：

| 字段 | 含义 |
| --- | --- |
| `platform` | worker 运行平台；macOS/Linux 会明确列出 `WINDOWS_HOST_REQUIRED` |
| `root` | worker 根目录是否存在、是否可写 |
| `authentication` | 是否配置 token，只返回布尔值和 header 名，不返回密钥 |
| `commands` | 按软件和动作列出 argv 模板是否合法、可执行程序是否找到以及解析路径 |
| `com` | 已声明的 ProgID；`dispatch_probe` 固定为 `not_run`，诊断不执行 COM |
| `features` | durable jobs、日志和软件串行化等协议特性 |
| `evidence` | machine evidence 的总数、有效/无效数、软件分布和许可证状态 |
| `readiness` | worker 健康、动作配置、可启动动作和实机证据计数 |
| `issues` / `warnings` | 需要修复的配置问题和非阻断提示 |

`readiness.execution_ready=true` 只表示至少有一个配置动作的启动程序已找到。
`readiness.real_machine_verified=true` 只在有效的 `machine-evidence/v1` 记录存在时
出现；命令存在、COM ProgID、健康检查和 fixture runner 都不会提升它。

## 控制端和 Agent 使用

远程 adapter 的 `doctor()` 会把对应软件的诊断摘要放在
`worker_diagnostics` 中，MCP `engineering_capabilities` 和 Web/CLI 的能力矩阵仍
沿用 `real_machine_verified` 的保守语义。完整 worker 能力响应也包含 `diagnostics`
字段，因此客户端只需一次 `/capabilities` 请求即可获得动作能力和配置诊断。

典型排查顺序：

1. `issues` 包含 `WINDOWS_HOST_REQUIRED`：把 worker 部署到 Windows；不要在 Mac 上
   把 COM 失败当作软件失败。
2. `commands[tool].missing_actions` 非空：检查动作绑定中的命令 argv、`{script}` 占位符
   和 Windows PATH。
3. `root.writable=false`：修复 worker 根目录权限，避免任务在提交后无法写日志或结果。
4. `authentication.token_configured=false`：生产部署应配置桥接 token；本地隔离测试可
   明确接受无 token，但这会出现在 `warnings`。
5. `evidence.invalid` 大于零：先查看 evidence 的输出哈希和原始任务状态，不能用新的
   fixture 记录覆盖不确定的商业软件结果。

示例：

```bash
curl -H "X-Bridge-Token: $SIMLOOM_WORKER_TOKEN" \
  http://windows-worker:8099/diagnostics
```

此接口是配置和证据诊断，不是 CATIA、Ansys、Adams 或 VI-grade 的实机验收报告；
真实建模、求解、结果读回和许可证状态仍需在对应 Windows 软件版本上逐款验证。

