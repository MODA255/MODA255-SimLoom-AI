# MatrixGen 九链路生产级每日真实回归测试体系设计

## 1. 目标与范围

### 1.1 目标

在现有 `daily-test-orchestrator` 基础上进行增量开发，形成改造后可立即投入生产环境每日运行的真实回归测试体系。每天执行多而全的真实测试用例，而不是每条业务链路只发送一个请求。

### 1.2 固定业务范围

一级业务链路固定为九条：

- `I-LINK`：链接入库
- `I-LIVE`：直播入库
- `I-UPLOAD`：本地上传入库
- `M-ECOM`：后台手动电商混剪
- `M-IP`：后台手动 IP 宣发
- `M-THEME`：后台手动自动主题发现
- `A-ECOM`：后台自动化电商混剪
- `A-IP`：后台自动化 IP 宣发
- `A-THEME`：后台自动化自动主题发现

平台来源（抖音、微博等）是用例维度，不是一级链路维度。

### 1.3 非目标

- 不修改 MatrixGen 后端业务代码。
- 不重写已有单链路 runner、远程 adapter、素材入库流程。
- 不将飞书通知作为测试执行成功的前置条件；飞书是报告出口。
- 不执行投放、发布或外部平台发布动作。

## 2. 设计约束

1. **增量开发**：复用 `material_ingest.py`、`live_ingest.py`、`file_ingest.py`、`chain_runners.py`、`automation_lifecycle.py`、`execution_ledger.py`、`cleanup.py`、`daily_report.py`。
2. **每日真实运行**：每天新建测试批次和真实远程任务，不用历史 task 作为当天成功证据。
3. **高覆盖量**：阶段一每日计划 15-25 个 `case_run`，至少 10 个 P0 用例覆盖九条一级链路；阶段二每日计划 30-50 个 `case_run`，再扩展 P1/P2 轮换。
4. **七天保留**：资源和证据保留七天，之后批量清理；运行中或下载中的资源禁止清理。
5. **可恢复、可审计**：每次提交、状态迁移、远程资源和清理动作必须可追踪。
6. **投放关闭**：所有测试固定 `delivery_enabled=false`，创建前后均做安全校验。

## 3. 总体架构

新增一层九链路编排，不替换既有单链路执行器：

```text
NineChainSuite
  ├── Preflight
  ├── CaseScheduler
  ├── Existing Runner Adapters
  │   ├── ContentIngestRunner       (I-LINK)
  │   ├── LiveIngestRunner          (I-LIVE)
  │   ├── FileIngestRunner          (I-UPLOAD)
  │   ├── ManualVfcRunner           (M-*)
  │   └── AutomationRunner          (A-*)
  ├── UnifiedLifecycle
  ├── ResourceRegistry
  ├── Acceptance / DeliverySafety
  ├── SevenDayCleanup
  └── Report + Feishu Notification Adapter
```

建议新增模块：

- `configs/daily-nine-chain-real.json`
- `src/orchestrator/nine_chain_suite.py`
- `src/orchestrator/case_scheduler.py`
- `src/orchestrator/unified_lifecycle.py`
- `src/orchestrator/resource_registry.py`
- `src/orchestrator/production_readiness.py`

仅做小幅适配的既有模块：

- `suite_executor.py`
- `chain_report.py`
- `cleanup_plan.py`
- `daily_report.py`

统一命令入口：

```bash
orchestrator daily-run \
  --config configs/daily-nine-chain-real.json \
  --execute
```

## 4. 用例模型与每日规模

### 4.1 标识

- `run_id`：一次九链路每日运行。
- `batch_id`：本次运行的测试素材批次。
- `chain_id`：九条一级链路之一。
- `case_id`：稳定的用例定义 ID。
- `case_run_id`：某用例在某次 run 中的运行实例。
- `resource_id`：远程素材、任务、子任务、会话、分段或成片 ID。

### 4.2 用例配置结构

```json
{
  "suite_id": "daily-nine-chain-real-v1",
  "retention_days": 7,
  "daily_full_run": true,
  "chains": [
    {
      "chain_id": "I-LINK",
      "runner": "material_ingest",
      "cases": [
        {
          "case_id": "I-LINK-DOUYIN-001",
          "source_type": "douyin_video",
          "url_ref": "...",
          "required_status": "active",
          "priority": "P0"
        }
      ]
    }
  ]
}
```

