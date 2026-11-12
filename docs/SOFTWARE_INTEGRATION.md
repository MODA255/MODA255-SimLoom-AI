# 操作工程软件：执行契约与接入

SimLoom AI 的主要职责是操作 CATIA、Ansys、Adams、VI-grade，再读回结果并检查工程要求。
商业软件负责原生建模和求解。本地 CAD、OpenCascade、Viewer 与离线演示是辅助工具。

这里的接入目标是“让 SimLoom 驱动已安装的软件完成一次可核验的工程动作”，不是把厂商
软件替换成 SimLoom 自己的实现。CATIA、Ansys、Adams 和 VI-grade 的原生模型、网格、求解
状态和结果文件仍是权威来源；SimLoom 只负责控制面、文件契约、任务生命周期、独立检查
和审计记录。除非明确标记为离线 fixture，否则任何本地生成结果都不能作为厂商软件的
真实求解证据。

## 当前可验证的能力

- 本机和 HTTP worker 共用 `simloom-ai/worker/v1`，按 `软件.动作` 绑定启动命令。
- 每个动作明确指定项目中的可信脚本；自然语言参数写入 JSON 请求文件，不拼接进 shell。
- 脚本与输入文件按项目相对路径传输，控制端和工作站不需要共享磁盘路径。
- 每个请求使用新目录；同一编号、同一输入可重复查询或提交，但只执行一次。编号对应的输入改变会报冲突。
- 请求声明输出清单；全部文件必须存在、非空并通过大小及 SHA-256 校验，才报告完成。
- 下游读取本轮已校验的结果；失败时保留日志，整轮结果不发布到项目正式路径。
- 成功后结果和不可变历史副本在同一事务中发布。直接 operation 调用也走事务。
- `completed` 表示执行完成且所配置的文件/证据契约通过；工程阈值另看验收结论。
  没有证据契约时仍只验证文件完整性，`vendor_validation=not_run`。

执行前可以调用 `EngineeringAgent.preflight()`、CLI `preflight`、Web `POST /api/preflight`
或 MCP `engineering_preflight`。它复用同一个动作和输出契约，逐步检查动作绑定、项目内脚本、
本机启动命令或远程 worker；加 `probe=true` 时还会读取 worker 健康状态。它只给出“是否具备
启动条件”，不会启动 CATIA、Ansys、Adams 或 VI-grade，也不会把许可证、求解收敛或工程指标
合格误报成已验证。

这些能力用真实子进程和本地 HTTP 服务测试，fixture 明确标记为测试程序。
目前没有 CATIA / Ansys / Adams / VI-grade 的许可证、版本及工程操作实机验收证据。

## 配置一个软件动作

控制端配置举例（脚本必须由项目提供，下面的路径不是已安装软件）：

```json
{
  "tools": {
    "catia": {
      "bridge_url": "http://127.0.0.1:8099",
      "bridge_token_env": "SIMLOOM_BRIDGE_TOKEN",
      "actions": {
        "create_or_update_model": {
          "script": "scripts/catia_task.py",
          "inputs": ["inputs/template.CATPart"],
          "outputs": ["artifacts/model.step"]
        }
      }
    }
  }
}
```

Windows worker 配置举例：

```json
{
  "windows_bridge": {
    "root_dir": "C:/SimLoom/jobs",
    "host": "127.0.0.1",
    "port": 8099,
    "token": "replace-with-your-token",
    "timeout_seconds": 1800,
    "commands": {
      "catia.create_or_update_model": [
        "C:/SimLoom/python/python.exe", "{script}", "{request_file}"
      ]
    },
    "com": {}
  }
}
```

这是**项目自备 Python runner** 的示例，需把 Python 路径改为工作站实际路径，并实现
`catia_task.py` 来调用已安装的 CATIA。这里没有假定某个软件版本的批处理参数。
runner 读取第一个参数指向的 JSON 文件，按 `parameters`、`inputs`、`outputs` 操作软件。
它必须等待软件任务完成、确认原生导出结束后再退出。应只操作本次工作目录里的副本。
自定义厂商入口也可以作为 argv 列表配置，但必须包含 `{script}`。

启动 worker：

```sh
engineering-agent --config worker.json bridge --bridge-config worker.json
```

在同一机器直接执行时，省略 `bridge_url`，在动作配置内增加
`"command": ["实际 Python 路径", "{script}", "{request_file}"]`。

