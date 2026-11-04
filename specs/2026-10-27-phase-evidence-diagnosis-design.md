# MatrixGen 阶段证据、血缘与诊断体系设计

## 目标

建立一套可追溯的剪辑质量诊断链路：

```text
任务证据采集
→ 阶段证据标准化
→ 文本产物采集
→ 数据血缘构建
→ 阶段级确定性 Rubric
→ 证据完整度门禁
→ LLM Judge
→ 六链路结构化日报
```

目标不是只判断“任务是否完成”，而是回答：哪个阶段、哪类输入或哪次转换导致问题；如果证据不足，则明确输出“无法归因”，不生成猜测性的根因。

## 范围

第一阶段只消费 MatrixGen 当前已经暴露的接口和字段，不立即修改 MatrixGen 后端。接口没有返回的日志、脚本、预览或血缘字段必须标记为 `missing` / `unavailable`，禁止推测补齐。

纳入范围：

- 手动 VFC 任务；
- 自动化父任务、Session、Segment、剪辑子任务；
- 阶段状态、耗时、错误、metrics、output_data、output_files；
- 远程成片元数据；
- transcript、script、subtitle 等文本产物；
- `clip_ids`、`source_start`、`source_end`、`asset_clip_id`、`parent_clip_id` 等血缘字段；
- 阶段级确定性检查；
- 证据完整度门禁；
- LLM Judge 输入升级；
- 六链路日报诊断字段。

不纳入本阶段：

- 本地视频下载或缓存；
- 多模态视频抽帧；
- 在线流量抽样；
- 人工复核工作台；
- MinIO 归档；
- MatrixGen 后端新接口开发。

## 数据模型

### PhaseEvidence

```json
{
  "task_id": "...",
  "phase_id": "...",
  "phase_name": "select",
  "phase_index": 5,
  "status": "completed",
  "message": "select",
  "error_msg": null,
  "started_at": "...",
  "completed_at": "...",
  "duration_ms": 1000,
  "retry_count": 0,
  "metrics": {},
  "output_data": {},
  "output_files": [],
  "previews": [],
  "workflow_run_id": null,
  "trace_id": null,
  "evidence_status": "available"
}
```

### TextArtifact

```json
{
  "artifact_id": "...",
  "artifact_type": "transcript|script|subtitle|asr|ocr|vlm",
  "phase_name": "subtitle",
  "remote_url": "https://...",
  "text_sha256": "...",
  "text_length": 1234,
  "language": "zh-CN",
  "content": "...",
  "evidence_status": "available|missing|unavailable"
}
```

日报默认保存 URL、哈希、长度和状态；Judge 调用时读取文本内容。视频本体永不下载到本地。

### LineageRecord

```json
{
  "final_video_id": "...",
  "final_clip_id": "...",
  "source_clip_id": "...",
  "asset_id": "...",
  "source_start": 38.14,
  "source_end": 49.04,
  "final_start": 14.9,
  "final_end": 23.98,
  "transformations": [
    {"phase": "select", "workflow_run_id": "..."},
    {"phase": "arrange", "workflow_run_id": "..."},
    {"phase": "compose", "workflow_run_id": "..."}
  ]
}
```

## 证据采集

采集器按以下顺序工作：

1. 查询任务详情；
2. 查询阶段或自动化 stages；
3. 查询远程成片元数据；
4. 查询 transcript_json 或 transcript_oss_url；
5. 查询 task/script/user_script/IP scripts_text；
6. 从成片 `clip_ids` 构建初步血缘；
7. 收集 output_data、output_files、metrics 和 workflow_run_id；
8. 对每项证据标记 available、missing 或 unavailable；
9. 禁止调用视频下载接口。

文本来源优先级：

```text
video.transcript_json
→ video.transcript_oss_url
→ task.transcript_json
→ task.script
→ task.user_script
→ phase output_files
→ IP automation scripts_text
→ missing
```

## 阶段级 Rubric

先使用确定性规则，不交给 LLM：

- material_download：bytes_done、bytes_total、错误；
- segment：clip 数量、空切片、标签覆盖率；
- select：选中数量、shortfall、重复率；
- refine_slicer：输入输出数量、去重和失败数；
- arrange：时间轴空洞、重叠、顺序；
- subtitle：文本非空、时间范围覆盖；
- compose：done 数量、错误；
- upload：远程 URL、file_size、created 状态。

每个阶段输出：

```json
{
  "status": "passed|failed|warning|skipped|insufficient_evidence",
  "failure_modes": [],
  "evidence": [],
  "next_action": null
}
```

## 证据完整度门禁

定义四种状态：

- `complete`：满足该评测所需的全部证据；
- `partial`：能做部分确定性判断；
- `insufficient`：缺少关键证据，不能做内容或根因结论；
- `unavailable`：接口未配置或远程不可访问。

证据不足时：

```json
{
  "status": "insufficient_evidence",
  "overall_score": null,
  "confidence": 0,
  "cannot_conclude": true,
  "missing_evidence": ["subtitle_text", "select.output_data"]
}
```

## LLM Judge 边界

LLM Judge 只评估：

- 主题与 brief/query 一致性；
- 品牌、商品、IP 文本一致性；
- 脚本/字幕中的事实性和无证据承诺；
- 内容完整性和主题贯穿。

LLM Judge 不判断：

- 阶段是否完成；
- 文件是否存在；
- 时间轴是否有空洞；
- 投放是否开启；
- 画面清晰度、音频质量、字幕视觉效果。

输入必须包含：

```text
任务上下文
阶段证据摘要
文本产物
血缘摘要
远程成片元数据
确定性 Rubric 发现
证据完整度
```

关键证据缺失时不输出伪造分数。

## 日报结构

```json
{
  "execution": {},
  "phase_evidence": {},
  "text_evidence": {},
  "lineage": {},
  "deterministic_rubric": {},
  "llm_judge": {},
  "evidence_completeness": {},
  "final_diagnosis": {
    "status": "diagnosed|insufficient_evidence|passed",
    "failure_stage": null,
    "failure_mode": null,
    "root_cause": null,
    "evidence": [],
    "next_action": null
  },
  "local_media_files": []
}
```

最终状态由确定性硬失败优先决定；父任务 waiting_live、投放关闭导致推广阻断、证据不足只能形成 warning 或 insufficient，不得覆盖已完成的剪辑子任务。

## 实施顺序

1. 先写证据模型和缺失状态测试；
2. 实现阶段证据标准化采集；
3. 实现 TextArtifact 采集；
4. 实现 clip → asset → phase → video 初步血缘；
5. 实现阶段级 Rubric；
6. 实现证据完整度门禁；
7. 升级 LLM Judge 输入与缺证据行为；
8. 将所有内容接入六链路结构化日报；
9. 用真实历史任务回放验证，不下载视频。

## 验收标准

- 六条链路都有阶段证据摘要；
- 每个阶段都明确 available/missing/unavailable；
- transcript/script 来源和哈希可追踪；
- 成片 clip_ids 能构建可查询的初步血缘；
- 阶段级硬失败不依赖 LLM；
- 证据不足时 Judge 不输出虚假质量分；
- 日报包含诊断状态、证据和下一步动作；
- `runs/` 下不存在视频文件；
- 全量测试通过。
