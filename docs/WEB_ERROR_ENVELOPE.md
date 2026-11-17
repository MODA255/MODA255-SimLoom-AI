# Web API 错误契约

SimLoom 的 Web API 在 HTTP 状态码之外返回一个稳定的错误对象。当前版本是
`simloom-ai/error-envelope/v1`。它让 Agent 可以在 Web、MCP、Windows worker 和本地
Python 入口之间复用错误分类、请求追踪和重试判断，而不必解析中文提示或 URL。

## 响应格式

发生错误时，响应仍保留旧客户端使用的 `error` 字符串和顶层 `code`，同时增加统一字段：

```json
{
  "ok": false,
  "request_id": "agent-request-42",
  "error": "INVALID_OPERATION_ARGUMENTS:...",
  "code": "INVALID_ARGUMENTS",
  "error_envelope": {
    "schema_version": "simloom-ai/error-envelope/v1",
    "code": "INVALID_ARGUMENTS",
    "message": "INVALID_OPERATION_ARGUMENTS:...",
    "request_id": "agent-request-42",
    "details": {},
    "retryable": false
  }
}
```

新客户端应优先读取 `error_envelope`：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 错误对象版本；不等于 Web 传输版本 `simloom-ai/web/v1` |
| `code` | 稳定机器码，例如 `INVALID_ARGUMENTS`、`RESOURCE_NOT_FOUND`、`JOB_STILL_ACTIVE` |
| `message` | 给工程师看的具体说明；不能作为路由条件 |
| `request_id` | 本次 HTTP 请求的追踪编号 |
| `details` | 路径、冲突对象、校验上下文等结构化补充信息；缺省为空对象 |
| `retryable` | 是否可以在不改变输入的情况下再次观察或重试；不是“必须重试” |

HTTP 状态码仍表示传输层结果：400 通常表示输入或契约错误，403 表示来源/访问拒绝，404 表示
资源不存在，409 表示锁或工程冲突，500 表示未分类运行时错误。Agent 应先检查
`error_envelope.code`，再结合 `retryable`、`details` 和工程任务状态选择下一步。

## 请求追踪

调用方可以通过 `X-Request-Id` 提供自己的追踪编号。服务端接受非空、最多 128 个可打印字符且
不含换行的值，并在响应顶层和 `error_envelope.request_id` 原样返回。缺少或不合规时，服务端
生成 32 位十六进制编号。编号只用于关联日志和客户端事件，不参与幂等判断；软件任务的幂等仍由
其显式 `request_id` 和输入摘要决定。

成功响应暂不强制加入 `request_id`，以保持已有 Web 客户端的返回形状。需要统一追踪成功与失败的
客户端可以使用请求头和服务端日志关联；错误对象自身始终带有编号。

## 与 MCP 的关系

MCP 工具执行失败使用相同的字段语义，并在 `structuredContent.error` 中返回版本化错误对象；
JSON-RPC 传输错误仍使用 MCP 的 `error` 字段。Web 的顶层 `error` 字符串是兼容字段，不能与
MCP 的 JSON-RPC `error` 对象混用。两者都应依据稳定 `code` 进行处理：

| 情况 | Web | MCP |
| --- | --- | --- |
| 输入不符合 operation schema | HTTP 400 + `error_envelope.code=INVALID_ARGUMENTS` | `result.isError=true` + `structuredContent.error.code=INVALID_ARGUMENTS` |
| 项目/文件不存在 | HTTP 404 + `RESOURCE_NOT_FOUND` | 工具执行错误或资源读取错误 + `RESOURCE_NOT_FOUND` |
| 外部编辑、锁或任务冲突 | HTTP 409 + `CONFLICT`/`BUSY`/原始任务码 | 工具执行错误 + 对应稳定码 |
| 未知工具或未知 MCP 方法 | 不适用 | JSON-RPC 协议错误 |

## 兼容和边界

- 旧客户端可以继续读取顶层 `error` 和 `code`，无需立即升级。
- 新代码不得从 `message` 中截取错误码，也不得把所有 409 都自动重跑；例如输出被外部修改时
  需要先人工确认，`retryable` 会保持为 `false`。
- 错误封装不会把商业软件执行成功、许可证有效或工程指标合格写入响应。四软件结果仍须读取
  `engineering-result/v1`、vendor evidence 和 `real_machine_verified`。
- 本契约只覆盖 Web 请求处理层。远程 Windows worker 的文件协议继续使用
  `simloom-ai/worker/v1`，其原始错误会在控制端结果中保留并映射到工程失败分类。

## 回归

```bash
.venv/bin/python -m pytest -q tests/test_engineering_workspace.py -k 'web_errors or api_errors'
```

测试验证了调用方提供的请求 ID、服务端生成的请求 ID、旧字段兼容和完整 envelope 字段。它们不
证明任何商业软件后端已经在 Windows 实机上运行。