其他动作键是 `ansys.mesh_and_solve`、`adams.build_and_run_multibody`、
`vi-grade.run_vehicle_dynamics`。控制端 `actions` 中仅写点号后的动作名。
`executable` 单独出现不再允许启动：必须指定脚本和 argv，以免仅打开软件就返回成功。
启动命令只能来自配置；旧的字符串命令不再支持。

自动生成的 CATScript、Mechanical Python、Adams command、VI-grade scenario 仍是模板。
特别是 Mechanical 脚本不应假定能直接当作 Workbench journal 运行。模板不会自动成为
动作绑定，也不表示对应软件功能已经实现。须按实际产品、版本与团队模型模板编写 runner。

## 请求与结果

请求含 `schema_version`、`request_id`、`operation=run`、`tool`、`action`、`script`、
`parameters`、`inputs`、`outputs` 和 `files`。每个文件条目有 `path`、`bytes`、
`sha256`、`content_base64`。`inputs` 可通过动作配置补入模板或辅助文件。
计划中的输出优先于动作默认输出；输出不得覆盖输入脚本或模板。

worker 在 `<root>/jobs/<request_id>/` 中运行，工作目录即该目录。
`_simloom_request.json` 提供任务参数与相对输入/输出路径；`_logs/` 保存 stdout/stderr。
允许的命令占位符是 `{script}`、`{request_file}`、`{project_dir}`、`{tool}`、`{action}`。
客户端只在请求 ID、版本、动作、退出码、完整输出清单及文件哈希全部匹配时接受完成。
错误返回 `status=failed` 和 `error.code/message`，不会用空响应推断成功。
HTTP 的健康检查和执行均使用 `X-Bridge-Token`。远程部署应使用受控网络或 TLS 代理；
示例绑定 loopback，没有替用户开放外部网络。

CLI 的 plan/run/validate/enqueue 支持 `--parameters` JSON；MCP 与 Web 规划/任务入口支持 `parameters` 对象，队列保存该参数快照。

VI-grade 场景等工程参数必须显式给出。规划入口的 `parameters` 会传递到软件步骤，
例如 `parameters={"scenario": "项目已定义的工况"}`；系统不替工程师猜测载荷或工况。

## 连接状态的含义

| 状态 | 已有证据 | 尚不能证明 |
| --- | --- | --- |
| configured | 保存了配置 | 地址可访问、软件已安装 |
| reachable | 认证后的 worker 健康请求成功 | 软件可启动、许可证有效 |
| executable_found / execution_ready | 脚本绑定存在、命令可解析到程序；实际脚本在运行准备时检查 | 厂商任务可执行、模板正确、工程结果合格 |
| real_machine_verified | 本版固定为 false，待引入实机验收记录 | 不能用路径、COM dispatch 或 fixture 测试代填 |

只要一个软件动作具备启动条件，就可以尝试该软件；不要求先配置全部四款。
macOS/Linux 可以控制远程 Windows worker。pywin32 只用于 COM，不作为其他启动方式的门槛。

## 当前边界与下一步

- 文件传输目前是小任务协议：最多 128 个文件、解码后合计 32 MiB，请求体上限 48 MiB。
  大型 CAD/网格/结果需补分块传输或受管文件存储，目前会明确拒绝超限文件。
- 作业目录隔离与项目事务不是操作系统沙箱。配置的 runner 是可信代码；如果它修改软件
  当前会话、外部数据库或工作目录外的文件，项目 rollback 无法撤销那些软件内部副作用。
- 远程 worker 已有持久任务状态、独立监督进程、日志分页、取消、按软件排队与结果重连。
  直接本机命令模式仍同步运行，不使用这一套远程任务持久化与软件占用管理。
- 超时或取消会停止直接启动的 runner；其子进程或已启动的软件会话可能仍在运行。
  因此失败后的软件占用保留，需实地核实并显式解除；不会自动重新执行不确定的求解。
- 含远程步骤的多步骤计划已有持久工程链与显式恢复入口；按原计划继续剩余步骤，详见 `WORKFLOW_RECOVERY.md`。
  本机步骤执行状态不明时停止恢复，不自动重跑。
