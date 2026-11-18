# 工作台连接设置

工作台现在提供一个本地“软件连接设置”卡片，用来修改控制端连接 Windows worker 时最常变化的字段。它服务于 SimLoom AI 的控制面：CATIA、Ansys、Adams 和 VI-grade 仍然由各自软件及 runner 执行，设置页不会自动安装、启动或验证商业软件。

## 可编辑字段

每款软件都有三项：

- `bridge_url`：Windows worker 的 HTTP/HTTPS 地址，例如 `http://192.168.1.20:8099`；
- `executable`：本机启动程序提示，只用于诊断展示，不会单独触发软件；
- `bridge_token_env`：读取访问令牌的环境变量名，默认 `SIMLOOM_BRIDGE_TOKEN`。

页面还可以保存 Windows worker 的默认监听地址、端口、根目录和超时。端口范围是 1～65535，超时范围是 1～86400 秒。

## 保存位置与合并规则

工作台使用任务数据库所在目录的 `simloom-runtime-config.json` 保存运行时覆盖。例如桌面模式的数据目录下会形成：

```text
SimLoom AI/
├── workspace.db
└── simloom-runtime-config.json
```

启动服务时，运行时文件会覆盖主配置中的同名连接字段；动作脚本、输入输出契约、MCP 策略和安全开关仍来自主配置文件。保存时会先写临时文件、刷新磁盘并原子替换，避免进程中断留下半份 JSON。

保存后当前进程会立即重建 planner、vendor adapters、Windows helper 和 CAD helper，不需要重启工作台。点击“重新检测”才会执行新的健康探测；打开设置页不会自动访问 worker。

设置页同时显示动作绑定与 worker profile。它会汇总主配置中的脚本、命令、输入输出、能力要求和恢复策略，并允许下载脱敏的 `simloom-worker-profile.json`。profile 的字段和状态定义见 [`ACTION_PROFILE.md`](ACTION_PROFILE.md)。它只代表配置诊断；完整输入输出 contract 仍需在具体工程目标下执行 plan-level preflight。

## 安全边界

运行时配置只接受白名单字段。它拒绝未知工具、未知字段、控制字符、非法 URL、非法环境变量名、越界端口和超时。`bridge_token`、API key、密码和动作命令不会被页面读取或写入；worker 令牌必须由环境变量提供。

页面显示“连接正常”只代表认证后的 worker 健康接口可访问。它不能证明 CATIA/Ansys/Adams/VI-grade 已安装、许可证可用、脚本正确或工程结果通过。完整状态仍以 `/api/doctor`、`/api/capabilities`、preflight 和真实 Windows 证据为准。

## HTTP 接口

`GET /api/settings` 返回脱敏后的当前设置、动作 profile 和保存路径。`POST /api/settings` 接收同样的 `settings` 对象，并返回保存后的脱敏快照和 profile。`GET /api/settings/profile` 下载脱敏的 worker profile：

```json
{
  "settings": {
    "schema_version": "simloom-ai/runtime-config/v1",
    "tools": {
      "catia": {
        "bridge_url": "http://192.168.1.20:8099",
        "executable": "",
        "bridge_token_env": "SIMLOOM_BRIDGE_TOKEN"
      }
    },
    "windows_bridge": {
      "host": "127.0.0.1",
      "port": 8099,
      "root_dir": "C:/SimLoom/jobs",
      "timeout_seconds": 1800
    }
  }
}
```

接口只改变当前控制端的连接声明，不会因为保存而发起软件任务。配置动作脚本仍需在主配置文件中明确声明，并且必须通过 preflight。

## 验证范围

运行时配置的 schema、原子写入、拒绝未知/非法字段、当前进程即时生效和 Web 脱敏返回均有自动化测试。测试证明的是控制面配置行为；四款商业软件的 Windows 实机连接、版本和许可证仍需单独验收。
