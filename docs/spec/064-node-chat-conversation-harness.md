# 节点续聊对话 Harness（测试清单与门禁）

> 配套 [ADR-064](../adr/064-node-chat-conversation.md) / [Spec](./064-node-chat-conversation-spec.md) / [Changes](./064-node-chat-conversation-changes.md)。
> 编码前骨架，随 M1–M4 实施回填实测结果。

## 1. 测试金字塔

```
        ┌───────────────────────────────┐
        │ E2E（Runbook 手测 4 场景）      │ 真实节点+真实任务
        └──────────────┬────────────────┘
        ┌──────────────┴────────────────┐
        │ 集成：中枢端点 6 · WS 事件收发 2 │ httpx/WS mock
        └──────────────┬────────────────┘
        ┌──────────────┴────────────────┐
        │ 单元：中枢落库 4 · 节点续聊 8   │ 纯函数/mock
        │ · 前端分流与持久化 5           │
        └───────────────────────────────┘
```

## 2. 单元 / 集成清单

### 中枢（opc-central-dispatch-hub）

- UT-H1 `node-chat` 端点：running → 409；任务不存在 → 404；节点离线 → 422；空闲 → 202 且下行 payload 含 `trace_task_id`/`last_seq`/`reply_to`
- UT-H2 `last_seq` 计算：链上最后任务 max(seq) 正确；无轨迹任务 → 0
- UT-H3 消息写入：用户 @消息（mention_node）落 `task_building_sessions.messages`；节点回复（role=assistant）落库
- UT-H4 `agent.node_chat_reply` 处理：落库 + reply_to 关联；`agent.node_chat_error` 处理：告警不崩
- UT-H5 steer 用户消息落库（回归 ADR-061 M5a 行为不变）
- IT-H6 WS 事件收发：`hub.node_chat` 下行格式；`agent.node_chat_reply` 上行被消费

### 节点（opc-agent-programming）

- UT-N1 `send_prompt` delivery 参数化：续聊传 `queue`，steer 保持 `steer`
- UT-N2 续聊处理：按 root_task_id 定位会话；会话缺失 → `node_chat_error(session_missing)`
- UT-N3 临时采集器：归属 `trace_task_id`、seq 从 `last_seq+1` 续；轮次结束销毁
- UT-N4 回复捕获：轮次结束提取 assistant 全文 → `agent.node_chat_reply`；独立 300s 超时 → error(timeout)
- UT-N5 续聊不进 `_current_tasks`、不改任务状态、不触发看门狗
- UT-N6 竞态：会话活跃时 queue 入队不丢

### 前端（opc-web-console）

- UT-F1 发送分流：空闲 @ → 续聊端点；running @ → steer（既有用例改造）
- UT-F2 消息持久化：乐观回显 + 落库调用失败时本地保留并标错
- UT-F3 历史恢复：messages 含 mention_node 用户消息与节点回复，刷新加载后渲染
- UT-F4 节点回复消息渲染：节点徽标 + 来源标注（D6）
- UT-F5 错误气泡：`node_chat_error` 展示 reason

## 3. E2E（Runbook 手测，待 M4 补操作单）

| # | 场景 | 验收 |
|---|---|---|
| E2E-1 | 空闲任务 @节点 对话 | 回复出现在会话流 + 折叠块过程行 seq 接续 |
| E2E-2 | 刷新恢复 | 用户消息与节点回复仍在 |
| E2E-3 | 执行中 @（steer） | 行为不变 + 用户消息落库 |
| E2E-4 | 异常路径 | 节点停机时 @ → 明确报错、消息保留、任务状态不变 |

## 4. CI 门禁

- 中枢：`python -m ruff check app tests` + `python -m pytest tests/services tests/api -q`
- 节点：`python -m ruff check app tests` + `python -m pytest tests -q`
- 前端：`npx tsc -b` + `npx vitest run`（相关用例）
- 红线：①续聊不影响任务状态机与执行链路；②消息持久化失败不影响对话主链路（旁路告警）；③不触碰 ADR-063 折叠块只读/脱敏红线。
