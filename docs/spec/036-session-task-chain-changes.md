# 会话内任务链自动挂接与规范文档按需生成 Changes（变更清单）

> 关联：[ADR-036](../adr/036-session-task-chain-and-spec-command.md) / [Spec](./036-session-task-chain-spec.md) / [Harness](./036-session-task-chain-harness.md)
>
> 按 `.trae/rules/decision-documents.md` 要求：边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| docs/adr/036-session-task-chain-and-spec-command.md | 增 | - | 决策文档 |
| docs/spec/036-session-task-chain-spec.md | 增 | - | 功能 Spec |
| docs/spec/036-session-task-chain-harness.md | 增 | - | 测试清单 |
| docs/spec/036-session-task-chain-changes.md | 增 | - | 本变更清单 |
| docs/INDEX.md | 修 | 1.1/1.2/1.3/六 | 登记本次决策 |
| docs/adr/012-tilacode-execution-layer-integration.md | 修 | 末尾修订记录 | 目录默认命名修订指向 ADR-036 |
| docs/adr/028-task-workspace-binding-and-artifact-sync.md | 修 | 末尾修订记录 | output_dir 缺省行为修订指向 ADR-036 |
| docs/adr/033-ai-skills-and-smart-dispatch.md | 修 | 末尾修订记录 | create_task/三件套策略/spec 命令修订指向 ADR-036 |
| opc-central-dispatch-hub/app/services/task_service.py | 修 | 新增 `_sanitize_title_for_dir` / `TaskService._auto_link_parent` / `TaskService._resolve_output_dir`；`create_task` 增自动挂链与预生成主键；`_create_task_from_spec` / `_create_task_from_planning` 增 task_id 参数并显式赋 Task.id | 同会话自动挂链 + 可读目录命名（ADR-036） |
| opc-central-dispatch-hub/app/skills/task_creation.py | 修 | description 增自动挂链说明；TaskCreateRequest 构造增 `change_brief=requirement[:500]` | 技能层补本轮修改说明透传 |
| opc-central-dispatch-hub/app/agents/orchestrator.py | 修 | 新增 `_SPEC_COMMAND` / `_match_spec_command` / `_run_forced_spec`；`chat_stream` 前置 /spec 拦截 | /spec 确定性强制生成三件套（ADR-036） |
| opc-central-dispatch-hub/app/agents/orchestrator_prompts.py | 修 | ORCHESTRATOR_BASE_PROMPT 增准则 4/5（文档按需生成 + 自动挂链说明） | 三件套按需生成策略提示词 |
| opc-web-console/src/components/TaskNewCommandPalette.tsx | 增 | TaskNewCommandPalette 组件 + COMMANDS 常量（/spec） | 斜杠命令提示面板（ADR-036） |
| opc-web-console/src/components/TaskNewChatPanel.tsx | 修 | 输入以 / 开头且非发送中时条件渲染命令面板；placeholder 增命令提示 | 面板集成入口 |
| opc-web-console/src/pages/TaskNew.css | 修 | 追加 task-new__cmd-palette / cmd-item / cmd-name / cmd-desc 样式 | 命令面板样式（主题色值取自 tokens 同源） |
| opc-central-dispatch-hub/tests/services/test_task_service_auto_link.py | 增 | 10 项用例：自动挂链/链尾挂接/显式 parent 优先/change_brief 填充与保留/目录命名与净化/继承与回退/显式目录优先/无会话不挂链/跨会话隔离 | 自动挂链与命名回归 |
| opc-central-dispatch-hub/tests/skills/test_create_task_skill.py | 修 | 增 test_skill_passes_change_brief / test_skill_auto_link_via_service | 技能层 change_brief 透传与自动挂链回归 |
| opc-central-dispatch-hub/tests/agents/test_orchestrator.py | 修 | 增 test_spec_command_forces_spec_skill / test_spec_command_tolerates_whitespace / test_normal_message_not_intercepted / test_spec_command_works_when_skill_disabled | /spec 确定性拦截回归 |
| opc-central-dispatch-hub/tests/api/v1/test_task_building.py | 修 | 增 test_spec_command_round_persists_specification_and_steps；http_client fixture 先 dispose 全局引擎连接池（防跨事件循环 pre_ping 撞已关闭 proactor） | /spec 轮真实链路持久化集成测试 + 既有 flaky 修复 |
| opc-central-dispatch-hub/tests/api/v1/test_task_lifecycle.py | 修 | `_delete_task` 先清理任务关联的 DispatchDecision（ADR-033 决策审计行外键引用 tasks） | 既有测试清理缺陷修复（与本次决策无关，顺带修复） |
| opc-central-dispatch-hub/tests/conftest.py | 修 | ws_client fixture 在进入 TestClient 前 `asyncio.run(engine.dispose())` 清空全局引擎连接池 | 修复跨测试模块事件循环残留连接导致的 'NoneType' has no attribute 'send' 批次 ERROR（既有 flaky，与本次决策无关） |

<!-- 开发过程中在此实时追加：文件 → 变更类型（修/增/减） → 函数/类级明细 → 作用说明 -->
