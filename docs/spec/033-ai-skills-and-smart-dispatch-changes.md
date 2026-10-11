# 中枢 AI 技能体系与智能调度 Changes（变更清单）

> 关联：[ADR-033](../adr/033-ai-skills-and-smart-dispatch.md) / [Spec](./033-ai-skills-and-smart-dispatch-spec.md) / [Harness](./033-ai-skills-and-smart-dispatch-harness.md)
>
> 按 `.trae/rules/decision-documents.md` 要求：边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| docs/adr/033-ai-skills-and-smart-dispatch.md | 增 | - | 决策文档 |
| docs/spec/033-ai-skills-and-smart-dispatch-spec.md | 增 | - | 功能 Spec |
| docs/spec/033-ai-skills-and-smart-dispatch-harness.md | 增 | - | 测试清单 |
| docs/spec/033-ai-skills-and-smart-dispatch-changes.md | 增 | - | 本变更清单 |
| docs/INDEX.md | 修 | 1.1/1.2/1.3/六 | 登记本次决策 |
| docs/standards/api-standards.md | 修 | §3 错误码 204001 / §4.3 SSE 事件 step_started、step_finished / §7 变更记录 | 协议规范登记 |
| opc-central-dispatch-hub/alembic/versions/20260914_ai_skills_and_dispatch_decisions.py | 增 | upgrade/downgrade | hub_skills + dispatch_decisions 两表迁移 + system_settings.ai_dispatch_enabled 列 + 5 技能种子数据 |
| opc-central-dispatch-hub/app/models/hub_skill.py | 增 | HubSkill | 技能注册表模型（skill_key 唯一 + enabled 开关） |
| opc-central-dispatch-hub/app/models/dispatch_decision.py | 增 | DispatchDecision | AI 调度决策审计模型（候选快照/AI 原始输出/validated/fallback/耗时） |
| opc-central-dispatch-hub/app/models/system_settings.py | 修 | SystemSettings.ai_dispatch_enabled | AI 智能调度开关持久化列（NULL 回退 env） |
| opc-central-dispatch-hub/app/models/__init__.py | 修 | __all__ | 导出 HubSkill / DispatchDecision |
| opc-central-dispatch-hub/app/core/config.py | 修 | Settings | 新增 AI_DISPATCH_ENABLED / AI_DISPATCH_TIMEOUT_SECONDS / ORCHESTRATOR_MAX_TOOL_ROUNDS |
| opc-central-dispatch-hub/app/core/errors.py | 修 | ErrorCode.SKILL_NOT_FOUND = 204001 | 技能域业务错误码 |
| opc-central-dispatch-hub/.env.example | 修 | - | 同步 3 个新配置项示例 |
| opc-central-dispatch-hub/app/skills/__init__.py | 增 | - | 技能包导出 |
| opc-central-dispatch-hub/app/skills/base.py | 增 | Skill / SkillContext / SkillResult / RESULT_EVENT | 技能基类与执行协议（async generator，中间事件 + _result 终止事件） |
| opc-central-dispatch-hub/app/skills/registry.py | 增 | SKILL_REGISTRY / find_skill / get_enabled_skills / to_openai_tools | 技能注册表：代码注册 + hub_skills 启用开关过滤 + OpenAI tools 定义转换 |
| opc-central-dispatch-hub/app/skills/node_inspection.py | 增 | FindIdleNodesSkill / GetNodeMetricsSkill | 查找空闲节点（复用 ADR-032 预筛）/ 节点资源详情 |
| opc-central-dispatch-hub/app/skills/task_skills.py | 增 | GetTaskStatusSkill | 任务详情 / 队列概况查询 |
| opc-central-dispatch-hub/app/skills/dispatch_skill.py | 增 | SmartDispatchSkill | 对话智能派发：候选筛选 → AI 决策 → 审计落库 → dispatch_task 指定节点派发 |
| opc-central-dispatch-hub/app/skills/spec_generation.py | 增 | GenerateSpecDocsSkill | 包装 SpecBuilderAgent.chat_stream，转发 chunk/thinking/specification_delta 等事件 |
| opc-central-dispatch-hub/app/services/dispatch_decision_service.py | 增 | list_available_nodes / build_candidate_snapshot / is_ai_dispatch_enabled / decide_assignments / record_decision / NodeDecision | 候选预筛泛化（与 scheduler 同口径）+ 批量 AI 决策（白名单校验、超时/幻觉不重试回退）+ 决策审计 |
| opc-central-dispatch-hub/app/services/scheduler.py | 修 | dispatch_pending_tasks 重写；删 _find_best_available_node；增 _classify_pending_tasks / _assign_plan / _quick_path_decision | 三路分类（快速路径/规则/AI 决策）+ 批量决策钩子 + 决策与分配同事务提交 |
| opc-central-dispatch-hub/app/clients/deepseek.py | 修 | 增 chat_completion_raw；chat_completion_messages 改为包装调用 | 支持 function calling（tools 参数 + 完整 message 响应，含 tool_calls） |
| opc-central-dispatch-hub/app/agents/orchestrator_prompts.py | 增 | ORCHESTRATOR_SYSTEM_PROMPT 等 | 对话 Agent 系统提示词（技能调用准则 + 强制收敛提示） |
| opc-central-dispatch-hub/app/agents/orchestrator.py | 增 | OrchestratorAgent.chat_stream / _run_tool_call / _yield_final_answer | 统一对话链路：function calling 循环、step_started/step_finished 事件、技能事件转发、tool 消息回传、最大轮数强制收敛 |
| opc-central-dispatch-hub/app/services/orchestrator_service.py | 增 | OrchestratorService | 对话链路服务层包装（含 with_client 测试注入） |
| opc-central-dispatch-hub/app/services/skill_service.py | 增 | SkillService.list_skills / get_skill / set_skill_enabled | 技能管理服务（注册表元数据 + hub_skills upsert） |
| opc-central-dispatch-hub/app/services/system_settings_service.py | 修 | 增 set_ai_dispatch_enabled | 开关写入（下一轮调度热生效） |
| opc-central-dispatch-hub/app/schemas/skill.py | 增 | SkillResponse / SkillDetailResponse / SkillUpdateRequest / AIDispatchSettingsResponse / AIDispatchSettingsUpdate | 技能与 AI 调度设置 DTO |
| opc-central-dispatch-hub/app/api/deps.py | 修 | 增 get_skill_service / get_orchestrator_service | 依赖注入 |
| opc-central-dispatch-hub/app/api/v1/endpoints/skills.py | 增 | GET "" / GET /{skill_key} / PATCH /{skill_key} | 技能管理端点 |
| opc-central-dispatch-hub/app/api/v1/endpoints/settings.py | 修 | 增 GET/PUT /ai-dispatch | AI 智能调度开关端点 |
| opc-central-dispatch-hub/app/api/v1/endpoints/task_building.py | 修 | task_build_chat_stream 切换 OrchestratorService；保留非流式 /chat 走 SpecBuilderService | 流式对话端点接入统一技能链路，step 事件透传前端 |
| opc-central-dispatch-hub/app/api/v1/api.py | 修 | api_router 注册 skills 路由（prefix=/skills） | 路由聚合 |
| opc-central-dispatch-hub/tests/services/test_ai_dispatch_decision.py | 增 | 7 项用例 | 开关关闭行为不变/快速路径零 LLM/采纳合法选择/幻觉回退不重试/解析失败回退/LLM 异常回退/上下文瘦身 |
| opc-central-dispatch-hub/tests/services/test_skill_registry.py | 增 | 6 项用例 | 注册表契约/5 技能/未知 key/tools 结构/启用过滤/默认全启用 |
| opc-central-dispatch-hub/tests/agents/test_orchestrator.py | 增 | 3 项用例 | 技能调用事件流/未知技能名兜底/最大轮数强制收敛 |
| opc-web-console/src/types/skill.ts | 增 | SkillSummary / SkillDetail / SkillUpdateRequest / AIDispatchSettings | 技能类型定义 |
| opc-web-console/src/types/taskBuilding.ts | 修 | ChatMessage.steps / SkillStep / 事件类型扩展 | 步骤卡片数据结构 + SSE 事件类型 |
| opc-web-console/src/api/skills.ts | 增 | fetchSkills / fetchSkillDetail / updateSkillEnabled / fetchAIDispatchSettings / updateAIDispatchSettings | 技能与设置 API 封装 |
| opc-web-console/src/pages/Skills.tsx | 增 | Skills 页面 | 技能管理页：列表 + 启用开关 + 详情抽屉（完整 JSON Schema） |
| opc-web-console/src/components/SkillStepList.tsx | 增 | SkillStepList / StepIcon / StepBody | 对话内可折叠技能执行步骤卡片（对标 Trae 执行轨迹） |
| opc-web-console/src/components/AIDispatchCard.tsx | 增 | AIDispatchCard | 系统设置页 AI 智能调度开关卡片 |
| opc-web-console/src/components/TaskNewChatPanel.tsx | 修 | 消息渲染插入 SkillStepList | assistant 消息渲染技能步骤 |
| opc-web-console/src/components/AppLayout.tsx | 修 | menuItems 增「技能」/ getSelectedKey / getPageTitle | 侧边栏技能入口 |
| opc-web-console/src/components/TaskNewRightPanel.tsx | 修 | TaskNewDocPanel 补 onTabChange 透传 | 修复存量缺失 prop 导致的 tsc 报错 |
| opc-web-console/src/pages/Settings.tsx | 修 | 挂载 AIDispatchCard | AI 智能调度开关入口 |
| opc-web-console/src/pages/TaskNew.css | 修 | .skill-step__* 样式 | 步骤卡片深色主题样式 |
| opc-web-console/src/App.tsx | 修 | /skills 路由 + Skills import | 路由注册 |
| opc-web-console/src/hooks/useSkillSteps.ts | 增 | useSkillSteps | 步骤事件状态管理（从 useTaskBuildingChat 拆出） |
| opc-web-console/src/hooks/useTaskBuildingChat.ts | 修 | 增 step_started/step_finished 分支接入 applyStepEvent；顶部加豁免注释 | SSE 步骤事件接入对话状态机 |
| opc-web-console/src/hooks/useTaskBuildingChat.ts | 修 | thinking/chunk 两处消息重建改为 spread 保留全部字段 | 修复：技能步骤卡片被流式增量覆盖丢失（用户测试反馈） |
| opc-central-dispatch-hub/app/agents/orchestrator_prompts.py | 修 | ORCHESTRATOR_SYSTEM_PROMPT 拆为 ORCHESTRATOR_BASE_PROMPT + build_system_prompt(enabled, disabled)；增 ORCHESTRATOR_FAKE_TOOL_CORRECTION | 系统提示词动态生成：启用/禁用技能清单分离，禁止文本输出工具调用语法（用户测试反馈：技能禁用时模型输出 DSML 伪调用） |
| opc-central-dispatch-hub/app/agents/orchestrator.py | 修 | _build_messages 增 skills/disabled_skills 参数；_yield_final_answer 增伪工具调用检测 + 一次不带 tools 的修正重试 + _strip_fake_tool_syntax 剥离兜底；增 _contains_fake_tool_syntax | DSML 伪调用兜底：修正为自然语言说明技能已禁用 |
| opc-central-dispatch-hub/tests/agents/test_orchestrator.py | 修/增 | 增 test_fake_tool_syntax_corrected / test_fake_tool_syntax_stripped_when_correction_unavailable / test_build_system_prompt_lists_enabled_and_disabled | DSML 修正轮与动态 prompt 回归 |
| opc-central-dispatch-hub/app/api/v1/endpoints/task_building.py | 修 | _task_build_event_stream 持久化条件去掉 specification 非空限制；_persist_chat_exchange specification 改为可选（None 时不传，保留会话已有三件套） | 修复：会话自动实时保存——普通技能对话（无文档生成）每轮流结束也落库（用户测试反馈：不点保存会话不保存） |
| opc-web-console/src/components/TaskNewDocPanel.tsx | 修 | 删「保存会话」按钮与 onSaveSession prop | 会话自动保存，移除手动保存入口 |
| opc-web-console/src/components/TaskNewRightPanel.tsx | 修 | 删 onSaveSession prop 链 | 同上 |
| opc-web-console/src/pages/TaskNew.tsx | 修 | 删 onSaveSession 回调与 handleSaveSession 解构；注释更新 | 同上 |
| opc-web-console/src/hooks/useTaskSessionActions.ts | 修 | 删 handleSaveSession；params 移除 messages/specification/mode/model/createSession/updateSession | 手动保存动作删除，仅保留恢复/继续/新建/删除 |
| opc-central-dispatch-hub/tests/api/v1/test_task_building.py | 修 | test_task_build_chat_stream_returns_sse_events mock 目标 SpecBuilderService → OrchestratorService | 修复：流式端点切 Orchestrator 后测试误打真实 LLM |
| opc-central-dispatch-hub/app/agents/orchestrator.py | 修 | _run_tool_call 执行技能前回填 context.args = args | 修复：LLM 工具参数未传入技能执行上下文——可选参数技能"恰好成功"掩盖问题，smart_dispatch 必填 task_id 报「任务 ID 无效: ''」（用户测试反馈） |
| opc-central-dispatch-hub/tests/agents/test_orchestrator.py | 修/增 | 增 test_tool_call_args_passed_to_skill（FakeSkill 捕获 context.args 断言） | 参数传递链路回归 |
| opc-central-dispatch-hub/app/skills/base.py | 修 | SkillContext 增 session_id 字段 | 技能可感知当前对话会话，支持派发后回写任务关联 |
| opc-central-dispatch-hub/app/agents/orchestrator.py | 修 | chat_stream 增 session_id 参数并透传 SkillContext | 会话 ID 传入技能执行上下文 |
| opc-central-dispatch-hub/app/services/orchestrator_service.py | 修 | chat_stream 透传 session_id | 服务层签名同步 |
| opc-central-dispatch-hub/app/api/v1/endpoints/task_building.py | 修 | chat_stream 调用传 request.session_id | 端点层注入会话上下文 |
| opc-central-dispatch-hub/app/skills/dispatch_skill.py | 修 | 增 _bind_task_to_session：派发成功后更新 TaskBuildingSession.task_id（失败仅告警） | 修复：AI 派发的任务不进入对话页跟踪态——产物 Tab 显示「暂无进行中的任务」（用户测试反馈，根因：跟踪态依赖 URL ?task= 参数，旧链路由「创建任务」按钮写入，AI 派发不经过该入口） |
| opc-web-console/src/pages/TaskNew.tsx | 修 | handleSend 对话结束后同步会话详情，发现 task_id 且 URL 无 task 参数时写入 URL 进入跟踪态 | 前端感知 AI 派发结果，产物/摘要/方案 Tab 关联任务 |
| opc-central-dispatch-hub/tests/skills/test_smart_dispatch_session_binding.py | 增 | 2 项用例：绑定成功回写会话 / 无 session_id 跳过绑定 | 会话绑定回归 |
| opc-central-dispatch-hub/tests/conftest.py | 修 | db_session 清理链路增 delete(DispatchDecision) | 新表外键依赖加入测试清理顺序 |
| opc-central-dispatch-hub/app/skills/task_creation.py | 增 | CreateTaskSkill（create_task）：校验会话三件套非空模板 → TaskService.create_task（spec_chat 链路自动绑定会话 task_id）→ 结果引导 LLM 续调 smart_dispatch | 补全 AI 自主派发流程断层（用户测试反馈：三件套只是草案未落库，AI 派发时无任务可派、反向索要任务 ID）——AI 可自主完成「创建任务 → 智能派发」全链路，无需手动点「创建任务」 |
| opc-central-dispatch-hub/app/skills/registry.py | 修 | SKILL_REGISTRY 增 CreateTaskSkill | 注册表 5 → 6 个技能（hub_skills 无记录默认启用，无需迁移） |
| opc-central-dispatch-hub/app/agents/orchestrator_prompts.py | 修 | 行为准则增「创建任务 → smart_dispatch 派发」标准链路 | 引导 LLM 两步自动完成 |
| opc-central-dispatch-hub/tests/skills/test_create_task_skill.py | 增 | 2 项用例：三件套创建成功+会话绑定 / 无三件套失败提示 | create_task 技能回归 |
| opc-central-dispatch-hub/tests/services/test_skill_registry.py | 修 | 技能数量断言 5 → 6 | 注册表扩容同步 |
| opc-web-console/src/components/TaskNewIterationBar.tsx | 减 | 整文件删除 | 移除「基于任务 xx 继续迭代」迭代条（用户反馈：继续修改同一项目直接回到对应会话即可） |
| opc-web-console/src/pages/TaskNew.tsx | 修 | 删迭代条渲染/changeBrief 状态/handleCancelParent/change_brief 字段；handleSend 传 parent_task_id+output_dir 上下文；useParentTaskContext 不再解构 | 迭代进入 ?parent= 直接恢复父任务会话，无迭代 UI |
| opc-web-console/src/hooks/useTaskSessionActions.ts | 修 | 删 onClearChangeBrief 参数与调用 | 迭代条删除联动 |
| opc-web-console/src/hooks/usePlanReviewFlow.ts | 修 | 删 onClearChangeBrief 参数与调用 | 同上 |
| opc-web-console/src/hooks/useTaskBuildingChat.ts | 修 | sendMessage 增 context 参数（parent_task_id/output_dir）透传请求 | 任务链上下文随对话请求下发 |
| opc-web-console/src/api/taskBuilding.ts | 修 | TaskBuildingChatStreamOptions 增 parent_task_id/output_dir 并入 body | 同上 |
| opc-web-console/src/types/taskBuilding.ts | 修 | TaskBuildChatRequest 增 parent_task_id/output_dir | 类型同步 |
| opc-central-dispatch-hub/app/schemas/task_building.py | 修 | TaskBuildChatRequest 增 parent_task_id/output_dir | 对话请求携带任务链上下文 |
| opc-central-dispatch-hub/app/agents/orchestrator.py | 修 | chat_stream 增 parent_task_id/output_dir 参数透传 SkillContext | 会话链路注入任务链上下文 |
| opc-central-dispatch-hub/app/services/orchestrator_service.py | 修 | chat_stream 透传 parent_task_id/output_dir | 服务层签名同步 |
| opc-central-dispatch-hub/app/api/v1/endpoints/task_building.py | 修 | chat_stream 调用传 parent_task_id/output_dir | 端点层注入 |
| opc-central-dispatch-hub/app/skills/base.py | 修 | SkillContext 增 parent_task_id/output_dir | 技能可感知任务链与工作目录 |
| opc-central-dispatch-hub/app/skills/task_creation.py | 修 | TaskCreateRequest 构造带 parent_task_id/output_dir | AI 创建的迭代任务复用任务链（Agent 端复用根任务工作目录，ADR-028） |
| opc-central-dispatch-hub/app/api/v1/endpoints/task_building.py | 修 | _task_build_event_stream 收集 step_started/step_finished 组装 steps；_persist_chat_exchange 增 steps 参数附加到 assistant 消息 | 修复：中枢重启/会话恢复后技能步骤卡片不再显示——步骤随会话持久化（用户测试反馈） |
| opc-web-console/src/hooks/useParentTaskContext.ts | 修 | 恢复父任务会话时若 session.task_id 存在且 URL 无 task 参数 → 写入 URL 建立跟踪态 | 修复：中枢重启后回到 ?parent= 会话产物 Tab 显示「暂无进行中的任务」（用户测试反馈）——上一轮任务产物立即可见 |
| opc-central-dispatch-hub/app/skills/spec_generation.py | 修 | execute 末尾 `context.current_specification = final_spec` 回写生成结果 | 修复：同轮对话 generate_spec_docs 成功后 create_task 报「当前对话还没有可执行的三件套规范文档」连续失败（用户测试反馈 3 次）——根因：生成结果只经 SSE 事件更新前端面板，执行上下文仍是本轮请求开始时的旧快照，create_task 读取到 None/旧值 |
| opc-central-dispatch-hub/tests/skills/test_spec_generation.py | 增 | 2 项用例：生成成功回写 context.current_specification / 生成失败不覆盖原值 | 技能间上下文传递回归 |
| opc-central-dispatch-hub/app/schemas/task_building.py | 修 | ChatMessage 增可选 thinking/steps 字段 | 修复：中枢重启/会话恢复后步骤卡片与思考内容消失（用户测试反馈）——根因 1：schema 仅有 role/content，Pydantic 验证丢弃 steps；根因 2：thinking 增量从未收集落库 |
| opc-central-dispatch-hub/app/api/v1/endpoints/task_building.py | 修 | _task_build_event_stream 收集 thinking 增量（thinking_chunks）；_persist_chat_exchange 增 thinking 参数并随 assistant 消息落库（content/thinking/steps 任一非空即落）；请求侧 model_dump 改 exclude_none=True | 根因 2 修复：思考内容随会话持久化；排除 None 键进入消息历史 |
| opc-central-dispatch-hub/app/agents/orchestrator.py | 修 | _build_messages 历史消息仅回传 role/content（history_messages 列表推导） | 防御：assistant 消息携带的 thinking/steps 是展示层数据，不得进入 LLM 请求体（未知字段可能导致 API 400） |
| opc-central-dispatch-hub/app/services/task_building_session_service.py | 修 | create/update 的 messages 落库 model_dump 改 exclude_none=True | user 消息落库不写 thinking/steps 的 None 键，保持 JSON 干净 |
| opc-central-dispatch-hub/tests/api/v1/test_task_building.py | 增 | test_stream_persists_steps_and_thinking：流式对话含 thinking/step 事件 → 会话详情 assistant 消息带 thinking/steps，user 消息不携带 | 步骤/思考持久化回归 |
| opc-central-dispatch-hub/tests/conftest.py | 修 | db_session 清理链路增 delete(TaskChainContext)（import 同步） | 修复 ADR-034 遗漏：task_chain_contexts 外键引用 tasks，清理缺表导致测试批量报 ForeignKeyViolationError |

<!-- 开发过程中在此实时追加：文件 → 变更类型（修/增/减） → 函数/类级明细 → 作用说明 -->
