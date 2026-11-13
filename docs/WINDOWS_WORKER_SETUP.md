# Mac 控制端与 Windows 软件工作站

SimLoom AI 的控制端可以运行在 macOS。CATIA、Ansys、Adams 和 VI-grade 保持安装在一台
Windows 工作站上，由 Windows worker 在本机用户桌面会话中启动脚本、管理作业并回传文件。
控制端不需要共享 Windows 路径，也不会把本地 fixture 当成商业软件证据。

## Windows 工作站

建议使用独立的 x86-64 Windows 工作站，安装与项目绑定的 Python、Git、SimLoom AI、四款
工程软件、对应许可证客户端和项目所需的厂商 SDK。先固定以下信息并写入项目配置：

- CATIA V5 或 3DEXPERIENCE 的产品和版本；
- Ansys 的具体产品（Mechanical、MAPDL、Fluent、AEDT 等）和版本；
- Adams 的产品线和版本；
- VI-grade 的具体产品、版本和场景入口；
- Python 版本、许可证服务器和结果导出格式。

不要把四款桌面软件放进 Docker。worker 可以作为 Windows 服务被监控，但真正的 worker 进程
应在已登录的工程师用户桌面会话中运行，以便使用 COM、图形界面和用户许可证。

## 安装与启动

在 Windows 项目目录中执行：

```bat
install-windows.cmd
```

然后复制配置并填入真实路径。配置中的 `commands` 必须是 argv 数组，不能是 shell 字符串；
每个动作至少要包含 `{script}` 占位符：

```json
{
  "windows_bridge": {
    "root_dir": "D:/SimLoom/worker",
    "host": "0.0.0.0",
    "port": 8099,
    "token": "use-a-long-random-token",
    "timeout_seconds": 1800,
    "commands": {
      "catia.create_or_update_model": [
        "C:/SimLoom/.venv/Scripts/python.exe",
        "C:/SimLoom/scripts/catia_runner.py",
        "{script}",
        "{request_file}"
      ]
    },
    "com": {}
  }
}
```

启动 bridge：

```bat
.venv\Scripts\engineering-agent.exe --config worker.json bridge --bridge-config worker.json
```

生产环境应通过防火墙、VPN/Tailscale 或反向 TLS 代理限制访问来源。示例端口不应直接暴露到
公网；`X-Bridge-Token` 只是应用层认证，不能替代网络隔离和 TLS。

## Mac 端连接

四个工具的配置只需要填写 Windows worker 的地址和 token 环境变量：

```json
{
  "tools": {
    "catia": {
      "bridge_url": "http://win-sim-01:8099",
      "bridge_token_env": "SIMLOOM_BRIDGE_TOKEN"
    },
    "ansys": {
      "bridge_url": "http://win-sim-01:8099",
      "bridge_token_env": "SIMLOOM_BRIDGE_TOKEN"
    }
  }
}
```

Mac 端先调用只读接口：

```text
GET /health
GET /capabilities
```

`/capabilities` 返回四款软件已配置的动作、命令检查和协议能力；`real_machine_verified` 在
真实软件动作完成前保持 `false`。它不会因为路径存在、COM dispatch 成功或 fixture 运行成功
而宣称软件已验收。

之后再提交 `job.submit`。每个作业写入独立的 `root_dir/jobs/<request_id>/`，保存输入、脚本、
stdout、stderr、输出文件和 SHA-256 清单。Mac 断线时用同一个 request ID 查询状态或回收结果，
不能重新提交一个不确定是否已经启动的求解。

## 四款软件的第一条真实 smoke test

先逐款通过一个最小闭环，再运行跨软件链：

1. CATIA：打开副本、修改一个明确命名的参数、保存并重新打开，导出 STEP 和参数报告。
2. Ansys：打开模板工程、修改一个明确参数、执行最小求解、读取一个结果值和原生结果文件。
3. Adams：加载团队已有的最小模型和 command file，运行短时间仿真，导出 JSON/CSV 指标。
4. VI-grade：运行一个官方或团队维护的基线场景，导出原始结果和指标报告。

每条 smoke test 都必须记录软件版本、机器名、许可证状态、输入哈希、脚本哈希、输出哈希、
日志和截图。只有真实 Windows 软件运行并通过独立结果契约，才可以把动作标记为实机证据。

## 远程开发和 CI

Mac 上继续提交代码到 GitHub；Windows 工作站拉取同一提交并运行 worker。需要远程编辑时可以
使用 VS Code Remote SSH 或 Windows 终端，但软件运行仍留在 Windows 桌面会话。

离线单元测试放在 Mac/CI；真实商业软件 smoke test 使用私有 self-hosted Windows runner，按
软件设置独立标签，例如 `windows-catia`、`windows-ansys`、`windows-adams` 和 `windows-vigrade`。
公共 runner 和本地 fixture 只能验证协议、文件契约和恢复逻辑，不能替代授权软件验收。
