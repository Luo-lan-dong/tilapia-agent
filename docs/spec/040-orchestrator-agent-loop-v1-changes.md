# Orchestrator Agent Loop v1 Changes 变更清单

> 关联：[ADR-040](../adr/040-orchestrator-agent-loop-v1.md) · [Spec](./040-orchestrator-agent-loop-v1-spec.md) · [Harness](./040-orchestrator-agent-loop-v1-harness.md)
> 规则：边开发边实时登记（修/增/减 + 作用说明），禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/clients/deepseek.py` | 修 | 增 `chat_completion_stream_raw()` | 流式 function calling：`delta.reasoning_content`/`delta.content`/`delta.tool_calls` 三类增量事件，tool_calls 按 index 分片拼装（首帧 id/name + arguments 增量），流结束 yield 完整 assistant message；tool_choice 透传；usage 末帧落库复用 |
| `app/agents/orchestrator.py` | 修 | 重写 `_build_messages()` | 工具轨迹回流：历史透传 assistant.tool_calls 与 role:"tool" 消息；steps/thinking 剥离（展示层）；旧格式兼容；脏数据兜底（tool_calls 缺 tool 响应自动补空消息防 400） |
| `app/agents/orchestrator.py` | 修 | 重写 `chat_stream()` 主循环 | 非流式 for 循环 → 真流式循环：reasoning delta 实时转发 thinking、content delta 经 DSML 过滤器实时转发 chunk；工具轮 assistant(tool_calls)+tool 结果进 prompt 与轨迹缓存；每轮工具轨迹经内部事件 `_history_trajectory` 交端点落库 |
| `app/agents/orchestrator.py` | 修 | 达轮上限收尾 | 三重禁用（对齐 tilacode max-steps）：不携带 tools + `tool_choice="none"` + FORCE_FINAL_PROMPT，新增 `_stream_plain_answer()` 流式收尾 |
| `app/agents/orchestrator.py` | 修 | 删 `_yield_final_answer()`，增 `_DsmlStreamFilter` / `_stream_fake_tool_correction()` | 伪流式 4 字符切块退役；DSML 标记改为流式检测（尾部前缀缓冲 + 污染中止），修正重试流式化 |
| `app/agents/orchestrator.py` | 修 | 增常量 `_TRAJECTORY_EVENT = "_history_trajectory"` | 内部事件名（端点消费，不透传前端） |
| `app/api/v1/endpoints/task_building.py` | 修 | `_task_build_event_stream()` | 收集 `_history_trajectory` 内部事件（每轮全量累积取最后一份）；不透传前端；透传 `_persist_chat_exchange` |
| `app/api/v1/endpoints/task_building.py` | 修 | `_persist_chat_exchange()` | 新增 `trajectory_messages` 参数：轨迹按序插入用户历史与最终回复之间落库 |
| `app/schemas/task_building.py` | 修 | `ChatMessage` | role pattern 扩展 `tool`；新增 `tool_calls` / `tool_call_id` 可选字段（缺失会导致 JSONB 序列化静默丢字段 + tool 消息 422） |
| `app/agents/orchestrator.py` | 修 | `_inject_attachment_images()` | 端到端联调发现：非 user 消息被 `{"role","content"}` 削平丢失 tool_calls/tool_call_id，导致 provider 400（tool 消息失去前置关联）；修复为非 user 消息原样透传，仅 user 消息走附件转换 |
| `tests/agents/test_orchestrator.py` | 修 | `FakeDeepSeekClient` 改流式脚本；4 个覆写客户端适配 | mock 迁移到 `chat_completion_stream_raw`；新增 UT-7/8/9（回流/兼容/兜底）、UT-16（轨迹事件）测试；max_rounds 测试补三重禁用断言 |
| `tests/clients/test_deepseek_stream_tools.py` | 增 | 6 项测试 | UT-1 单工具分片拼装 / UT-2 多工具交错 / UT-3 混合流三类事件 / UT-4 usage 落库 / UT-5 流式 4xx / UT-6 tool_choice 透传 |
| `tests/api/v1/test_task_building_session.py` | 增 | 3 项测试 | UT-17 轨迹按序落库 / UT-18 三件套不覆盖回归 / UT-19 无轨迹兼容回归 |

## 登记记录

- 2026-09-21：创建骨架（ADR-040 四件套建立，随开发实时登记）。
- 2026-09-21：登记全部实施变更（上表 13 项）；期间修复 2 个边界——①轨迹 assistant content None 与 `ChatMessage.content: str` 冲突，源头归一化为空串；②`_inject_attachment_images` 削平非 user 消息丢失 tool 关联字段（端到端联调发现）。
- 2026-09-21：端到端验证通过——①真流式确认（chunk 逐 token 分散到达，138 片/562ms 跨度）；②轨迹按序落库（user → assistant(tool_calls) → tool → assistant 回复）；③跨轮回引用闭环（第二轮问"刚才那个节点叫什么"直接引用第一轮工具结果且零技能调用）。注意：落库发生在 done 事件之后，客户端提前断开 SSE 会丢本轮落库（与既有行为一致，前端 EventSource 保持连接不受影响）。
- 新增端到端验证脚本 `scripts/e2e_agent_loop_v1_20260921.py`（供路线图后续 P1-P5 实施复用）。