- 文件存在和哈希匹配只是完整性检查。内置 runner 已加入读回和单位证据；完整物理有效性与四软件实机仍待验收。
- 保留 worker 作业与失败日志；自动清理策略尚未实现。
- 旧 bridge 协议没有文件/版本契约，需同步升级控制端和 worker；旧请求明确报版本错误。

## 软件动作与独立证据校验（v2）

`examples/vendor_runners/` 的四个入口使用安装在工作站上的同版本 SimLoom 包。
控制端只传工程参数。启动软件的命令与 CATIA ProgID 来自动作配置的 `vendor_options`；
Adams/VI-grade 请求里的旧 `vendor_command` 会明确报错，需迁移到配置。

| 入口 | 具体行为 | 尚未证明 |
| --- | --- | --- |
| `catia_parameter_export.py` | 连接已运行的 CATIA；只对本轮 CATPart 副本修改明确命名的数字参数；保存、关闭、重新打开；比较请求值和两次读回值；用重新打开的文档导出 STEP | 实机版本兼容、几何尺寸与全部拓扑正确 |
| `ansys_mechanical_solve.py` | 启动独立 PyMechanical App；打开模型副本、按唯一分析名选择；清理旧结果、创建指定结果、同步求解、检查 `Done` 和 `IsSolved`；读取 `Maximum.Value/Unit`；保存原生模型和 JSON | 所有物理类型、网格质量、边界条件正确及设计合格 |
| `adams_command_runner.py` | 运行配置好的命令；传入 command file；重读 JSON/CSV 指标并按单位评价 | Adams 专有结果格式解析与求解收敛；它是导出接口，需团队已有命令/导出脚本 |
| `vi_grade_scenario_runner.py` | 运行配置好的场景命令；重读 JSON/CSV 指标并按单位评价 | 特定 VI-grade 产品/版本的 CLI 与收敛；仍需团队已有场景/导出脚本 |

CATIA 使用 exact native `Parameter.Name`，重复名、只读和公式驱动参数会被拒绝；
不允许通过 `allow_relation` 绕过。长度如 `25mm`，角度如 `3deg`，无量纲数字如 `2.5 1`。
保存后先 `Close` 再 `Open`，避免只读到同一个内存对象。默认绝对误差为请求单位下 `1e-6`，
可显式设置 `tolerance`。不退出工程师的 CATIA 应用，只关闭 runner 打开的副本文档。
当前实现不支持装配体与本地化逗号小数等所有参数表现形式，无法识别时失败。

Ansys 当前只提取请求列出的总变形和等效应力最大值。模型的几何、材料、载荷、边界条件及分析
已由工程师配置；runner 不猜物理条件。`SolveRequired`、部分结果、待后处理等状态不能当完成。
结果求值、保存或退出失败会报错。`.mechdb`/`.mechdat` 依赖的伴随文件须全部加入 `inputs` 并保持
相对路径；外部绝对链接尚不自动重写，原生输出的完整重开能力也待实机验收。

### 如何验证

控制端在提交前生成 `simloom-ai/vendor-evidence/v2` 契约，保存请求参数、输入 SHA-256 和输出清单；
同一快照同时进入请求摘要和本地重连回执。worker 执行后再次读取输入哈希，验证输入没有改变。
控制端收到文件后独立重复以下检查，不把报告中的 `passed` 当成依据：

- 请求 ID、软件、动作、原始输入哈希和产物哈希与本次请求一致。
- CATIA 参数在更新后、重新开盘后都与请求值匹配；单位可兼容换算；STEP 有有效的文件头尾。
  这项文件检查不等于 B-Rep 几何验收。
- Mechanical 状态与结果已求值标记满足要求，JSON 中每个请求指标都有有限数值和兼容单位。
- Adams/VI-grade 原始 JSON/CSV 重新解析；CSV 必须明确列名、单位与 `min/max/abs_max/last`
  聚合方式，禁止忽略空值或 NaN 后取前一个有效样本。
- 根据提交时保存的阈值重新计算工程验收结果；单位未知、维度冲突、布尔数值、缺指标均失败。

成功证据标为 `verification=artifact_and_vendor_evidence`。**执行成功与设计合格分开**：
数值有效但超过工程阈值时仍保留原始输出和历史，`engineering_acceptance=failed`，供工程师复查设计。
无验收规则时为 `not_requested`。证据错误会阻止发布。断线后的 collect 使用原回执，不能用更换配置
绕过单位、阈值或参数比较。

