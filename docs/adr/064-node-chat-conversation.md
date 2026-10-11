# ADR-064: 节点续聊对话（`@空闲节点` = 真对话，下线建任务路径）

## 状态

Accepted（**用户已裁决**：完整版过程展示一步到位 / 下线建任务路径 / 详设认可，2026-10-05）

**实施进度（2026-10-05）**：M1（中枢）/ M2（节点）/ M3（前端）**全部完成**并经三端回归（中枢 583+6 passed / 节点 270 passed / 前端 24+ passed）；M4 收尾含一轮**真实环境 E2E 循环修复**：0.6.1→0.6.3 三轮构建部署两节点，探针实测全绿——真实任务 completed（L1 判据）→ `@节点` 续聊 202 → 续聊轮轨迹 seq 接续（204 行单调）→ **节点真实回复落库会话消息**。循环中修复一处真缺陷：回复提取 `reply_extract_failed`（context 端点解析对续聊轮取不到文本）→ 改为 `_run_node_chat_round` 直接捕获 SSE 轨迹 text 行（所见即所得），context 解析降为回退。登记见 `docs/spec/064-node-chat-conversation-changes.md`；踩坑（BOM / 版本常量脱节 / 落库时序）见 Runbook §4。

> 本 ADR **修订 [ADR-063](./063-node-agent-trace-mirror.md) 的 D4 空闲分支**：原「空闲 → 建后续任务派发」语义废弃，改为「节点续聊对话」。ADR-063 其余决策（D1 会话视图 / D2 只读旁观 / D3 整体落库 / D5 群聊式 / D6 来源标注）与执行中 @节点 → steer 分支**保持不变**。

## 日期

2026-10-05

## 问题背景

ADR-063 M4 上线后用户实测反馈（附截图）三个问题：

| # | 问题 | 根因（2026-10-05 只读侦察，文件:行） |
|---|---|---|
| S1 | @空闲节点报「任务缺少规范文档，无法派发」 | `start_node_chat` 建后续任务**未复制 `specification` 字段**（`task_service.py:1123-1141`），而 `dispatch_task` 硬校验 `specification is None` 即拒（`task_service.py:1218-1222`）→ 必然拦截 |
| S2 | 语义不符：用户要的是「像跟中枢聊天一样，基于节点侧上下文对话」，不是开新任务 | D4 被实现为「建任务 + 派发」：节点不会回复一句话，而是默默执行新任务 |
| S3 | 发送失败/刷新后 @消息消失 | `appendUserMessage` 仅前端本地回显（`useTaskBuildingChat.ts:162-164`）；中枢唯一消息持久化 `task_building_sessions.messages` 只有规划 Chat 写入；steer 的用户消息同样不落库 |

### 关键侦察事实（可行性依据）

| 事实 | 证据 |
|---|---|
| tilacode 会话**永不销毁**，session_id 固定 `ses_task_{root_task_id}` | `task_bridge.py:603`；`tilacode_client.py:182`（同 id create_session 幂等复用） |
| 空闲会话收到 prompt 会**开新执行**（上下文连续） | `tilacode/packages/core/src/session/run-coordinator.ts:9`（"Starts execution while idle or joins the active execution"） |
| delivery 合法值 `steer \| queue`，节点当前硬编码 `steer` | `tilacode/packages/client/src/generated/types.ts:401`；`tilacode_client.py:268-287` |
| 现有 steer 对已完成任务 **noop**（要求任务在 `_current_tasks`，finally 已弹出） | `task_bridge.py:442-445`、`:385` |
| 轨迹上报端点**无任务状态前置**（仅校验任务存在） | `opc-central-dispatch-hub/app/api/v1/endpoints/tasks.py:597-634` |
| 轨迹采集器生命周期随任务（创建/finally 移除） | `task_bridge.py:310`、`:389-391` |

## 决策

