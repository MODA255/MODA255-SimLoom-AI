# 每日全链路巡检与详细报告实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MatrixGen 测试编排器升级为可每日运行、可接入飞书机器人、输出完整阶段证据和详细 HTML 报告的混合巡检系统。

**Architecture:** API 负责创建和轮询任务，复用前端已有的 task/phases/nodes/diagnostics/videos 接口；浏览器只读打开任务页面并截图。每个实例保存原始快照、轮询事件、阶段耗时、错误、截图和远程成片元数据，汇总层生成 JSON、Markdown 和 HTML 报告，后续再接飞书通知。

**Tech Stack:** Python 3.12、pytest、httpx、Playwright、现有 RemoteAdapter、静态 HTML/CSS/JavaScript。

---

### Task 1: 建立每日测试实例矩阵和运行目录

**Files:**
- Create: `configs/daily-test-matrix.json`
- Create: `src/orchestrator/run_layout.py`
- Test: `tests/test_run_layout.py`

- [ ] **Step 1: Write the failing test**

验证 6 条链路、每条 3 次实例、P0/P1 数据集、delivery=false 和不保存本地媒体。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_layout.py -q`
Expected: FAIL because matrix loader/layout functions do not exist.

- [ ] **Step 3: Implement minimal matrix loader and layout**

实现 `load_test_matrix()`、`build_run_dir()`、`build_case_dir()`，每个运行目录包含 `run.json`、`summary.json`、`chains/{case_id}/`。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_run_layout.py -q`
Expected: PASS。

---

### Task 2: 实现 API 轮询、状态事件和阶段耗时

**Files:**
- Create: `src/orchestrator/runner.py`
- Modify: `src/orchestrator/adapters.py`
- Modify: `src/orchestrator/polling.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write failing tests**

覆盖 task 创建、每轮 poll 快照、终态退出、阶段开始/结束耗时、错误、重试、超时和单链路失败不阻塞其他实例。

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_runner.py -q`
Expected: FAIL because runner APIs do not exist.

- [ ] **Step 3: Implement runner**

实现 `run_case()`、`poll_case()`、`compute_phase_timeline()`；所有响应原文摘要写入 case 目录；不调用视频下载接口。

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_runner.py tests/test_polling.py -q`
Expected: PASS。

---

### Task 3: 完整采集前端可见证据

**Files:**
- Modify: `src/orchestrator/adapters.py`
- Modify: `src/orchestrator/daily_pipeline.py`
- Modify: `src/orchestrator/evidence_adapter.py`
- Create: `src/orchestrator/diagnostic_collection.py`
- Test: `tests/test_frontend_evidence_collection.py`

- [ ] **Step 1: Write failing tests**

验证 `/tasks`、`/phases`、`/nodes`、`/nodes/{key}`、`/videos`、diagnostics 的统一采集；检查 transcript/script、clip lineage、接口 404/权限错误的具体记录。

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_frontend_evidence_collection.py -q`
Expected: FAIL because diagnostic collection and complete evidence aggregation are incomplete.

- [ ] **Step 3: Implement collection**

为每个 case 保存：`task.json`、`poll-events.json`、`phases.json`、`nodes.json`、`diagnostics/*.json`、`videos.json`、`text-artifacts.json`、`lineage.json`、`evidence-manifest.json`。

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_frontend_evidence_collection.py tests/test_evidence_adapter.py -q`
Expected: PASS。

---

### Task 4: 实现确定性问题分析和 flaky/性能波动统计

**Files:**
- Create: `src/orchestrator/analysis.py`
- Modify: `src/orchestrator/diagnosis.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write failing tests**

覆盖稳定通过、稳定失败、偶发失败、超时、阶段耗时 P50/P95、错误类型聚合、阶段间等待时间和代码版本字段。

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_analysis.py -q`
Expected: FAIL because analysis functions do not exist.

- [ ] **Step 3: Implement analysis**

输出 `stable_pass`、`stable_fail`、`flaky`、`timeout`、`performance_regression`，并将每个结论绑定原始 case 证据。

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_analysis.py -q`
Expected: PASS。

