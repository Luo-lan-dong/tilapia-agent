# ADR-045: Orchestrator Steering v1（执行中插话：队列提升 + 配额重置）

## 状态

Accepted

## 日期

2026-09-23

## 问题背景

路线图（040-orchestrator-agent-loop-roadmap.md）P5：中枢对话为「请求-响应」串行模式——
AI 回复期间前端输入框禁用，用户发现方向错误只能等本轮结束再补充（多轮工具对话
可达 1-2 分钟）或刷新页面丢弃整轮。

tilacode 对标（探查档案章节二 Steering）：执行中用户再发消息，先落库为
`session_input`（不丢），在**下一个 Provider Turn 边界**提升进历史，且提升任何
新输入会**重置步数配额**——用户随时修正方向，agent 不会在错误路径上跑完配额。

现状约束（探查结论）：

1. OrchestratorService/Agent 为**请求级实例**（`Depends(get_async_session)`），
   跨请求插话需要会话级队列注册表（模块级，单进程部署下安全）；
2. 流结束持久化 `_persist_chat_exchange` 为**覆盖式**（前端回传历史 + 工具轨迹
   + 回复），插话消息若直接写库会被冲掉——需要端点协调插入位置；
3. 前端 `sending` 时输入框 `disabled`（TaskNewChatPanel/InputBar）。

## 决策

与用户确认（2026-09-23）：**完整 Steering（tilacode 对标）**。

1. **插话投递端点**：`POST /api/v1/tasks/build/chat/steer`
   （`{session_id, content}`）。会话无活跃流时返回 409——前端降级为普通发送。

2. **会话级队列注册表**（新增 `app/services/steering_service.py`，模块级）：
   - `open(session_id)`：chat_stream 开始时注册活跃标志；
   - `enqueue(session_id, content)` → bool：无活跃流返回 False；
   - `drain(session_id) → list[str]`：非阻塞取空队列（Provider Turn 边界消费）；
   - `close(session_id)`：流结束注销（残留消息兜底丢弃——正常路径已在收尾前
     drain 清空）。

3. **边界提升 + 配额重置**（`orchestrator.chat_stream`）：
   - 循环由 `for _round in range(max_rounds)` 改为 `while rounds_used < max_rounds`
     手动计数——**提升插话时 `rounds_used = 0`**（tilacode 步数配额重置语义）；
   - 提升检查点（Provider Turn 边界）：
     a. **工具轮执行完成后**（下一轮流式调用前）；
     b. **收尾判定时**（模型未调工具即将 done 前）——有排队插话则提升后
        继续下一轮而非结束，插话永不丢失；
     c. **doom loop / 达轮上限强制收尾前**——插话提升进收尾 prompt，模型
        收尾回答参考最新修正；
   - 提升动作：user 消息 append 进 `prompt_messages`（本轮后续 LLM 调用可见），
     yield 前端事件 `steering_applied`（data: `{content}`）与内部事件
     `_steering_messages`（data: `{messages: [...]}`，仿 ADR-040 轨迹事件）。

4. **持久化协调**（`task_building.py:_persist_chat_exchange`）：端点收集
   `_steering_messages`，插入顺序 = 前端回传历史 → 工具轨迹 → **插话 user 消息**
   → assistant 最终回复（OpenAI 协议允许 user 消息位于 assistant/tool 序列后，
   无 400 风险；审计顺序与提升时序一致）。

5. **前端**（零新协议，复用 SSE 通道）：
   - 回复中解禁输入框；发送时先调 steer 端点：
     - 成功 → 本地立即显示 user 气泡（`queued` 标记「已排队」），收到
       `steering_applied`（content 匹配）后清除标记；
     - 409（流已结束的竞态）→ 降级为普通 `sendMessage`；
   - 排队气泡由「待提升」状态图标区分于普通消息。

6. **明确不做（本次）**：跨进程/多实例队列（中枢单进程部署，模块级注册表
   足够）、插话撤回、排队消息编辑。

## 备选方案

| 方案 | 结论 |
|------|------|
| 轻量排队（前端排队、本轮结束自动续发，后端零改动） | 否决（用户确认选完整版）：插话本轮内 AI 感知不到，方向修正延迟一整轮 |
| 跳过 P5（场景不匹配论证） | 否决（用户确认）：多轮工具对话 1-2 分钟窗口内修正方向有真实价值 |
| 插话直接写 DB（task_building_sessions.messages） | 否决：与流结束覆盖式持久化冲突（冲掉/错位），且流进行中写库与端点层保存竞态 |
| 插话提升仅在工具轮边界（不含收尾前） | 否决：模型无工具调用直接回答时插话将随流 close 丢弃，违背「不丢」原则 |

## 影响

### 对中枢后端的影响

- 新增 `app/services/steering_service.py`（队列注册表）；
- `app/api/v1/endpoints/task_building.py`：`POST /chat/steer` 端点 +
  `_persist_chat_exchange` 插入 steered 消息；
- `app/agents/orchestrator.py`：`chat_stream` 循环改 while 手动计数、三处
  边界 drain、`steering_applied`/`_steering_messages` 事件、drain 时
  `rounds_used = 0`；
- 新事件：`steering_applied`（前端可见）、`_steering_messages`（内部）。

### 对前端的影响

- `api/taskBuilding.ts`：`steerSession()` API；
- `useTaskBuildingChat`：`steer()` 动作（409 降级）、`steering_applied` 事件
  处理（清除排队标记）、`steeringQueue` 状态；
- `TaskNewChatPanel`/`TaskNewInputBar`：sending 时解禁输入与发送（文案不变，
  行为分支为 steer）；排队气泡角标。

### 兼容性

- 不投插话时行为与现状完全一致（drain 空队列零开销）；
- `steering_applied` 为新事件，旧前端收到未知事件走 default 分支忽略。

## 关联文档

- [040-orchestrator-agent-loop-roadmap.md](../spec/040-orchestrator-agent-loop-roadmap.md)（P5 定义与 tilacode 探查章节二）
- [ADR-040](./040-orchestrator-agent-loop-v1.md)（多轮循环与轨迹事件模式，本决策复用）
- [ADR-043](./043-execution-guard-v1.md)（doom loop 强制收尾路径与插话提升的交互）