| # | 决策 | 说明 |
|---|---|---|
| D1 | **空闲 @节点 = 节点续聊** | 中枢下行 `hub.node_chat` → 节点对 `ses_task_{root_task_id}` 发 `send_prompt(delivery="queue")` → 节点轮次结束后上行 `agent.node_chat_reply`（assistant 全文）→ 中枢落会话消息 → 前端会话流显示「节点回复」（节点徽标，延续 ADR-063 D6 来源标注） |
| D2 | **下线建任务路径** | `node-chat` 不再建任务、不走规划/派发；`start_node_chat` 废弃。要派新任务走正常「新建任务」流程，边界清晰 |
| D3 | **续聊完全绕开任务状态机** | 不进 `_current_tasks`、不触发无进度看门狗/完成判定/并发槽/任务状态变更；竞态安全：一律 `delivery="queue"`，发起瞬间若会话恰好活跃则天然入队不丢失 |
| D4 | **续聊过程实时展示（完整版一步到位）** | 续聊轮的思考/工具/正文实时进折叠块。**seq 接续机制**：中枢下行带 `trace_task_id`（任务链最后任务 id）+ `last_seq`（该任务当前 max seq）；节点为续聊轮**临时挂采集器**（归属 trace_task_id），seq 从 `last_seq+1` 续；上报复用 `POST /agent-trace`（`ON CONFLICT` 幂等兜底）；轮次结束采集器销毁 |
| D5 | **消息持久化** | 用户 @消息（steer 与 node-chat 两路）与节点回复都写入 `task_building_sessions.messages`；前端加载会话即恢复，刷新不丢 |

## 事件契约（新增，复用 api-standards 命名风格）

| 方向 | 事件 | payload | 说明 |
|---|---|---|---|
| 下行 | `hub.node_chat` | `task_id`（链上最后任务 id）、`root_task_id`、`node_id`、`text`、`reply_to`、`trace_task_id`、`last_seq` | 节点按 `root_task_id` 定位会话；`reply_to` 用于回复关联 |
| 上行 | `agent.node_chat_reply` | `reply_to`、`root_task_id`、`task_id`、`node_id`、`text`、`tokens`（可选） | assistant 全文；中枢落消息表 |
| 上行 | `agent.node_chat_error` | `reply_to`、`task_id`、`node_id`、`reason` | 会话丢失/投递失败 → 前端气泡报错（消息已落库不丢失） |

## 放弃的方案

| 方案 | 放弃原因 |
|---|---|
| 保留建任务语义 + 修 specification 复制 | 不是对话：节点不回话、走完整任务状态机，用户明确否决 |
| 中枢轮询/订阅轨迹拼装回复上会话流 | 异步拼接时序脆、实现绕；直接 `agent.node_chat_reply` 全文回传更干净 |
| 放开 steer 端点到空闲任务 | 节点 bridge 对非 current 任务直接 noop（`task_bridge.py:442-445`），且 steer 与续聊语义混用 |

## 后果

- **正面**：@空闲节点成为真对话（发一句回一句、上下文连续、可回看）；消息刷新不丢；规范文档拦截问题随建任务路径下线自然消失。
- **代价**：节点侧需支持「脱离任务状态机的会话续聊」与临时轨迹采集（seq 接续）；中枢需新增消息写入能力；三端联动改动。
- **风险**：①节点重启后 tilacode 会话可用性需实测（会话持久化在 tilacode 侧 storage，预期可恢复，M2 开发时验证）；②seq 接续起点错位 → 以中枢下发 `last_seq` 为准 + `ON CONFLICT` 幂等兜底；③续聊轮卡死无看门狗 → 节点侧为续聊轮设独立轻量超时（默认 300s，超时回 `agent.node_chat_error`）。

## 模块拆分（一次只做一个模块，逐个验收）

| 模块 | 内容 | 验收 |
|---|---|---|
| **M1** 中枢 | `node-chat` 端点改造（续聊转发 + 202；running 仍 409）+ `hub.node_chat` 下行 + `agent.node_chat_reply/error` 处理 + 消息写入方法（拆 `_persist_chat_exchange`）+ steer 用户消息落库 | 单测：端点分支/落库/事件 payload；真实链路前先 mock |
| **M2** 节点 | `hub.node_chat` 处理 + `send_prompt` delivery 参数化 + 续聊轮临时采集器（trace_task_id/last_seq 接续）+ 回复捕获回传 + 独立超时 + 错误回传 | 单测全过；对已完成任务的会话续发消息能收到回复 |
| **M3** 前端 | 发送分流（空闲→续聊新语义）+ 消息持久化调用 + 历史恢复 + 节点回复消息渲染（节点徽标）+ 错误提示 | vitest + tsc；浏览器实测：发消息→回复出现→刷新仍在 |
| **M4** 收尾 | 三端回归 + Runbook 更新（adr063 验收操作单 `@节点` 节修订）+ ADR-063 状态标注同步 + INDEX | 三端全绿；E2E 按 Runbook 手测 |

## 相关文档

- 前序：[ADR-063](./063-node-agent-trace-mirror.md)（D4 空闲分支被本 ADR 修订；轨迹通道/折叠块/steer 机制沿用）
- Spec / Harness / Changes：`docs/spec/064-node-chat-conversation-{spec,harness,changes}.md`