v1 自定义 `checks/passed` 报告继续兼容，但只标为 `artifact_and_report_schema`，表示格式与声明检查，
不再称为独立工程验证。未配置报告契约的自定义动作仍仅为 `artifact_integrity`。
runner 是受信任的观察来源，文件与数值检查不能证明安装许可证或报告真实性；`real_machine_verified`
仍是 false。不能把受控测试程序的输出作为软件实机通过证据。

### 配置与参数示例

参考 `configs/vendor-actions.example.json` 和 `examples/vendor_runners/parameters/*.json`。
将示例入口与输入工程文件放入项目对应路径，工作站安装同版本 SimLoom；CATIA 还需 pywin32，
Ansys 还需与本机 Mechanical 兼容的 `ansys-mechanical-core`。示例中的 Python 路径需改为实际路径。
CATIA ProgID 与 Adams/VI-grade 命令留空，必须按安装环境填写；没有杜撰厂商启动参数。

以下为 Adams 动作的配置片段：

```json
{
  "tools": {
    "adams": {
      "actions": {
        "build_and_run_multibody": {
          "script": "examples/vendor_runners/adams_command_runner.py",
          "command": ["C:/SimLoom/python/python.exe", "{script}", "{request_file}"],
          "validation": {"kind": "adams_export"},
          "vendor_options": {"command": [], "timeout_seconds": 1800}
        }
      }
    }
  }
}
```

`vendor_options.command` 填实际厂商入口的 argv；可用 `{command_file}`、`{scenario_file}`、
`{result_output}`、`{metrics_output}` 和 `{project_dir}` 占位符。不使用 shell 字符串。
配置在控制端和 worker 的摘要中留存；更换命令后不得复用原请求 ID。
远程模式在工具下增加 `bridge_url`，并在 worker 的 `commands` 中绑定同名动作与 Python 入口。

参数中的源文件会自动加入传输，输出清单可由这些入口的显式输出参数推导；如果另行提供 `outputs`，
它必须与参数一致。额外输入依赖仍使用 `inputs`。所有输入输出均为工作目录相对路径且不可互相覆盖。

JSON 指标格式为 `{"metrics":{"peak":12.5},"units":{"peak":"m/s^2"}}`。
使用 `required_metrics` 指定哪些值必须存在，用 `metric_units` 指定期望单位；规则必须提供独立 `unit`。
目前支持的单位由 `vendor_evidence.UNITS` 明确列举，包括长度、角度、压力、力、时间、速度、加速度和
角速度；未知单位会报错，不进行猜测或自动标签替换。

### API 依据和测试范围

实现核对了 PyCATIA 的 CAA 文档（Parameter、Document、Part），Ansys 官方 PyMechanical App 文档、
Mechanical `Solution`、`TotalDeformation`、`Quantity` 文档，以及 `ansys-mechanical-stubs==0.1.15`
包中 v242 的 `SolutionStatusType`（`Done` 为完成；还有待求解、部分结果、待后处理等状态）。
接口文档仅用于实现依据，不能替代软件版本上的运行验收。

- https://pycatia.readthedocs.io/en/latest/api/pycatia/knowledge_interfaces/parameter.html
- https://pycatia.readthedocs.io/en/latest/api/pycatia/in_interfaces/document.html
- https://mechanical.docs.pyansys.com/version/stable/api/ansys/mechanical/core/embedding/app/App.html
- https://scripting.mechanical.docs.pyansys.com/version/stable/api/ansys/mechanical/stubs/v252/Ansys/ACT/Automation/Mechanical/Solution.html
- https://scripting.mechanical.docs.pyansys.com/version/stable/api/ansys/mechanical/stubs/v252/Ansys/Core/Units/Quantity.html

回归入口：`python -m pytest tests/test_native_vendor_evidence.py tests/test_four_vendor_runners.py tests/test_vendor_validation.py tests/test_software_integration.py tests/test_worker_jobs.py -q`。
测试包含假 COM/Mechanical 对象、真实受控子进程、参数篡改、无效单位、CSV 损坏与重连回收。
它们不能作为四款商业软件操作成功的证据。


## 长任务、断线与软件占用

控制端现在使用 `job.submit → job.status → job.result`。请求先保存到工作站的 SQLite
任务库，独立监督进程负责执行；HTTP 服务或控制端退出不取消正在运行的任务。
重新提交相同 ID 和输入是幂等的；改了输入则返回 `JOB_ID_CONFLICT`。
`run` 保留同步兼容入口，但也通过持久任务后台执行。

