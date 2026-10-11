# 上下文管理 v1 Changes 变更清单

> 关联：[ADR-041](../adr/041-context-management-v1.md) · [Spec](./041-context-management-v1-spec.md) · [Harness](./041-context-management-v1-harness.md)
> 规则：边开发边实时登记（修/增/减 + 作用说明），禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `alembic/versions/20260921_ctx_mgmt.py` | 增 | 迁移 20260921_ctx_mgmt | `llm_providers.context_window`（INTEGER 默认 65536）+ `task_building_sessions.context_compaction`（JSONB） |
| `app/models/llm_provider.py` | 修 | 增 `context_window` 列映射 | 窗口元数据 ORM |
| `app/models/task_building_session.py` | 修 | 增 `context_compaction` 列映射 | 压缩点持久化 |
| `app/schemas/llm_provider.py` | 修 | `LLMModelCreate`/`LLMModelResponse` 增 context_window；增 `LLMModelUpdate` | 模型窗口读写与编辑 Schema |
| `app/services/llm_provider_service.py` | 修 | `LLMProviderConfig` 增 context_window；get/update 透传；`add_model` 透传；增 `update_model()` | 运行时配置携带窗口；模型元数据编辑 |
| `app/api/v1/endpoints/settings.py` | 修 | 增 `PATCH /llm/models/{id}`；三个响应透传 context_window | 模型编辑端点 |
| `app/clients/deepseek.py` | 修 | `chat_completion_stream_raw` message 事件携带 usage | 真实 prompt_tokens 供占用展示 |
| `app/services/context_compaction_service.py` | 增 | estimate_tokens / estimate_messages_tokens / compaction_threshold / should_pre_compact / is_context_overflow_error / _serialize_message / _message_cost / split_for_compaction / build_summary_prompt / build_checkpoint_message / project_messages / `ContextCompactionService.compact_if_needed` | 压缩核心：chars/4 估算、触发判定（窗口−8192−20000）、切分（尾部 8000 token recent）、五段锚定摘要、checkpoint 投影、原文保留 |
| `app/skills/history_recall.py` | 增 | `HistoryRecallSkill` | 历史回查技能：keyword/role/limit 检索会话原始 messages（含工具轨迹），对冲多轮压缩失真 |
| `app/skills/registry.py` | 修 | SKILL_REGISTRY 增 HistoryRecallSkill | 技能注册 |
| `app/agents/orchestrator.py` | 修 | chat_stream：读会话行、预压接入、overflow 兜底重跑（while+retry）、每轮流结束 yield `context_usage`；`_build_messages` 增 context_compaction 投影参数 | 压缩与计量接入对话链路 |
| `tests/services/test_context_compaction.py` | 增 | UT-1~9 共 9 项 | 估算/触发/切分/序列化/锚定/落库/投影/回归/失败降级 |
| `tests/skills/test_history_recall.py` | 增 | UT-14~16 共 6 项 | keyword/role/limit/工具轨迹可检索/无会话/无匹配 |
| `tests/agents/test_orchestrator.py` | 增 | UT-10~13 共 4 项；FakeDeepSeekClient 增 usage/overflow/chat_completion_messages | usage 事件/估算回落/overflow 兜底重跑/普通 400 不误触发 |
| `tests/conftest.py` | 修 | db_session 清库解除 tasks.session_id；`_restore_guarded_tables` 剥离双向关联插入后回填 | 修复真实库 tasks↔sessions 双向外键导致的清库/恢复冲突 |
| 前端 `types/taskBuilding.ts` | 修 | ChatMessageRole 增 tool；ChatMessage 增 tool_calls/tool_call_id；事件类型增 context_usage + TaskBuildStreamContextUsage | 类型支撑 |
| 前端 `types/llmProvider.ts` | 修 | LLMModelItem/LLMModelCreateRequest 增 context_window；增 LLMModelUpdateRequest | 类型支撑 |
| 前端 `api/llmProvider.ts` | 修 | 增 `updateLLMModel()` | PATCH 编辑 |
| 前端 `api/taskBuilding.ts` | 修 | 流式请求 messages 类型放宽（tool 角色 + 轨迹字段） | 轨迹回传 |
| 前端 `hooks/useTaskBuildingChat.ts` | 修 | 增 contextUsage state + context_usage case；sendMessage 透传 tool_calls/tool_call_id | 圆环数据源 + 轨迹回传 |
| 前端 `components/TaskNewInputBar.tsx` | 修 | 增 contextUsage prop + Progress 圆环（绿/橙/红三档阈值 60/80） | 占用实时展示 |
| 前端 `pages/TaskNew.tsx` | 修 | 解构并透传 contextUsage | 接线 |
| 前端 `components/LLMModelsPanel.tsx` | 修 | 增「上下文窗口」列 + 编辑弹窗（updateLLMModel）+ 创建表单窗口字段（顺带修复 supports_vision 提交遗漏） | 窗口可编辑 |
| 前端 `App.tsx` | 修 | 补全 `<Suspense>` 闭合标签 | 修复其他会话遗留的半成品懒加载重构（typecheck 阻塞） |
| `scripts/e2e_ctx_mgmt_20260921.py` | 增 | 端到端验证脚本 | context_usage 事件 + 压缩状态检查 |