敏感 token、App Secret、用户登录态和本地真实路径不写入仓库配置，使用环境变量或本地受保护配置引用。

### 4.3 每日用例层级

阶段一必须调度九条一级链路，不允许因直播链路暂时不可用而只运行部分链路。九条链路的运行策略分为“强制真实执行”和“直播可观测执行”：

#### 阶段一强制真实执行：六条非直播链路

以下六条必须真实提交并完成硬验收，不满足时阶段一不得标记为生产通过：

- `I-LINK`：链接入库，至少覆盖抖音链接和微博链接。
- `I-UPLOAD`：本地上传入库，至少覆盖单文件上传；必须等待素材 ready/active。
- `M-ECOM`：后台手动电商混剪，使用本次 batch 的链接入库或本地上传素材。
- `M-IP`：后台手动 IP 宣发，使用满足 IP 素材门槛的本次 batch 多素材池。
- `M-THEME`：后台手动自动主题发现，使用本次 batch 的非直播素材。
- `A-IP`：后台自动化 IP 宣发，使用满足门槛的本次 batch 多素材池。

#### 阶段一必须调度但按条件执行：三条直播链路

以下三条仍必须出现在当日计划、preflight 和报告中，不得静默跳过：

- `I-LIVE`：直播入库。若直播源和录制服务可用，真实提交并等待 ready/active；否则完成直播源探针并标记 `blocked`/`inconclusive`，不得伪造通过。
- `A-ECOM`：后台自动化电商混剪。若直播入库达到依赖条件则真实执行；否则记录依赖阻断和直播探针证据。
- `A-THEME`：后台自动化自动主题发现。若直播入库达到依赖条件则真实执行；否则记录依赖阻断和直播探针证据。

阶段一不是“只跑能跑的链路”：六条非直播链路是硬门槛，三条直播链路是必调度、可条件执行的可观测对象。直播链路阻断不得被计为 passed，也不得阻断六条非直播链路。

阶段一 P0 最低用例为 10 个：`I-LINK` 至少 2 个、其余八条至少各 1 个；六条非直播链路必须有真实终态证据。P1 用例按轮换表追加，阶段一每日建议 15～25 个真实 `case_run`；直播链路 blocked/inconclusive 时不计入已执行数量，但必须保留在计划和报告中。阶段二再扩展到 30～50 个。P2 异常、恢复、取消、重试和清理故障用例按周或发版前轮换。

## 5. 调度与依赖

### 5.1 批次

第一批并行执行三类入库：

```text
I-LINK / I-LIVE / I-UPLOAD
```

其中 `I-LINK` 和 `I-UPLOAD` 是阶段一非直播强制执行链路；`I-LIVE` 即使无法完成，也必须完成探针、提交尝试或阻断证据。

第二批执行手动剪辑：

```text
M-ECOM ← I-LINK 或 I-UPLOAD 的 ready 素材
M-IP   ← I-LINK 或 I-UPLOAD 的合格多素材池
M-THEME← I-LINK / I-UPLOAD 的 ready 素材
```

第三批执行自动化剪辑：

```text
A-IP    ← I-LINK 或 I-UPLOAD 的合格 IP 素材池
A-ECOM  ← I-LIVE（直播依赖满足时）
A-THEME ← I-LIVE（直播依赖满足时）
```

六条非直播链路的依赖失败必须使阶段一失败或阻断；直播依赖失败只使对应直播链路为 blocked/inconclusive，不得使非直播链路跳过或回退历史资源。

### 5.2 依赖规则

- 只允许消费本次 `batch_id` 产生且通过 ready/active 验收的素材。
- 依赖素材未就绪时，用例为 `blocked`，记录依赖原因。
- 依赖失败不得自动回退到历史素材。
- 独立用例失败不阻塞其他独立用例。
- 入库提交成功但尚未 ready 时，依赖用例进入等待队列而不是误判失败。

## 6. 统一生命周期

所有用例统一使用：

```text
planned
preflight
submitted
running
poll_timeout
completed
failed
blocked
cancelled
cleanup_pending
cleaned
cleanup_failed
```

`poll_timeout` 不是 `failed`，也不是 `passed`。超时后：

