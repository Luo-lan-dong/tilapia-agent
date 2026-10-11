# Orchestrator Agent Loop v1 Spec（工具轨迹持久化回流 + 多轮循环 + 真流式）

> 关联：[ADR-040](../adr/040-orchestrator-agent-loop-v1.md) · [Harness](./040-orchestrator-agent-loop-v1-harness.md) · [Changes](./040-orchestrator-agent-loop-v1-changes.md)
> 路线图：[040-orchestrator-agent-loop-roadmap.md](./040-orchestrator-agent-loop-roadmap.md) P0

## 1. 功能描述

将中枢对话链路（OrchestratorAgent）从「薄决策循环」升级为 tilacode 式
「持久化多轮思考循环」：工具轨迹（tool_calls + tool 结果）持久化并回流
LLM 请求；每轮 LLM 调用真流式（正文/思考/工具调用增量）；达到轮次上限
三重禁用工具强制收尾。

## 2. 用户故事

- 作为用户，我问"刚才查到的那个节点呢"，模型能引用上一轮工具结果回答（不失忆）。
- 作为用户，我希望看到 AI 回复逐字流出、思考过程实时展示，而不是等生成完一次性出现。
- 作为用户，我希望工具执行期间看到步骤卡片（现有协议保持）。
- 作为运维，我希望每轮对话的技能调用轨迹（调了什么、参数、结果）可从会话记录审计。
- 作为系统，模型达到最大轮次后必须停止调用工具，纯文本收尾。

## 3. 核心流程

```
POST /task-building/chat/stream (messages 历史 + session_id)
   │
   ▼
_build_messages：system + 历史透传（含 tool_calls / role:"tool"）
   │                 └─ 脏数据兜底：tool_calls 缺 tool 响应 → 补空
   ▼
┌─ 循环（round < orchestrator_max_tool_rounds）────────────────┐
│  chat_completion_stream_raw(prompt_messages, tools)          │
│    ├─ delta.reasoning_content → SSE thinking（实时）          │
│    ├─ delta.content          → SSE chunk（实时）              │
│    └─ delta.tool_calls[i]    → 按 index 拼装                  │
│  流结束：                                                     │
│    ├─ 无 tool_calls → 最终回答已流完 → done，返回本轮轨迹       │
│    └─ 有 tool_calls → assistant(tool_calls) 追加 prompt+轨迹缓存 │
│         ├─ SSE step_started → 技能事件 → step_finished（不变） │
│         ├─ tool 结果消息追加 prompt + 轨迹缓存                 │
│         └─ 续行下一轮                                          │
└──────────────────────────────────────────────────────────────┘
   │ 达到上限：tools=[] + tool_choice="none" + FORCE_FINAL_PROMPT
   ▼
落库（端点）：历史 + [assistant(tool_calls), tool×N, assistant(回复)]
```

## 4. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 流式接口不支持 tools（旧版自定义 provider） | 捕获 4xx 含 "tool" 关键字时降级非流式 `chat_completion_raw` 重试一次 |
| 流式响应中途断开（httpx 异常） | 已收到的 chunk/thinking 已下发不撤回；未完成的 tool_calls 丢弃；error + done 事件收尾 |
| tool_calls 参数 JSON 解析失败 | 按 `{}` 空参数执行（现状行为），技能返回参数错误摘要回传模型 |
| assistant 历史带 tool_calls 但缺 tool 响应（脏数据） | `_build_messages` 自动补空 tool 消息（content=""）防 400 |
| DSML 伪工具调用标记出现在正文 | 现有修正逻辑保留：一次不带 tools 的流式重试，仍失败则剥离标记 |
| 流式增量拼装后 tool_calls 为空（纯 tool_choice 异常） | 视为无工具轮，按最终回答处理 |

## 5. 消息格式约定

### 5.1 落库扩展（task_building_sessions.messages JSONB）

```jsonc
[
  {"role": "user", "content": "帮我看看有哪些空闲节点"},
  // 本轮工具轨迹（新增，原 OpenAI 格式）
  {"role": "assistant", "content": "", "tool_calls": [{"id": "call_x", "type": "function", "function": {"name": "find_idle_nodes", "arguments": "{}"}}]},
  {"role": "tool", "tool_call_id": "call_x", "content": "节点 code 空闲..."},
  // 最终回复（现有结构不变，steps/thinking 继续附加）
  {"role": "assistant", "content": "当前空闲节点是...", "steps": [...], "thinking": "..."}
]
```

### 5.2 回流规则（_build_messages）

| 历史消息形态 | 回传 LLM 的形态 |
|------------|----------------|
| user / tool | 原样（role/content / role/tool_call_id/content） |
| assistant 无 tool_calls | `{"role": "assistant", "content"}`（现状，thinking/steps 剥离） |
| assistant 有 tool_calls | `{"role": "assistant", "content", "tool_calls"}`（tool_calls 原样） |
| assistant 有 tool_calls 但缺对应 tool 消息 | 同上 + 自动补 `{"role": "tool", "tool_call_id", "content": ""}` |

## 6. 验收标准

- [ ] 同会话两轮对话：第一轮触发 find_idle_nodes，第二轮问"刚才那个节点呢"，模型能引用结果；
- [ ] 会话 JSONB 中可见 assistant(tool_calls) 与 tool 消息按序落库；
- [ ] 前端收到真流式 chunk（首字延迟 ≈ LLM 首 token 延迟，非生成完再切 4 字符）；
- [ ] thinking 事件为 reasoning_content 实时增量；
- [ ] 达到 max_rounds 的轮次请求 payload 中 tools 为空且 tool_choice="none"；
- [ ] 旧格式会话（无 tool_calls）恢复后对话行为与现状一致；
- [ ] 全部既有 Orchestrator/技能测试回归通过，新增流式拼装与回流单测通过。

## 7. 关联文档

- [ADR-040](../adr/040-orchestrator-agent-loop-v1.md)
- [040-orchestrator-agent-loop-roadmap.md](./040-orchestrator-agent-loop-roadmap.md)
- [api-standards.md](../standards/api-standards.md)（SSE 事件协议 §4.2 语义不变）
