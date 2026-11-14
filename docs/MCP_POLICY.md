# MCP 工具访问策略

SimLoom 的 MCP server 可以在配置中限制可发现、可调用的工具和 typed operation。策略只控制
MCP 表面；Python、CLI、Web 和 Windows worker 仍使用各自的权限与事务边界。

## 配置

在控制端配置中增加 `mcp` 对象。四个字段都接受精确名称或带 `*` 的 shell 风格通配符：

```json
{
  "mcp": {
    "allow_tools": ["engineering_plan", "engineering_preflight", "operation.cad.*"],
    "deny_tools": ["operation.cad.script_apply"],
    "allow_operations": ["cad.document_*", "cad.entity_inspect"],
    "deny_operations": ["cad.entity_remove_duplicates"]
  }
}
```

空的 allow-list 表示不限制这一层。deny 规则优先于 allow 规则。operation 规则可以写成
`cad.entity_move` 或完整的 `operation.cad.entity_move`；工具规则始终使用完整工具名。

策略同时作用于：

- `tools/list` 返回的工具；
- `tools/call`，即使客户端跳过 discovery 直接调用；
- `engineering_operations` 工具和 `engineering://operations` 资源中的 operation schema。

因此客户端不能通过读取 operation 目录绕过 discovery 策略。被策略拒绝的已知工具返回 MCP
工具执行错误：`result.isError=true`、文本 `content` 和 `structuredContent.error`，错误码为
`MCP_POLICY_DENIED`。它仍然是一次合法的 MCP 请求；未知工具继续返回 JSON-RPC 错误。

## 与 safe/full profile 的关系

allow/deny 策略和 MCP profile 叠加：safe profile 仍隐藏并拒绝写入、执行和取消类工具；策略
只能进一步缩小范围，不能把 safe profile 变成 full profile。full profile 也会继续受到策略限制。

`initialize` 返回生效后的策略摘要，便于外部客户端记录本次会话使用的访问边界。配置为空或
没有 `mcp` 字段时，行为与此前版本兼容。

