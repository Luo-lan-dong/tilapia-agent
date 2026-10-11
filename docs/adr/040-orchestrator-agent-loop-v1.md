# ADR-040: 中枢 Orchestrator Agent Loop v1（工具轨迹持久化回流 + 多轮循环重构 + 真流式）

## 状态

Accepted

## 日期

2026-09-21

## 问题背景

路线图（040-orchestrator-agent-loop-roadmap.md）探查确认中枢 Orchestrator 对话链路的四项缺陷：

1. **工具轨迹不回流**：`orchestrator.py:_build_messages` 历史只回传 `role/content`，
   assistant 的 `tool_calls` 与对应 `tool` 结果全部丢弃——上一轮调了 `find_idle_nodes`
   查到节点，下一轮问"刚才那个节点呢"模型完全不知道。
2. **轨迹不落库**：`task_building.py:_persist_chat_exchange` 注释明确"工具调用的
   tool 消息不落库"——崩溃/重启后本轮技能轨迹无法审计、无法恢复。
3. **伪流式**：最终回答等 LLM **生成完成后**按 `REPLY_CHUNK_SIZE=4` 切块下发；
   工具轮 `chat_completion_raw` 全程非流式——首字延迟高、工具执行期无反馈。
4. **达轮上限未禁工具**：注入 FORCE_FINAL_PROMPT 后仍携带 tools，模型可能继续
   发起 tool_calls 导致下一轮 400（assistant tool_calls 缺 tool 响应）。

tilacode 对标实现（探查档案章节二/三）：工具轨迹先落库再续行，每轮从 DB 重载含
工具结果的完整历史；全程流式事件驱动（textDelta/toolCall）；步数到限三重禁用工具。

## 决策

与用户确认（2026-09-21）：

1. **轨迹持久化：扩展现有 `task_building_sessions.messages` JSONB**（备选方案 A）。
   assistant 消息扩展 `tool_calls` 字段（OpenAI 原格式），每个工具调用追加一条
   `{"role": "tool", "tool_call_id": ..., "content": ...}` 消息项。无数据库迁移。
   消息顺序：`...用户历史 → assistant(tool_calls) → tool × N → assistant(最终回复)`。
2. **轨迹回流：`_build_messages` 重写**。历史消息透传 `tool_calls` 与 `role:"tool"`
   消息；`steps`/`thinking` 仍为展示层数据不进 LLM 请求（ADR-033 语义不变）；
   兼容旧格式（无 tool_calls 的 assistant 消息行为不变）；assistant 有 tool_calls
   但缺对应 tool 响应的脏数据补空 tool 消息防 400。
3. **真流式：DeepSeekClient 新增 `chat_completion_stream_raw`**。流式解析
   `delta.content` / `delta.reasoning_content` / `delta.tool_calls`（按 `index`
   增量拼装 id/name/arguments）；Orchestrator 循环每轮改流式调用——
   reasoning delta 实时转发 `thinking` 事件、content delta 实时转发 `chunk` 事件、
   流结束后拼装完成的 tool_calls 逐个执行技能（step 协议不变），工具结果
   追加进 prompt_messages 并缓存本轮轨迹供落库，续行下一轮。
4. **多轮循环重构**：续行判定 = 本轮出现 tool_calls（与 tilacode 一致，不看
   stop reason）；达到 `orchestrator_max_tool_rounds` 时 **tools 置空 +
   `tool_choice: "none"`** + FORCE_FINAL_PROMPT 强制纯文本收尾（三重禁用）；
   DSML 伪工具调用修正轮保留（流式、不带 tools）。
5. **thinking 真流式一并实施**（用户确认）：`reasoning_content` 增量实时转发
   `thinking` 事件，前端协议不变。
6. **崩溃恢复简化**：本轮工具轨迹在流式完成后由端点一次性落库；中途崩溃本轮
   轨迹丢弃（与现状一致，不做 tilacode failInterruptedTools 等价物，列入 P3
   执行保护再评估）。

明确不做（本次）：

