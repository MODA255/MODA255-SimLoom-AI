# 阶段一有限范围生产运行实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有编排器上增量实现阶段一九链路调度：九条链路全部进入计划，六条非直播链路强制真实执行，三条直播链路可观测执行，并具备生产安全门禁与本地报告。

**Architecture:** 保留现有 material/live/file/manual/automation runner，新增轻量的阶段一 suite 适配层、统一 case 状态、资源登记和 readiness 检查。阶段一不重写后端、不启用未经验证的自动清理；所有远程写操作先过安全门禁并记录证据。

**Tech Stack:** Python 3.12, pytest, SQLite/JSON evidence, existing RemoteAdapter and orchestrator CLI.

---

### Task 1: 建立阶段一配置与统一 case 模型

**Files:**
- Create: `configs/daily-nine-chain-phase1.json`
- Create: `src/orchestrator/phase1_suite.py`
- Test: `tests/test_phase1_suite.py`

- [ ] **Step 1: 写失败测试**：验证九条 chain ID、六条 mandatory、三条 conditional、P0=10、阶段一 planned 范围和配置没有历史 task ID。
- [ ] **Step 2: 运行测试确认失败**：`pytest tests/test_phase1_suite.py -q`，预期因模块/配置不存在失败。
- [ ] **Step 3: 实现最小配置加载与 case 展开**：支持 `run_id/batch_id/case_run_id` 派生、planned/submitted/terminal/executed 统计和九链路完整性校验。
- [ ] **Step 4: 运行测试确认通过**：`pytest tests/test_phase1_suite.py -q`。
- [ ] **Step 5: 提交**：`git add ... && git commit -m "feat: add phase one nine-chain suite"`。

### Task 2: 实现资源登记与 submission_unknown 保护

**Files:**
- Create: `src/orchestrator/resource_registry.py`
- Modify: `src/orchestrator/repository.py`
- Test: `tests/test_resource_registry.py`

- [ ] **Step 1: 写失败测试**：覆盖写入前 pending、成功补 resource ID、未知提交保护、重复登记幂等和报告导出。
- [ ] **Step 2: 运行测试确认失败**：`pytest tests/test_resource_registry.py -q`。
- [ ] **Step 3: 实现 SQLite 资源登记**：记录 run/case/resource、状态、created_at、cleanup_after、保护原因；不执行删除。
- [ ] **Step 4: 运行测试确认通过**。
- [ ] **Step 5: 提交**：`git add ... && git commit -m "feat: add phase one resource registry"`。

### Task 3: 实现统一结果状态与投放安全门禁

**Files:**
- Create: `src/orchestrator/phase1_acceptance.py`
- Modify: `src/orchestrator/delivery_safety.py`
- Test: `tests/test_phase1_acceptance.py`

- [ ] **Step 1: 写失败测试**：验证 passed/failed/blocked/inconclusive/poll_timeout，创建前后 delivery 字段，字段缺失不得 passed。
- [ ] **Step 2: 运行测试确认失败**。
- [ ] **Step 3: 实现硬验收聚合和投放双重校验**。
- [ ] **Step 4: 运行测试确认通过**。
- [ ] **Step 5: 提交**：`git add ... && git commit -m "feat: add phase one acceptance gates"`。

### Task 4: 实现三类入库适配器

**Files:**
- Create: `src/orchestrator/phase1_ingest_runners.py`
- Modify: `src/orchestrator/file_ingest.py`
- Test: `tests/test_phase1_ingest_runners.py`

- [ ] **Step 1: 写失败测试**：I-LINK/I-LIVE/I-UPLOAD 统一 contract；链接和直播复用已有 ingest；本地上传必须 ready/active 才能完成；pending 不得驱动下游。
- [ ] **Step 2: 运行测试确认失败**。
- [ ] **Step 3: 实现适配器**：提供 preflight/submit/poll/accept/resources，兼容旧函数；缺少远端查询 ID 时标记 blocked/inconclusive。
- [ ] **Step 4: 运行测试确认通过**。
- [ ] **Step 5: 提交**：`git add ... && git commit -m "feat: add phase one ingest runners"`。

### Task 5: 实现阶段一调度器与九链路执行分派

**Files:**
- Create: `src/orchestrator/phase1_runner.py`
- Modify: `src/orchestrator/cli.py`
- Test: `tests/test_phase1_runner.py`, `tests/test_cli_phase1.py`

- [ ] **Step 1: 写失败测试**：九链路均进入计划；六条非直播真实调用；三条直播即使阻断也留下 probe/dependency evidence；独立链路不互相阻塞。
- [ ] **Step 2: 运行测试确认失败**。
- [ ] **Step 3: 实现串行安全调度**：先入库，再手动，再自动化；复用现有 adapter；不使用历史 task ID；保存每个 case evidence；阶段一只允许显式配置运行。
- [ ] **Step 4: 增加 CLI 命令**：`phase1-readiness` 只读检查；`daily-run --phase1 --execute` 进入阶段一 runner；readiness 失败禁止写操作。
- [ ] **Step 5: 运行测试确认通过**。
- [ ] **Step 6: 提交**：`git add ... && git commit -m "feat: add phase one production runner"`。

### Task 6: 实现阶段一报告与保护型清理计划

**Files:**
- Create: `src/orchestrator/phase1_report.py`
- Modify: `src/orchestrator/daily_report.py`
- Test: `tests/test_phase1_report.py`

- [ ] **Step 1: 写失败测试**：报告包含九链路、planned/submitted/terminal/executed/blocked/inconclusive/disabled；资源未终态时 cleanup plan 为 protected。
- [ ] **Step 2: 运行测试确认失败**。
- [ ] **Step 3: 实现 JSON/Markdown 报告和 cleanup plan，不执行远端删除。
- [ ] **Step 4: 运行测试确认通过**。
- [ ] **Step 5: 提交**：`git add ... && git commit -m "feat: add phase one reports and protected cleanup"`。

### Task 7: 全量验证与真实阶段一验收

**Files:**
- Modify: `docs/superpowers/specs/2026-10-29-nine-chain-production-design.md`（仅补充实际验收证据）
- Create: `runs/phase1-acceptance-<timestamp>/`

- [ ] **Step 1: 运行全量本地测试**：`pytest -q`，必须 0 failures。
- [ ] **Step 2: 运行 phase1 readiness**：配置、认证、路由、素材引用、投放关闭、目录和锁检查全部记录。
- [ ] **Step 3: 执行真实阶段一批次**：九条链路全部计划；六条非直播真实执行；直播链路探针/提交/依赖结果落盘；不执行自动删除。
- [ ] **Step 4: 检查资源登记与报告**：确认没有未登记的实际写资源，运行中/未知资源均 protected。
- [ ] **Step 5: 修复验证中发现的问题并重跑相关测试**。
- [ ] **Step 6: 完成阶段一验收记录和最终生产运行说明**。
