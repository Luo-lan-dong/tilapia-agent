# 节点续聊对话 Spec（`@空闲节点` = 真对话）

> 本文档配套 [ADR-064](../adr/064-node-chat-conversation.md)，修订 [ADR-063](../adr/063-node-agent-trace-mirror.md) D4 空闲分支。
> Changes：[064-node-chat-conversation-changes.md](./064-node-chat-conversation-changes.md)；测试：[064-node-chat-conversation-harness.md](./064-node-chat-conversation-harness.md)。

## 1. 功能描述

任务空闲时，用户在中枢会话 @节点 发文本 → 节点对该任务链的**既有 tilacode 会话**续发一条消息（`delivery="queue"`，上下文天然连续）→ 节点回复全文回流到中枢会话流（节点徽标消息），续聊轮的思考/工具过程实时进该节点折叠块（seq 接续链上最后任务）。用户消息与节点回复均落库，刷新不丢。执行中 @节点 仍走 steer（不变），其用户消息一并补落库。

## 2. 用户故事

- 作为用户，我 @ 一个空闲节点说「把登录按钮改成绿色试试」，它基于之前会话的上下文**回我一句话**，而不是默默开一个新任务。
- 作为用户，节点回复时我能展开折叠块**看它怎么想、调了什么工具**（和任务执行时一样的旁观体验）。
- 作为用户，我发的 @消息和节点的回复**刷新页面后还在**。
- 作为用户，@ 执行中的节点补话（steer）行为不变，我发的话也不丢失。
- 作为用户，节点那边出问题（会话丢失/超时）时我会看到明确报错，且我发的消息不消失。

## 3. 核心流程

```
用户 @空闲节点 + 文本（前端）
  │ ①乐观回显 + ②POST /v1/tasks/{id}/node-chat（持久化用户消息到 task_building_sessions.messages）
  ▼
中枢：校验（running→409 提示走 steer；节点离线→422）
  │  ③查任务链最后任务 id + 该任务 max(seq) + 节点地址
  ▼
WS 下行 hub.node_chat {task_id, root_task_id, node_id, text, reply_to, trace_task_id, last_seq}
  ▼
节点：定位会话 ses_task_{root_task_id}
  │ ④临时挂轨迹采集器（归属 trace_task_id，seq 从 last_seq+1 续）
  │ ⑤send_prompt(delivery="queue") —— 空闲即开新轮；竞态则入队
  │ ⑥订阅该会话事件直至轮次结束（独立超时 300s 兜底）
  ▼
轨迹行实时 POST /agent-trace → 折叠块实时渲染（seq 接续）
  ▼
轮次结束：提取 assistant 全文
  │ ⑦上行 agent.node_chat_reply {reply_to, task_id, node_id, text, tokens}
  ▼
中枢：节点回复落 task_building_sessions.messages → 会话流显示「节点回复」（节点徽标）
```

## 4. 降级链路

| 故障 | 行为 |
|---|---|
| 任务 running 时调 node-chat | 中枢 409，提示走 steer（前端已分流，兜底保护） |
| 节点离线 | 中枢 422，前端报错气泡；**用户消息已落库不丢失** |
| 会话不存在（节点侧重启后未恢复） | 节点回 `agent.node_chat_error`（reason=session_missing）→ 前端气泡提示；消息保留 |
| 续聊轮超过 300s 无结束 | 节点独立超时：中断等待 + 回 error(reason=timeout)；**不触碰任务状态机** |
| 发起瞬间会话恰好活跃（竞态） | `delivery="queue"` 天然入队，消息不丢、不中断执行 |
| seq 接续起点错位 | 中枢下发 `last_seq` 为准；上报 `ON CONFLICT (task_id,seq) DO NOTHING` 幂等兜底 |
| 中枢落库失败 | 回复仍经 SSE/轮询可见于轨迹；落库失败仅告警（旁路原则） |

## 5. 验收标准

1. 空闲任务 @节点发一句话 → 会话流出现**节点回复消息**（节点徽标区分来源），发一句回一句；
2. 折叠块在该轮实时出现 `reasoning`/`tool` 行，且 `seq` 接着该任务原轨迹（无重号/断档）；
3. 刷新页面：用户 @消息与节点回复**均还在**（从消息表加载）；
4. 执行中 @节点：steer 行为不变，且用户消息落库、刷新可见；
5. 会话忙碌竞态下消息不丢（入队执行）；
6. 节点续聊 300s 超时/会话丢失 → 前端明确报错，任务状态不受影响（保持原状态）；
7. 建任务路径下线：node-chat 不再产生新任务、不再触发规范文档校验；
8. 三端回归全绿（节点/中枢 pytest+ruff、前端 vitest+tsc）。