- 工具输出双轨制 / doom loop / 技能超时（P3）、上下文压缩（P1）、环境 Context
  Source（P2）、Steering（P5）——按路线图依次实施。
- spec_builder 非流式 `/chat` 端点与任务规划（planning）链路不改。

## 备选方案

| 方案 | 结论 |
|------|------|
| A. 扩展现有 messages JSONB（本决策） | 采纳。无迁移、前端会话恢复接口零改动、与「轻量版」路线一致 |
| B. 新建独立 session_messages 表（seq 序号列，tilacode 原生形态） | 否决。需迁移 + 改造前端恢复接口，当前规模收益不足；P1 压缩若需 per-message seq 再升级 |
| C. 仅改真流式、轨迹持久化推迟 | 否决。轨迹回流是「多轮思考」的地基，分开上线会造成伪流式与失忆两套中间态 |
| D. 沿用伪流式仅加工具事件 | 否决。与用户目标（真流式）直接冲突 |

## 影响

### 对中枢后端的影响

- `app/clients/deepseek.py`：新增 `chat_completion_stream_raw(messages, tools)`；
  `_parse_sse_lines` 扩展 tool_calls 增量解析；`_build_payload` 支持 `tool_choice`。
- `app/agents/orchestrator.py`：`chat_stream` 循环重构（流式 + 轨迹缓存 + 三重禁用）；
  `_build_messages` 重写（tool 轨迹回流 + 脏数据兜底）；`_yield_final_answer`
  退役为流式版本的 DSML 修正逻辑；新增本轮历史轨迹内部事件供端点落库。
- `app/api/v1/endpoints/task_building.py`：`_persist_chat_exchange` 按
  `assistant(tool_calls) → tool × N → assistant(回复)` 顺序落库。
- `app/services/orchestrator_service.py`：透传新增内部事件（对外协议不变）。

### 对前端的影响

- 无协议变更：`chunk` 语义从"切块"变为"真流式 delta"，`thinking` 从整段/切块
  变为增量，`step_started`/`step_finished`/`specification`/`done`/`error` 不变。
- 历史会话恢复：assistant 消息新增 `tool_calls` 字段（JSONB 透传），前端现有
  渲染忽略未知字段即可。

### 兼容性

- 旧会话数据（无 tool_calls）回流行为与现状一致。
- 回归风险集中在流式 tool_calls 增量拼装（分片交错场景），单测覆盖。
- DeepSeek/OpenAI 兼容格式通用，自定义 OpenAI 兼容 provider（ADR-030）同样受益。

## 实际实施情况

- 全部按决策实施（2026-09-21 完成），无决策偏差。
- 联调发现并修复 2 个边界：
  1. 轨迹 assistant content 为 None 与 `ChatMessage.content: str` 校验冲突 → 源头归一化为空串；
  2. `_inject_attachment_images` 把非 user 消息削平为 `{role, content}`，丢失
     tool_calls/tool_call_id → provider 400「tool 消息失去前置关联」→ 修复为非 user 消息原样透传。
- 端到端验证：真流式确认（138 chunk / 562ms 分散到达）、轨迹按序落库、
  第二轮零技能调用直接引用第一轮工具结果（多轮思考闭环）。
- 测试：orchestrator 16 项 + deepseek 流式 6 项 + 落库 3 项新增/更新全部通过；
  全量回归 370 passed（2 失败均为共享开发库数据污染/环境竞态，失败点两次运行间漂移，单跑通过）。

## 关联文档

- [040-orchestrator-agent-loop-roadmap.md](../spec/040-orchestrator-agent-loop-roadmap.md)（路线图与探查档案）
- [ADR-033](./033-ai-skills-and-smart-dispatch.md)（技能框架，本决策为其循环层升级）
- [ADR-030](./030-llm-models-management.md)（多模型管理，流式方法需兼容自定义 provider）
- [tilacode runner/llm.ts](../../opc-agent-programming/tilacode/packages/core/src/session/runner/llm.ts)（对标实现）