---

### Task 5: 实现浏览器只读截图采集

**Files:**
- Create: `src/orchestrator/browser_observer.py`
- Create: `tests/test_browser_observer.py`
- Modify: `configs/remote-test.local.json`

- [ ] **Step 1: Write failing tests**

使用 mock page 验证截图命名、异常时追加截图、浏览器失败不改变 API 任务结果。

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_browser_observer.py -q`
Expected: FAIL because observer does not exist.

- [ ] **Step 3: Implement observer**

使用已登录 storage state，只读打开任务详情、节点详情、成片详情；截图保存到 `chains/{case_id}/screenshots/`，不保存视频。

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_browser_observer.py -q`
Expected: PASS。

---

### Task 6: 生成完整单链路与六链路 HTML 报告

**Files:**
- Modify: `src/orchestrator/hybrid_report.py`
- Create: `src/orchestrator/report_assets.py`
- Test: `tests/test_detailed_report.py`

- [ ] **Step 1: Write failing tests**

验证报告包含总览、实例矩阵、阶段时间线、耗时、错误、重试、诊断文件、截图链接、远程成片链接、证据缺口、flaky 统计和下一步动作。

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_detailed_report.py -q`
Expected: FAIL because detailed sections do not exist.

- [ ] **Step 3: Implement HTML/JSON/Markdown report**

生成：

```text
run-summary.json
run-report.md
run-report.html
chains/{case_id}/chain-result.json
chains/{case_id}/chain-report.html
```

HTML 使用内联 CSS/JS，支持折叠原始证据、阶段时间线、错误列表、截图墙和远程成片链接。

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_detailed_report.py tests/test_hybrid_report.py -q`
Expected: PASS。

---

### Task 7: 接入每日巡检命令和飞书机器人输出适配

**Files:**
- Modify: `src/orchestrator/cli.py`
- Create: `src/orchestrator/feishu_payload.py`
- Create: `configs/feishu-report.json.example`
- Test: `tests/test_daily_inspection_cli.py tests/test_feishu_payload.py`

- [ ] **Step 1: Write failing tests**

验证 `daily-inspect` 能执行矩阵、生成报告、只输出摘要给飞书 payload，并保留本地/远程报告链接；不把 token、Cookie 或本地视频写入 payload。

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_daily_inspection_cli.py tests/test_feishu_payload.py -q`
Expected: FAIL because command and Feishu payload adapter do not exist.

- [ ] **Step 3: Implement CLI and payload**

新增：

```bash
orchestrator daily-inspect --config ... --matrix ... --output-dir ...
orchestrator report-feishu --report ...
```

飞书 payload 包含：总体状态、失败/警告数量、Top 错误、最慢阶段、报告入口和每条链路摘要。

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_daily_inspection_cli.py tests/test_feishu_payload.py -q`
Expected: PASS。

---

### Task 8: 历史回放、真实 dry-run 和全量验证

**Files:**
- Modify: `src/orchestrator/replay.py`
- Create: `runs/meeting-complete/`
- Test: existing full suite

- [ ] **Step 1: Run historical replay**

使用现有六条任务快照，生成完整报告，验证报告结构和证据缺口，不产生远程写操作。

- [ ] **Step 2: Run API-only daily inspection dry-run**

执行 6 条链路配置读取和接口采集，确认单条失败不会中断全局。

- [ ] **Step 3: Run full tests**

Run: `pytest -q`
Expected: 全部通过。

- [ ] **Step 4: Verify artifacts**

确认：

```text
HTML/JSON/Markdown 报告存在
每个 case 有阶段耗时和错误信息
截图目录可访问
没有本地视频文件
没有凭证进入报告
```
