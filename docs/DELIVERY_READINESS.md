# 2026-11-20 交付闭环清单

这份清单把 SimLoom-AI 在 2026 年 11 月 20 日交付节点前可以由控制端独立证明的内容，与必须在 Windows 工作站完成的实机验收分开记录。它是发布前的证据索引，不把 fixture、COM 探针或 worker 在线状态写成商业软件通过。

## 控制端闭环

以下链路已经在本地回归和公共 CI 中覆盖：

```text
发现 operation
→ preflight 和 operation contract
→ 事务化提交 / Windows worker 请求
→ 持久工作流与 request ID
→ 远程任务观察、日志和断线恢复
→ EngineeringResult / failure / failure_evidence
→ 独立结果验证
→ 有界 repair / replan
→ 版本、diff、报告和审计
```

工作流摘要现在统一提供：

- `next_action`：Agent 的保守路由提示；
- `next_action_reason`：该提示对应的持久证据；
- `evidence_state`：`complete`、`partial`、`missing` 或 `requires_review`；
- `requires_human`：是否必须由工程师确认软件会话、许可证或未知状态。

这些字段可以从 CLI、Web 工作台和 MCP workflow resource 读取。它们不授权自动重提请求，原始 `lifecycle`、`operation_contract`、`failure` 和 `failure_evidence` 仍是审计依据。

## 可重复验证

在项目根目录执行：

```bash
./.venv/bin/python -m pytest -q
node --check src/engineering_agent/static/workspace.js
browser_tmp=$(mktemp -d)
npm install --prefix "$browser_tmp" playwright@1.65.0
"$browser_tmp/node_modules/.bin/playwright" install chromium
NODE_PATH="$browser_tmp/node_modules" \
  scripts/ci/run_browser_test.sh tests/browser/software_jobs.cjs
```

交付节点的本地基线是全量 Python 测试通过、工作台 JavaScript 语法检查通过、浏览器工程链恢复和移动视口无横向溢出。公共 CI 还会在 Python 3.10/3.11/3.12、Ubuntu、macOS 和 Windows 上运行 portability 与浏览器任务。

## Windows 实机闸门

以下内容只能由装有对应软件和许可证的 Windows 工作站补齐：

1. CATIA、Ansys、Adams、VI-grade 的版本、ProgID、启动命令和许可证状态；
2. 每款软件至少一个真实建模/求解动作，以及原生结果回读；
3. 原生失败日志、GUI 会话占用、超时、取消、崩溃和恢复样本；
4. 独立工程指标验证与 `simloom-ai/machine-evidence/v1` 记录；
5. 干净 Windows 安装、worker 配置、升级和回滚验收。

运行真实 smoke test 前，必须把私有 worker 配置和参数放在 Windows 机器上，再手动触发仓库的 `Windows vendor smoke test` workflow。没有这些记录时，系统返回的 `real_machine_verified` 必须保持 `false`。

## 交付判定

控制端交付候选可在以下条件全部满足时发布：

- 本地全量回归通过；
- 最新 `engineering-agent` GitHub Actions run 的 Python、portability 和 browser jobs 全部成功；
- `main` 工作树 clean，提交已推送到 `origin/main`；
- 文档链接、MCP resource、CLI、Web 工作台和报告字段使用同一套调度命名；
- 任何尚未取得 Windows 实机证据的软件仍显示为“待实机验收”，不会被报告成已接入。

这一定义保证交付的是可安装、可观察、可恢复和可审计的 Agent-facing runtime，同时把四款商业软件的真实验收留在可追溯的 Windows 闸门内。
