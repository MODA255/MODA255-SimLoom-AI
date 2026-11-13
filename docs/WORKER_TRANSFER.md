# Worker 大文件传输

真实 CAD、网格和结果文件经常超过一次 JSON 请求适合承载的大小。worker 保留原有的小文件
内联协议，同时提供 `simloom-ai/worker-transfer/v1` 的持久分块传输。控制端会在输入文件超过
32 MiB 时自动启用它；输出总量超过 32 MiB 时，worker 返回哈希清单，控制端按块回收。

分块大小上限为 4 MiB，当前总传输上限为 4 GiB。每个文件都先声明路径、字节数和 SHA-256，
每个块也有独立 SHA-256。上传写入 `control/transfers/<transfer_id>/`，完成前不会被作业引用；
完成时再次按文件哈希检查。相同偏移和相同块可以安全重试，偏移不一致会失败，避免网络重试
造成静默拼接错误。

## 控制端流程

控制端使用同一个 request ID 作为 transfer ID：

```text
artifact.upload_begin
  -> artifact.upload_chunk (按文件和 offset 重复)
  -> artifact.upload_complete
  -> job.submit (files 为 manifest，file_transfer_id 指向已完成上传)
```

作业完成后，<= 32 MiB 的输出仍直接出现在 `job.result.outputs`。更大的输出返回
`transfer_required: true` 和 `path/bytes/sha256`，控制端使用：

```text
artifact.download_chunk
```

逐块下载，并在本地重新计算完整 SHA-256。任何清单、偏移、块哈希或最终文件哈希不匹配都会
使软件任务失败；不会把不完整文件发布到项目事务。

大文件的 vendor evidence 校验也不要求先把整个结果文件读入内存。worker 和控制端会对原生
产物按块流式计算 SHA-256；只有契约声明需要解析的 STEP、CSV、JSON 指标文件才会被读取内容。
因此网格、求解数据库和其他大型原生结果可以先完成完整性校验，再按需下载或交给专用查看器，
不会因为证据验证而复制一份完整内存副本。

## 断线恢复

上传目录和远程作业状态都位于 worker 的持久根目录。控制端断线后可以用同一个 transfer ID
查询 `artifact.upload_status`，从每个文件的 `written` 偏移继续上传；已完成的作业仍通过
request ID 查询和回收。控制端不会因为观察超时而重新启动相同求解。

## 限制

分块协议解决传输大小和断线重试，不解决软件本身的内存、磁盘、许可证或求解器限制。上传目录
需要定期清理策略；当前保留目录用于恢复和审计。worker 仍应只在受控网络或 TLS 代理后开放，
应用层 token 不能替代网络隔离。

```bash
.venv/bin/python -m pytest tests/test_artifact_transfer.py \
  tests/test_software_integration.py::test_http_bridge_chunks_large_input_without_shared_paths \
  tests/test_software_integration.py::test_http_bridge_downloads_large_output_in_chunks -q
```