1. 保存快照和最后一次远端响应。
2. 进入观察队列。
3. 继续查询到远端终态。
4. 最终转换为 `completed` 或 `failed`。
5. 终态前禁止清理。

## 7. 统一验收契约

每个用例必须通过五层验收：

1. 提交验收：请求被远端接受并有资源引用。
2. 远端状态验收：任务或素材达到预期终态。
3. 阶段完整性验收：阶段无失败、关键阶段证据齐全。
4. 业务产物验收：素材 active/ready、视频可下载/可播放、数量符合预期。
5. 投放安全验收：投放关闭字段满足要求。

统一结果：

```json
{
  "submission": "passed",
  "remote_lifecycle": "completed",
  "phase_integrity": "passed",
  "business_output": "passed",
  "delivery_safety": "passed",
  "overall": "passed"
}
```

接口未返回投放字段时标记 `inconclusive`，不得默认通过。创建前后检查：

```text
delivery_enabled == false
advertiser_id 为空
awemes 为空
products 为空
promotion_goal 为空
adlab_scene 为空
```

## 8. 资源登记与七天清理

每次远程写操作前登记资源，至少记录：

```json
{
  "run_id": "...",
  "case_run_id": "...",
  "resource_type": "asset|task|parent_task|child_task|session|segment|video",
  "resource_id": "...",
  "created_at": "...",
  "terminal_status": "...",
  "cleanup_after": "...",
  "cleanup_status": "pending"
}
```

清理规则：

- 成功、失败、取消资源均保留七天后清理。
- 未创建资源不进入清理队列。
- 运行中任务、下载中素材不得清理。
- 清理按已登记的 resource ID 执行，不按 URL、标题模糊删除。
- 清理失败进入重试队列并在日报告警。
- 资源状态和清理结果必须可重放、可审计。

## 9. 生产前置检查

每日真实运行前必须检查：

- MatrixGen access token 可用，必要时可刷新。
- 目标 API 路由可用。
- 测试配置完整，资源引用可解析。
- I-LINK/I-LIVE/I-UPLOAD 输入存在且符合来源策略。
- I-UPLOAD 本地文件存在、可读、在允许目录内。
- 测试目录和状态库可写。
- 所有投放配置关闭。
- 运行队列没有冲突的同批次锁。
- 报告和证据目录可写。

关键 preflight 失败时不执行真实写操作，单独报告 `AUTH_EXPIRED`、`AUTH_PERMISSION_DENIED`、`REMOTE_UNAVAILABLE`、`CONFIG_INVALID` 等环境阻断原因。

## 10. 报告与飞书出口

每次运行生成：

```text
runs/{run_id}/
├── run.json
├── preflight.json
├── cases/{case_run_id}/
├── resource-registry.json
├── cleanup-plan.json
├── daily-report.json
└── daily-report.md
```

报告必须区分：

- 通过、失败、阻塞、仍运行、观察超时。
- 测试失败、环境阻断、业务失败、素材不满足、证据不足。
- 本次新增失败与历史重复失败。
- 资源清理待处理和清理失败。

飞书通知作为可选出口：

```json
{
  "feishu": {
    "enabled": false,
    "chat_id_ref": "FEISHU_TEST_CHAT_ID",
    "report_doc_ref": "FEISHU_REPORT_DOC_TOKEN"
  }
}
```

通知失败不改变测试结果，但必须记为 `notification_failed` 并告警。

## 11. 与现有实现的兼容边界

本方案只增加测试编排器能力，不要求 MatrixGen 后端新增接口或字段。统一模型中的字段必须通过本地适配器从现有响应映射得到；无法确认的字段必须为 `unknown/inconclusive`，不能猜测为通过。

### 11.1 三类入库适配边界

- `I-LINK` 复用 `material_ingest.ingest_and_wait_ready`，通过链接入库路由提交并轮询 asset ready/active。
- `I-LIVE` 复用 `live_ingest` 的直播源选择和现有入库路由；直播业务成功必须至少满足远端 asset 终态、有效时长和切片/处理阶段要求，具体阈值在用例配置中声明。
- `I-UPLOAD` 复用 `file_ingest` 的提交逻辑，但必须由新增的 `FileIngestRunner` 补齐上传后的 asset 查询、ready/active 轮询、失败/超时、资源登记和下游依赖输出；不得在 `created/pending` 状态直接驱动剪辑。

三类入库 runner 都必须实现统一接口：

