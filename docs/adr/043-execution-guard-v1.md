# ADR-043: Orchestrator 执行保护 v1（技能超时 + Doom Loop 检测）

## 状态

Accepted

## 日期

2026-09-23

## 问题背景

路线图（040-orchestrator-agent-loop-roadmap.md）P3 执行保护的两项剩余缺口（步数上限 + 末轮禁工具已随 ADR-040 落地）：

1. **技能执行无超时**：`orchestrator.py:_run_tool_call` 直接
   `async for event in skill.execute(...)` 消费技能事件流，无任何超时保护。
   技能内部卡死（LLM 调用悬挂、DB 死锁、外部服务无响应）时整轮对话悬挂：
   SSE 连接不断开、前端永远停留在「回答中」，只能重启中枢恢复。
   LLM HTTP 层虽有超时（`deepseek.py` DEFAULT_TIMEOUT_SECONDS），但技能是
   「LLM 调用 + DB 操作 + 多步组合」的复合执行体，单次 HTTP 超时不覆盖总时长。

2. **无 doom loop 检测**：模型陷入原地打转（连续多轮发出完全相同的技能调用，
   如反复 `find_idle_nodes({})`）时，没有任何机制提前止损——会烧满
   `orchestrator_max_tool_rounds`（默认 8 轮）才被三重禁用强制收尾，
   每轮都真实执行技能 + 调 LLM，纯浪费 token 与时间。

tilacode 对标（探查档案章节二/三）：

- **步数上限**：最后一步禁用所有工具 + 注入"这是最后一步"提示（中枢已由
  ADR-040 实现）；
- **doom loop**：连续相同工具调用触发权限询问，由人决定是否继续——中枢无
  权限层，需要自动化等价物；
- **工具超时**：`Effect.raceAll([exitCode, abort, timeout])` 超时强杀
  （默认 2 分钟）。

## 决策

与用户确认（2026-09-23）：

1. **技能执行超时：300 秒全局预算，超时中止但对话循环继续**。

   - 配置：`skill_execution_timeout_seconds`（Pydantic Settings，默认 300，
     生产安全网定位，只在真正死锁时触发）；
   - 实现：`_run_tool_call` 的技能消费循环用 `asyncio.timeout()`（Python 3.11）
     包裹——超时 `TimeoutError` 转为失败 `SkillResult`（summary 注明
     「技能执行超时（300 秒），已中止」），`step_finished` 照常下发
     （status=failed），并按协议回传 tool 消息（每个 tool_call 必须有响应，
     防下一轮 provider 400），对话循环继续——模型可重试或换思路；
   - 中止后对共享 `AsyncSession` 执行 `rollback()`，丢弃技能未提交的事务
     残留，防止脏状态污染后续轮次。

2. **Doom Loop 检测：连续相同调用提前强制收尾**。

   - 配置：`orchestrator_doom_loop_threshold`（默认 3，即连续 3 轮相同才触发，
     保留 1 次重试余量）；
   - 判定：每轮 `tool_calls` 规范化为签名（`[{name, arguments}]` 经
     `json.loads` → `json.dumps(sort_keys=True)`，参数顺序无关），维护
     最近签名队列，**连续 threshold 轮签名完全相同**即命中
     （窗口滑动，交替重复 A,B,A,B 不误伤）；
   - 处理：命中轮的工具调用**不再执行**——为每个 tool_call 补一条
     tool 消息（content=「检测到连续重复调用，本次未执行」）进 prompt 与
     轨迹（协议合法性 + 落库审计），向前端下发 `warning` 事件
     （「检测到重复的工具调用，已停止执行」），随后注入 doom 专用收尾提示
     （告知模型重复调用无效、基于已有结果总结作答）并走不带 tools 的
     流式收尾（复用 `_stream_plain_answer`），本轮对话结束。

3. **明确不做（本次）**：

   - 权限 allow/ask/deny 体系（中枢技能为白名单内置技能，无第三方扩展面）；
   - 工具输出双轨制/截断落盘（中枢技能返回 summary 已限长，场景不同）；
   - 跨技能交替重复（A,B,A,B）检测——误伤合法交替查询，仅处理严格连续相同。

## 备选方案

| 方案 | 结论 |
|------|------|
| doom 仅告警不中断 | 否决（用户确认）：token 照烧，告警无止损价值 |
| doom 按会话累计相同调用次数判定 | 否决：正常场景存在合法重复（如隔轮查询节点负载，结果会变化），累计计数误伤 |
| 技能超时默认 120s / 60s | 否决（用户选择 300s）：中枢技能多为 LLM+DB 复合操作，120s 有误杀慢技能（三件套长文档生成）风险；300s 作为死锁兜底更稳妥 |
| 技能超时整轮终止报错 | 否决（用户确认）：体验差，已有结果全部丢弃 |
| 每技能自声明 timeout 字段 | 否决：过度设计，全局预算够用；个别慢技能可后续按需加白名单 |
| `asyncio.wait_for` 包单次事件 | 否决：async generator 逐事件 wait_for 复杂且取消语义不清；`asyncio.timeout()` 上下文（3.11 原生）语义干净 |

## 影响

### 对中枢后端的影响

- `app/core/config.py`：新增 `skill_execution_timeout_seconds`（默认 300）、
  `orchestrator_doom_loop_threshold`（默认 3）；
- `app/agents/orchestrator.py`：
  - `_run_tool_call`：技能消费循环包 `asyncio.timeout()`，超时转失败结果 +
    tool 消息回传；调用方（chat_stream）捕获超时标记做 rollback；
  - `chat_stream` 工具轮：签名计算 + doom 判定 + 命中分支（补 tool 消息、
    warning 事件、doom 提示收尾）。
- 对外协议：新增 1 个前端可见事件——`warning`（已有协议，ADR-039 引入，
  复用非致命提示通道），`step_finished`/`done` 语义不变。

### 对前端的影响

- 零改动：`warning` 事件已有处理（antdMessage.warning 弹示），
  `step_finished` failed 状态已有渲染（红色失败标记）。

### 兼容性

- 默认参数下正常对话无感（技能均在秒级完成，无连续相同调用）；
- doom 判定要求「连续 3 轮严格相同」，交替/间隔重复不触发；
- 超时 300s 远大于技能正常耗时，仅死锁场景触发。

## 关联文档

- [040-orchestrator-agent-loop-roadmap.md](../spec/040-orchestrator-agent-loop-roadmap.md)（P3 缺口定义）
- [ADR-040](./040-orchestrator-agent-loop-v1.md)（多轮循环 + 末轮禁工具，本决策为其执行保护补全）
- [ADR-033](./033-ai-skills-and-smart-dispatch.md)（技能框架，超时作用对象）
- tilacode `session/processor.ts`（doom loop 权限询问对标）、`shell.ts`（超时强杀对标）
