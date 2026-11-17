# SimLoom AI 开发文档规范

SimLoom AI 的每个可审查代码部分都必须同时交付代码、文档、测试和版本记录。文档不是发布后的补充材料，而是操作契约的一部分：它让工程师知道系统会调用什么、何时会写入文件、如何判断结果、失败后怎样恢复，以及哪些结论仍需真实 CATIA、Ansys、Adams 或 VI-grade 验收。

## 一次独立交付必须包含什么

1. **代码**：实现一个可以独立说明、测试和回滚的能力。跨模块改动必须说明调用边界和数据流。
2. **使用文档**：在 `README.md` 或相应的 `docs/*.md` 中说明入口、配置、输入、输出、典型示例、限制和错误处理。面向工程师的说明使用实际操作步骤，不只列函数名。
3. **设计文档**：记录 schema、事务边界、文件契约、状态转换、证据来源和兼容策略。若能力涉及 Windows worker 或商业软件，必须写明 Mac/离线、排练、连接、执行和实机验收的边界。
4. **测试**：测试成功路径、参数错误、文件冲突、重复调用、失败恢复和结果验证。受控 runner、fixture 或 fake COM 只能证明 SimLoom 的协议和恢复逻辑，不能标记商业软件实机通过。
5. **提交与推送**：完成相关检查后创建一个主题明确的 Git commit，并推送到 `origin/main`。提交前检查 `git diff --check`、语法检查和相关测试；适合时运行全量测试。

## 文档最低内容

每个新增 operation、协议字段、worker 能力或用户入口的文档至少回答：

- 它解决哪个工程问题，属于 SimLoom 还是厂商软件的职责？
- 从哪里调用（Python、CLI、Web、MCP、Windows worker）？
- 输入和输出 schema 是什么，路径是否必须位于项目目录？
- 哪些写操作经过事务、锁、checkpoint、独立验证、diff 和 rollback？
- 失败会返回什么稳定错误码，能否重试、恢复或需要人工确认？
- 如何运行测试，测试覆盖了什么，哪些情况仍然没有验证？
- 当前证据是离线、排练、连接、执行还是 `real_machine_verified`？

## 推荐的文档结构

```text
# 能力名称

## 解决的问题
## 调用入口
## 输入与输出
## 生命周期与事务边界
## 失败与恢复
## 配置示例
## 测试与证据范围
## 已知限制与下一步
```

## 版本记录要求

完成后同步更新以下至少一处：

- `README.md`：用户能直接看到的能力或入口；
- 相关主题文档：详细 schema、配置和限制；
- `docs/AGENTCAD_PARITY.md`：与 AgentCAD 对齐、四软件实机状态或下一步差距；
- `docs/PRODUCT_ENGINEERING_ROADMAP.md`：路线图阶段发生变化时。

文档中的日期使用明确的绝对日期。不能把“配置了命令”“worker 可访问”“fixture 运行成功”写成商业软件已经可用；只有 Windows 交互式会话、软件版本、许可证状态、smoke test、原生输出和独立工程证据齐全后，才可以登记实机验收。

## 提交前检查

```bash
git diff --check
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m pytest -q tests/test_<changed_area>.py
```

如果改动跨越多个模块，再运行全量测试：

```bash
.venv/bin/python -m pytest -q
```

提交说明应使用动词开头并说明一个独立能力，例如 `Expose capabilities through CLI`。推送后记录 commit id、测试数量和仍未验证的边界。