```text
preflight(case) -> result
submit(case) -> submission_pending/submitted
poll(case_run_id) -> running/completed/failed/poll_timeout
accept(case_run_id) -> acceptance result
resources(case_run_id) -> resource refs
```

### 11.2 动态任务和幂等

每日真实运行禁止依赖配置中的历史 `task_id` 作为当天结果。配置只保存输入引用和用例参数；每次运行由 `run_id + case_id` 生成 `case_run_id` 和 operation key。

远程写操作前登记 `submission_pending` 记录（payload hash、operation key、预期资源类型）；远程响应成功后补齐 resource ID；请求超时或连接断开时进入 `submission_unknown` 对账流程，禁止盲目重复提交。

### 11.3 超时观察与重启恢复

`poll_timeout` 必须持久化到状态库的 observer 队列，包含下次观察时间、最大观察窗口、最后快照和资源引用。CLI 重启后先恢复 observer 队列，再执行新批次。只有远程终态为 completed/failed/cancelled，或人工确认终止，才允许进入清理队列。

### 11.4 七天清理实现边界

resource registry 使用两阶段记录：

1. 写操作前记录无 resource ID 的 planned/submission_pending；
2. 提交成功后补齐 resource ID、created_at、terminal_status 和 cleanup_after。

清理器只处理 `cleanup_after <= now` 且资源已处于终态的记录。运行中、下载中和 `submission_unknown` 资源必须保留并进入观察/对账队列。清理失败必须持久化 retry_count、last_error 和 next_retry_at。七天是本方案默认值，不能沿用现有清理器的 30 天默认值；实现时必须通过配置显式传入 `retention_days=7`，并增加回归测试。

## 12. 每日用例配额与轮换

每日用例目标按 `case_run` 统计，并区分四个口径：`planned`（已计划）、`submitted`（已提交）、`terminal`（已到远端终态）、`executed`（已提交且完成本地硬验收）。`blocked`、环境 preflight 阻断、`disabled` 和未提交用例不计入 `submitted/terminal/executed`，但必须计入报告；直播链路的 `blocked/inconclusive` 计入 `planned`，不计入 `executed`。每日最低配额表用于阶段二扩容目标，阶段一不要求达到表中 28 个最低执行数。

| 一级链路 | P0 最低数 | P1 轮换建议 | 每日最低执行数 |
|---|---:|---:|---:|
| I-LINK | 2 | 2-4 | 4 |
| I-LIVE | 1 | 2-3 | 3 |
| I-UPLOAD | 1 | 2-4 | 3 |
| M-ECOM | 1 | 2-3 | 3 |
| M-IP | 1 | 2-3 | 3 |
| M-THEME | 1 | 2-3 | 3 |
| A-ECOM | 1 | 2-3 | 3 |
| A-IP | 1 | 2-3 | 3 |
| A-THEME | 1 | 2-3 | 3 |
| **合计** | **10** |  | **28** |

阶段一目标：`planned` 为 15-25 个，P0 至少 10 个；其中六条非直播链路的强制用例必须达到真实 `terminal` 并通过硬验收，三条直播链路必须有计划和探针/提交/依赖结果。阶段一不要求达到表中 28 个每日最低执行数。

阶段二目标：`planned` 为 30-50 个，且九条链路均有计划用例；`submitted`、`terminal`、`executed` 按真实状态统计，不能把 blocked/inconclusive 算作执行成功。两阶段均不得为了补足数量伪造提交或通过。

## 13. 分阶段投产策略

本方案优先满足尽快投入生产的目标，不要求首日一次性实现全部九链路、30-50 个用例、自动清理和完整通知能力。生产发布分为两个阶段。

### 阶段一：有限范围生产运行

阶段一仍然**调度全部九条链路**，但不把直播依赖不可用伪装成通过。六条非直播链路必须真实上线并完成硬验收；三条直播链路必须执行探针、提交尝试或依赖检查并输出证据。默认参数：

```text
release_mode = limited_production
enabled_chains = all nine chain IDs
mandatory_real_chains = I-LINK,I-UPLOAD,M-ECOM,M-IP,M-THEME,A-IP
conditional_chains = I-LIVE,A-ECOM,A-THEME
min_p0_cases = 10
daily_case_target = 15-25
delivery_enabled = false
auto_delivery_enabled = false
cleanup_mode = register_and_protect
feishu_enabled = optional
```