同一个 worker 根目录中的同一种软件按提交顺序串行执行。不同软件可以并行。
这不是厂商全局会话锁：不管理其他 worker 根目录、直接 COM 调用、独立本机命令，
或工程师从桌面启动的会话。团队应为同一工作站的相同软件配置统一 worker 根目录。

| 状态 | 含义 |
| --- | --- |
| accepted | 已持久接收，监督进程可能仍在启动 |
| queued | 监督进程已接管，等待软件占用释放 |
| running | 已取得软件占用，正在准备或执行脚本 |
| cancelling | 已请求停止直接启动的 runner |
| completed | runner 成功结束，声明的文件完整性检查通过 |
| cancelled / failed | 已取消或失败；`requires_review=true` 时仍保留软件占用 |
| interrupted | 检测到监督进程持有的系统锁已释放，但没有正常完成记录；不自动重启 |

是否仍被监督依据进程持有的系统锁检查，不根据心跳过期或记录里的 PID 推断。
如果监督进程异常退出，已启动的商业软件可能仍在运行；持久占用不会随系统锁一起释放。
尚未启动的 accepted 任务可以取消，或显式再次提交同一请求。没有按时间自动判死或自动换编号重跑。

`job.cancel` 取消排队任务时不会启动 runner。运行中的任务则请求监督进程停止直接子进程，
**不表示商业软件及其所有派生进程均已停止**。异常、取消和超时后的软件占用需要
`job.release`，并提供 `confirmed_stopped=true` 和核实记录 `reason`；活跃监督进程仍持锁时不能解除。
人应先在工作站核实原任务结束。解除会允许后续排队任务继续，这是明确的操作行为。

任务状态接口不回传完整文件内容。`job.logs` 使用字节 offset 分页，返回 UTF-8 预览和
无损的 `content_base64`；页面采用连续解码，跨页中文不会丢字节。`job.events` 使用递增游标，
`job.list` 使用游标分页。`job.result` 回传结果前重新核对完成时的哈希，拒绝被外部修改的文件。

## 控制端恢复与结果回收

提交前保存本地 receipt：`.engineering-agent/software-jobs/<request_id>.json`，包含请求摘要、
目标工作站和原输出文件的哈希，不保存访问令牌。HTTP 提交响应丢失时，控制端查询原编号；
只有工作站明确返回未找到时才用**相同编号**再次提交，不创建新编号。

控制端等待超过 `step_seconds` 会返回 `REMOTE_JOB_PENDING`，保留编号，当前计划不发布结果。
这仅表示停止等待，不表示远程停止。工作台的“环境检查 → 软件任务与连接恢复”可查询、
读日志、取消或回收已有结果。回收必须打开原项目并找到 receipt；目标连接和请求摘要必须匹配。
如果正式输出文件在提交后已被修改，回收返回冲突，不覆盖工程师的修改。
结果文件、历史副本和“已回收”记录在同一事务中保存；重复回收不再次执行软件。

CLI 示例：

```sh
engineering-agent --config controller.json software-job catia list
engineering-agent --config controller.json software-job catia status JOB_ID
engineering-agent --config controller.json software-job catia logs JOB_ID --offset 0
engineering-agent --config controller.json software-job catia cancel JOB_ID
engineering-agent --config controller.json --project-dir ./project software-job catia collect JOB_ID
engineering-agent --config controller.json software-job catia release JOB_ID --confirmed-stopped --reason "已核实该软件任务结束"
```

`JOB_ID` 必须是实际返回的 32 位任务编号。`software-job ... receipts` 查看本地项目记录。
Web 入口是 `POST /api/software-jobs`，MCP 工具为 `engineering_software_job`。
MCP safe profile 只允许查询；取消、解除占用、回收需要 full profile。
这些入口只管理已有任务，不提供绕过动作配置的新任务提交方式。

验证分层：`test_worker_jobs.py` 用真实子进程检查幂等、顺序、取消、监督进程被终止、
HTTP 服务进程被终止后重启、提交响应丢失、回收冲突与事务恢复；浏览器用受控 runner
实际点击查询、日志、结果回收和取消。它们不代替 Windows 实机和商业软件验收。