## 登记记录

- 2026-09-21：创建骨架（ADR-041 四件套建立，随开发实时登记）。
- 2026-09-21：登记全部实施变更（上表）；期间发现并修复 3 个问题：
  1. `_serialize_message` 截断后才估算导致长消息切分判定失效（head=0）→ 拆分真实成本估算 `_message_cost`（不截断）与展示序列化（截断）；
  2. tests/conftest 清库与数据守卫恢复未解除 tasks↔sessions 双向外键 → 清库解除 + 恢复「剥离关联插入、事后回填」；
  3. 前端 App.tsx 存在其他会话遗留的 `<Suspense>` 未闭合半成品 → 补全闭合以解除 typecheck 阻塞。
- 2026-09-21：端到端验证通过——①context_usage 每轮流结束实时推送（真实 usage：3262/65536=5.0% → 3557/65536=5.4%）；②压缩未触发时对话与轨迹回流回归正常；③压缩闭环（overflow→强制压缩→checkpoint 投影→重跑）由 UT-12 单测覆盖。后端 39 项 + 前端 typecheck/lint 通过（lint 2 项为存量问题）。
- 2026-09-22：修复用户反馈「切换页面再切回后上下文圆环消失」——contextUsage 原存页面内存 state，路由切换组件卸载即丢；改为按 sessionId 写 sessionStorage（`opc:ctx-usage:{id}`，context_usage 事件时写入），reset 扩展第三参 sessionId（恢复历史会话时读回；不传则清空），useTaskSessionActions.restoreSession 传入 session.id。涉及：前端 hooks/useTaskBuildingChat.ts、hooks/useTaskSessionActions.ts、types 无变更。另确认 percent 计量语义正确：圆环显示的是整个请求上下文总占用（system prompt + 工具定义 + 历史，约 2K token 起步），非单条消息占用。
- 2026-09-22：修复用户反馈「调低窗口到 1200 未触发压缩」——三个缺陷：①预压估算漏算 system prompt（技能清单+环境块约 2K token 是大头，tilacode 估的是 system+messages+tools 三者），estimate_messages_tokens 增 system_text 参数，compact_if_needed/orchestrator 调用点透传；②recent 保留量固定 8000 不随窗口缩放，小窗口下 head 恒空永远无可压缩内容，新增 recent_keep_tokens_for(window)=max(128, min(8000, window//8))；③压缩点已覆盖本次 head 时每轮重复调用摘要 LLM，_generate_summary 增防重复判定（previous.message_count >= len(head) 跳过）。前端 Progress format 数字未封顶（显示 197）同步修复。新增回归测试 3 项（累计 40 项全过）。
- 2026-09-23：验证压缩闭环 + 压缩进度可视化。①验证：低窗口（1200）会话 11b0d889 实际触发压缩（日志 `context_compaction_done` message_count=4 summary_tokens=147；DB context_compaction 列含 587 字摘要），新增 `scripts/check_compaction.py` 查询最近 15 个会话压缩状态；②压缩进度提示：orchestrator 预压判定命中与 overflow 兜底前新增 SSE 事件 `context_compacting`（开始）/`context_compacted`（完成，data.compacted 标记是否实际压缩），端点与前端 api 层均为全透传无需改动；前端 useTaskBuildingChat 增 compacting state + 两个事件 case（完成且实际压缩时 antdMessage.info 提示），TaskNewInputBar 圆环压缩中显示「压缩中」文字 + Progress status="active" 动画 + Tooltip 切换文案；③修复「发送消息时圆环消失」：sendMessage 中 setContextUsage(null) 删除，占用保留上一轮数值直至新 context_usage 事件到来。
- 2026-09-23（晚）：修复「显示压缩了但占用依然 5088/3200 不回落」——两个根因：①**token 估算系数对中文低估**：chars/4（tilacode 英文启发式）vs 中文实际 1.5~2 字/token（实测估算 3921 vs 真实 usage 5088，低估 1.3 倍），压缩触发/摘要推进/防重复判定全基于低估估算——该压不压、head 推进判定失真，`CHARS_PER_TOKEN` 4→2（保守高估，宁可提前压缩），测试同步（chars_per_2 断言 / recent_keep 测试消息 500→250 字符）；②**overflow 兜底与预压缩的交互断层**：估算修正后预压缩先行触发并落库压缩点，随后 overflow 的 force 压缩被防重复跳过（did_compact=False）→ 误 raise 中断对话；修复为压缩点已存在时 overflow 重跑本轮依然成立（rebuild 投影 + continue）。新增 `scripts/diagnose_context_usage.py` 占用构成分解（固定开销 system+tools / 历史 / 压缩点）。测试 52 passed；另确认极端小窗口（3200）下固定开销（system+tools ≈1074 tok）占比 34% 属测试配置边界，真实 64k+ 窗口无此问题。