阶段一必须具备：

- 唯一生产入口和只读 preflight/readiness gate；
- 九条链路全部进入计划、探针或真实执行，不得静默跳过；
- 六条非直播链路真实提交并完成硬验收；三条直播链路输出探针、提交或依赖证据；
- 每次运行生成新的 `run_id`、`batch_id`、`case_run_id`；
- 实际写操作都有稳定 resource ID，或明确进入 `submission_unknown`；
- 启用链路有提交、轮询、硬验收和本地证据；
- `poll_timeout` 不计为通过，且未终态资源受保护；
- 创建前后投放安全双重校验；字段缺失为 `inconclusive`；
- 资源登记、cleanup plan 和保护机制已生效；首日不执行未经验证的即时删除；
- 报告区分 planned/submitted/terminal/blocked/inconclusive/disabled；
- 直播链路阻断不得阻断六条非直播链路；
- 六条非直播链路任一未达到真实终态证据时，阶段一整体不得标记为生产通过。

阶段一不宣称九条链路全部成功，但必须宣称九条链路全部被调度并有结果；直播链路的 blocked/inconclusive 必须显式展示。
### 阶段二：全量生产运行

阶段二才启用九条一级链路、每日 30-50 个 `case_run`、P1/P2 轮换、完整 observer 恢复、七天自动清理、资源对账和飞书日报。阶段二必须满足下方全量门槛，并完成至少两轮真实运行。

## 14. 首日硬验收标准

### 配置与入口

- 生产使用唯一明确入口，且 readiness 失败时不会执行远端写操作。
- 启用链路使用显式 allowlist；敏感信息使用环境变量或受保护引用。
- 运行目录、状态库和证据目录可写；同批次锁冲突会阻止执行。

### 输入与依赖

- 每个启用 case 有稳定 `case_id`，本次运行生成新的 `run_id/case_run_id`。
- 不使用历史 task ID 作为当日成功证据。
- 依赖只消费本批次产生并通过 ready/active 验收的资源。
- I-UPLOAD 没有 ready/active 查询时必须 disabled/blocked，不得驱动下游。

### 远端执行与结果

- 每个实际写操作记录稳定 resource ID；连接异常进入 `submission_unknown`，禁止自动重提。
- 超时进入 `poll_timeout`，不算 passed，并保存最后远端快照。
- 终态必须区分 passed、failed、blocked、inconclusive。
- 每个启用链路至少有一个可人工核验的终态证据。

### 投放安全

- 创建前后均检查投放字段；检测到启用或非空投放对象立即失败并停止。
- 关键投放字段缺失时为 `inconclusive`，不得 passed。
- 首日不得产生投放、发布或外部平台发布动作。

### 资源与报告

- 所有已创建资源进入登记，至少含 `run_id`、`case_run_id`、`resource_type`、`resource_id`、`created_at`。
- 运行中、下载中、`poll_timeout`、`submission_unknown` 资源受保护。
- 首日不执行未经验证的即时远端删除，只生成 cleanup plan。
- 报告列出 planned/submitted/terminal/blocked/inconclusive/disabled，且能回答实际提交、终态、环境阻断、远端残留、保护资源和投放风险。

## 15. 全量生产验收门槛

阶段二投入每日生产运行前必须全部满足，并由 `production-readiness` 自动检查：

1. 九条一级链路均有配置、runner 和至少一个 P0 用例，且全量每日配置展开后的 P0 总数不少于 10（I-LINK 至少 2 个，其余链路至少各 1 个）。
2. 配置展开后每日计划 `case_run` 数为 30-50，且九条链路均有计划用例。
3. `daily-run --execute` 是唯一生产入口；不再要求历史 task ID。
4. I-LINK/I-LIVE/I-UPLOAD 均能提交、轮询、验收并登记资源；I-UPLOAD 不得停留在 created/pending。
5. 所有既有 runner 适配测试通过，且统一 runner contract 测试覆盖九条链路。
6. 生命周期迁移、submission_unknown 对账、poll_timeout observer、CLI 重启恢复和幂等测试通过。
7. `cleanup_after` 默认按 7 天计算；运行中、下载中和未知提交资源保护测试通过。
8. 投放关闭创建前/后断言通过；接口缺字段时结果为 `inconclusive`，不得判定 passed。
9. 真实环境完整跑通一轮九链路批次，至少每条链路一个 P0 终态证据。
10. 连续两轮运行均生成 `run.json`、`preflight.json`、九链路 case 证据、resource registry、cleanup plan 和日报。
11. 资源对账显示无未登记的本批次资源；无法对账时标记阻断，不得默认为通过。
12. 全量本地测试通过，且生产 readiness 命令退出码为 0。
13. 飞书通知失败不改变测试结果，但必须出现在日报的 notification 状态中。

## 16. 现有实现差距与增量落地边界

本设计是实现前的生产目标与验收合同；当前仓库尚未满足第 13 节的生产门槛，不能在实现完成前宣称可投入生产。实现必须先关闭以下差距：

| 差距 | 增量落地方式 | 完成证据 |
|---|---|---|
| 当前入口只有旧六链路 | 在现有 CLI 的 `daily-run` 分支增加九链路配置分派；保留旧入口兼容，不要求改 MatrixGen 后端 | 九链路配置解析测试、CLI 冒烟测试 |
| 入库 runner 未统一 | 新增适配器对象，内部调用现有 `material_ingest`、`live_ingest`、`file_ingest`；不改既有函数签名 | 三类 runner contract 测试 |
| 本地上传只有提交状态 | 在编排器侧增加上传结果查询/轮询适配；若远端没有可查询 ID，则该用例必须标记 `inconclusive/blocked`，不得驱动下游 | 上传 ready/failed/timeout 测试 |
| 长任务没有 observer | 新增 observer 状态表/JSONL 持久化和 `resume-observers` 子命令；旧 `resume` 保持兼容 | 重启恢复测试、终态收敛测试 |
| 现有清理默认 30 天且即时执行 | 新增资源级 registry 和七天 cleanup worker；旧 cleanup API 作为底层删除适配器，不直接决定清理时机 | 7 天计算、运行中保护、失败重试测试 |
| 现有报告是六链路 | 新增九链路聚合报告目录，不删除旧报告生成器 | 目录契约测试、字段完整性测试 |
| 没有生产 readiness 命令 | 新增只读 `production-readiness` 命令，生产 `daily-run` 只有 readiness 通过后才允许写操作 | 失败退出码和通过退出码测试 |

### 14.1 关键降级原则

- 后端没有稳定资源 ID、状态查询路由或投放字段时，测试结果为 `inconclusive` 或 `blocked`，不能用标题/URL 猜测，也不能声称通过。
- 远端写请求出现连接异常时，必须先对账；无法对账则保持 `submission_unknown`，禁止自动重提和自动清理。
- 任何目标链路尚未具备生产 runner 或 contract 测试时，`production-readiness` 返回非零并禁止 `daily-run --execute`。
- 在所有九条链路达到生产门槛前，旧六链路入口可以继续运行，但不得将旧六链路报告标记为九链路生产通过。

## 17. 增量实施顺序

1. 新增九链路配置模型和 P0/P1 用例矩阵，明确每日展开数量为 30-50 个 `case_run`。
2. 新增三类入库 runner adapter，先补 I-LINK/I-LIVE，再补 I-UPLOAD 的 ready 生命周期；每一步先写 contract 测试。
3. 新增 case scheduler，复用现有手动和自动化 runner，并持久化依赖关系。
4. 新增 resource registry、submission_pending/submission_unknown 对账和统一 execution ledger 映射。
5. 新增 observer 队列、`poll_timeout` 状态和重启恢复命令。
6. 新增七天 cleanup worker，接入现有 cleanup API，并实现终态保护和失败重试。
7. 新增九链路报告聚合、资源对账和飞书通知适配器。
8. 新增只读 `production-readiness` 检查和九链路 `daily-run --execute` 分派。
9. 先跑本地单元/集成测试，再跑真实九链路验收；连续两轮通过后才启用生产每日调度。

## 18. 成功标准

最终成功不是“命令执行完”，而是：

```text
每天无需人工补参数即可启动；
每天执行 30～50 个真实用例；
九条业务链路全部有结果；
长任务有最终状态；
每个远程资源可追踪、可清理；
失败可定位到环境、输入、阶段或业务验收；
不产生投放副作用；
报告和飞书通知稳定输出；
连续运行可恢复、可审计。
```
