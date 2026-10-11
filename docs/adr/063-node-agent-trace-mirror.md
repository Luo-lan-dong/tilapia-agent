# ADR-063: 节点 Agent 会话轨迹镜像（群聊式折叠块 + @节点对话）

## 状态

Accepted（**用户已确认三项决策**：方案②会话视图 / 只读旁观 / 整体落库）

> **2026-10-05 修订**：D4 空闲分支已被 [ADR-064](./064-node-chat-conversation.md) 修订为「节点续聊对话」，建任务路径下线；其余决策与执行中 steer 分支不变。

**实施进度（2026-10-02）**：M1（通道与契约）、M2（节点采集：reasoning/text/tool 分类 + seq/批量/重试/背压/脱敏/截断）、M3（前端群聊式折叠块 + after_seq 增量拉取 + 跟随/暂停 + 回看）、M4（`@节点` 双语义：执行中 → steer / 空闲 → node-chat 开新一轮）、M5（Runbook/回归收尾）**全部完成**，三端回归全绿。遗留：真实环境 E2E 手测「@ 后节点侧收到」（Runbook §3）。逐模块登记见 `docs/spec/063-node-agent-trace-mirror-changes.md`。

## 日期

2026-10-01

## 问题背景

中枢把任务派给节点后，中枢会话里**看不到节点 agent 在做什么、怎么想的**：

- 现有能看到的只有**进度级摘要**（节点 `on_progress` 上报的 `stage/message/tool/detail` + todos + 用量），渲染在右侧「最近动作 / 用量统计点」；
- 节点 agent 的**思考（reasoning）、正文、工具调用与输出**从未离开节点，中枢既看不到也没落库；
- 用户当前的替代做法是**直接看节点那边的 CLI 画面**（tilacode TUI），这要求用户自己开终端登节点，且无法回看。

用户诉求（原话要点）：

> 「中枢分配到相应的节点进行开发之后，把相应的节点给拉到这个中枢的会话里面，然后可以在这个会话里面看到对应节点的会话内容输出。默认折叠，可以展开查看当前他在做什么，就跟实时查看一样。」
> 「多节点时每个节点一个折叠块，就像群聊一样，每一个节点都是一个员工，都要在这个群里汇报工作进度。」
> 「不一定是 CLI 风格，我设计这个的初衷就是知道他在干嘛、在做什么、他是怎么思考的。」

## 已定决策（用户逐条裁决）

| # | 决策 | 说明 |
|---|------|------|
| D1 | **方案②：结构化会话视图**（否决逐字节终端镜像） | 不把 tilacode TUI 的终端字节流搬到中枢（那需要把节点执行方式从 `serve` 改成 PTY+TUI，会牵动事件消费/steer/权限/完成判定整条链路，且回看只能存录像、分辨率重排会错乱）。改为把**会话轨迹**结构化上报，由中枢渲染成可读视图。样式不追求 CLI 复刻，目标是"看懂他在干什么、怎么想的" |
| D2 | **只读旁观** | 折叠块内不可输入、不可按键；要说话走 `@节点`（D4） |
| D3 | **整体落库** | 轨迹按行落库（**只存视图行，不存终端字节**），任务结束后仍可回看 |
| D4 | **`@节点` 两种语义都要** | 该节点**正在跑** → 作为 steer 补话（不中断，复用 ADR-061 M5a 通道）；该节点**空闲** → 在中枢里向它开新一轮对话（**已被 [ADR-064](./064-node-chat-conversation.md) 修订为节点续聊对话，建任务路径下线**） |
| D5 | **多节点 = 群聊式** | 每个节点一个折叠块，块头一行显示「节点名 · 任务名 · 当前动作 · 进度」；默认折叠 |
| D6 | **来源必须标注** | 中枢编排 agent 与节点 agent 的输出在会话里**明确区分来源**，不能混为一谈 |

## 设计

### 数据流（单向，只读）

```
节点 tilacode SSE 事件 ──► 节点 EventTranslator（已有）
                              │  （新增：轨迹分类 + 截断 + 脱敏 + 批量）
                              ▼
                    POST /api/v1/tasks/{id}/agent-trace（批量、幂等）
                              ▼
                   中枢 task_agent_trace（落库，按 seq 有序）
                              ▼
        任务 SSE 新增事件 node_trace ──► 前端 NodeTraceBlock（折叠块，实时追加）
                              ▼
                    GET .../agent-trace?after_seq=  （回看 / 断线补拉）
```

### 轨迹行（`kind` 取值与语义）

| kind | 内容 | 来源 |
|---|---|---|
| `status` | 轮次开始/结束、阶段、当前动作、模型、工作目录、上下文用量 | 节点既有进度/用量上报（不重复造） |
| `reasoning` | 节点 agent 的思考片段 | tilacode 会话 part |
| `text` | 节点 agent 的正文片段（交付说明、结论） | 同上 |
| `tool` | 工具名 + 入参摘要 + 结果摘要 + 退出码 | 同上 |
| `todo` | 待办列表快照（变化时） | 节点 todo 上报（已有） |
| `usage` | 单轮 token/费用增量 | ADR-061 M5a 用量通道（已有，这里只引用） |

### 中枢侧改动

- **新表 `task_agent_trace`**：`id` / `task_id`(FK) / `node_id` / `session_id` / `seq`(任务内单调) / `kind` / `payload`(JSON 文本) / `created_at`；唯一约束 `(task_id, seq)` 保证幂等；索引 `(task_id, seq)`。
  - 体积红线：单行 payload 截断上限（建议 8 KB，超出保留头尾并标注 `truncated`）；每任务行数上限（建议 5000，超出只保留 `status`/`tool` 行并标记归档）。
- **入口** `POST /v1/tasks/{task_id}/agent-trace`：批量数组；`(task_id, seq)` 重复直接忽略；越界/未知 kind 丢弃并审计。
- **查询** `GET /v1/tasks/{task_id}/agent-trace?node_id=&after_seq=&limit=`：回看与断线补拉。
- **实时** 任务 SSE 流新增事件类型 `node_trace`（携带 node_id + 轨迹行），复用现有任务事件流，不新开连接。
- **`@节点`**：正在跑 → 复用 `POST /v1/tasks/{id}/steer`（已有）；空闲 → 新增 `POST /v1/tasks/{id}/node-chat`（指定 node_id + 文本，作为该节点的新一轮派发）。

### 节点侧改动

- `EventTranslator` 扩展：把会话 part 分类为 `reasoning`/`text`/`tool`，生成 `seq`，**批量**（建议 32 行或 1s 触发）上报，带重试与背压上限（队列满丢弃并降级为 `status` 行）。
- **脱敏**：apiKey/token/`.env` 内容与 `sk-` 形态串统一替换为 `***`；截断保留头尾。
- 复用既有 `hub_client` 上报通道；**不改**执行方式（仍是 `serve` + SSE 消费）。

### 实时推送的实现选择（2026-10-01 侦察结论，M1b 按此实现）

中枢**没有通用事件总线**（`grep "def publish|def broadcast|EventBus"` 在 `app/` 下仅命中日志模块 `core/logging.py:157` 的 `emit`）；前端现有任务流是 `app/api/v1/endpoints/task_building.py:113` 的 `StreamingResponse` + `service.chat_stream(...)` 异步生成器。因此：

- **M1 采用「按 `after_seq` 增量拉取」而非事件总线**：前端在折叠块**展开时**每 ~1s 调 `GET /api/v1/tasks/{id}/agent-trace?after_seq=N`，**收起即停**；进入会话时先做一次回看加载。
- SSE 事件 `node_trace` 降级为**可选优化**（将来若要接，接在 `chat_stream` 生成器里即可），**不阻塞 M1 验收**。
- 收益：少一条跨服务发布链路；节点掉线/浏览器断流后靠 `after_seq` 续拉即自愈；回看与实时共用同一个端点。

### 前端改动

- 新增 `NodeTraceBlock`：折叠块（默认折叠），块头 =「节点名 · 任务名 · 当前动作 · 进度」；展开后按轨迹行渲染（思考/正文/工具调用/待办/用量），**只读**。
- 会话流按「群聊」组织：中枢编排 agent 与各节点块并列，来源徽标区分（D6）。
- 实时追加 + **自动跟随最新**；用户手动上滚 → 暂停跟随并提示「有新输出」。
- 回看：进入会话时按 `after_seq` 分批加载；节点离线也能看（D3）。

### 暂定口径（待用户确认；**不阻塞 M1/M2**）

| 项 | 暂定默认 | 为什么先定 |
|---|---|---|
| 折叠块展开后默认显示量 | **最近 200 行 + 顶部「加载更早」按钮**（按 `after_seq` 反向分批补齐） | 长任务轨迹可达数千行；一次性全渲染会卡住会话流。用户随时可加载全部 |
| `@节点` 空闲开新一轮 | **在该节点同一工作目录开新会话**（不复用原会话上下文） | 与现有「任务链复用工作目录」一致；避免把上一轮上下文强加进新话题。若用户要"接着聊"，改为对原会话发 steer 即可，无需新通道 |
| 轨迹行的展示顺序 | 严格按 `seq` 升序（时间线），不做分组重排 | 与"实时旁观"一致，避免用户误判先后 |

### 红线（不做 / 不影响）

1. **不影响现有进度上报与完成判定**：轨迹是旁路通道，失败不得影响任务执行（上报失败只告警）。
2. **只读**：不新增任何节点侧能力或权限；折叠块不提供输入。
3. **不搬终端字节**：不引入 PTY/TUI 镜像。
4. **不落敏感信息**：凭据、环境变量、`.env` 内容一律脱敏。

## 放弃的方案

| 方案 | 放弃原因 |
|---|---|
| ① 逐字节终端镜像（PTY + xterm） | 需把节点执行方式从 `serve` 改为 TUI/PTY，牵动事件消费、steer、权限应答、完成判定整条链路；回看只能存录像且重排错乱；跨平台一致性差 |
| 复用中枢已有 PTY 终端（`terminal_service.py` + `/v1/terminal/ws`）直接显示节点终端 | 该终端是**中枢本机**的 PTY，不是节点上的会话；把节点会话搬进来仍需节点侧采集（本 ADR 的 M1/M2） |
| 只在展开时按需向节点拉取快照 | 节点离线即看不到、无回看、实时性差，与 D3 冲突 |

## 后果

- **正面**：用户不必登节点即可看懂"他在干什么、怎么想的"；可回看、可搜索、可跨节点对比；为后续"多节点协作/指挥"提供数据面。
- **代价**：新增一条节点→中枢的数据通道与一张表，需要认真处理**体积、脱敏、幂等、顺序**四件事；前端会话流复杂度上升（多来源、折叠、跟随）。
- **风险**：轨迹上报若阻塞主链路会拖慢任务 → 因此定为**旁路 + 失败只告警**；落库若失控会撑大数据库 → 因此定了截断与行数上限。

## 模块拆分（一次只做一个模块，逐个验收）

| 模块 | 内容 | 验收（可执行） |
|---|---|---|
| **M1** 通道与契约 | 中枢：迁移 `task_agent_trace` + ingress + 查询 + SSE `node_trace`；节点：最小上报（仅 `status` 行打通） | 迁移在库生效；真实派发一个任务后 `select count(*) from task_agent_trace` > 0；`GET .../agent-trace` 能按 seq 取回；重复 POST 不产生重复行 |
| **M2** 节点采集 | `EventTranslator` 扩展 reasoning/text/tool + seq + 批量 + 重试 + 背压 + 截断 + 脱敏 | 节点单测全过；真实任务中能看到 `reasoning`/`text`/`tool` 三类行；构造含 `sk-` 与 `.env` 内容的用例断言已脱敏；上报失败不影响任务完成 |
| **M3** 前端折叠块 | `NodeTraceBlock` + 群聊式会话布局 + 默认折叠 + 实时追加 + 跟随/暂停 + 回看加载 + 来源标注 | vitest 用例（折叠默认态、展开渲染、跟随暂停、来源标注）；手测：派发后块头显示当前动作，展开可见思考与工具输出 |
| **M4** `@节点` | 会话流 `@` 选节点；跑着 → steer（复用 M5a）；空闲 → `node-chat` 新一轮；状态与失败提示 | 单测：跑着 @ → 调用 steer 且任务状态不变；空闲 @ → 触发新一轮派发；E2E：@ 后节点侧确实收到 |
| **M5** 收尾 | Runbook 章节、结果记录表、INDEX、回归（节点/中枢/前端） | 三端回归全绿；Runbook 新增验收项可复制执行 |

## M2 可行性证据（2026-10-01 已只读核实）

「他是怎么思考的」有现成数据源，**M2 不需要改 tilacode、也不需要另走取数端点**：

| 事实 | 证据（文件:行） |
|---|---|
| 节点事件流里**有**思考事件三类 | `tilacode/packages/core/src/session/message-updater.ts:343`（`session.next.reasoning.started`）、`:358`（`...reasoning.delta`）、`:364`（`...reasoning.ended`） |
| 事件由 LLM 发布层产出 | `tilacode/packages/core/src/session/runner/publish-llm-event.ts:268-289`（`reasoning-start`/`-delta`/`-end` → 事件） |
| 会话 part 里有思考正文 | `tilacode/packages/core/src/session/compaction.ts:103` 读 `part.type === "reasoning"` 的 `part.text` |
| 节点侧当前**只把它当用量数字**（内容被丢弃） | `opc-agent-programming/app/executor/event_translator.py:431`（`tokens.get("reasoning")`） |

结论：M2 = 在节点翻译器里把 `reasoning.*` / 正文 / 工具事件映射为轨迹行（`kind=reasoning/text/tool`）+ `seq` + 批量 + 截断 + 脱敏。旁路通道，不改执行方式。

## M4 实现路径（2026-10-01 已只读核实）

「`@节点` 空闲也能开新一轮」**不需要新建通道**，复用中枢既有链路即可：

| 分支 | 实现路径 | 证据 |
|---|---|---|
| 节点**正在跑** | 复用 `TaskService.steer_task()`（明确不改任务状态） | `opc-central-dispatch-hub/app/services/task_service.py:986`（ADR-061 M5a） |
| 节点**空闲** | **建链上后续任务**（同 project_key / 同工作目录）+ 定向派发到该 `node_id`，用户文本作为该轮需求 | 同文件 `:110`（`create_task`）、`:1061`（`dispatch_task`） |

所以 M4 的净增工作量只有两处：端点 `POST /v1/tasks/{id}/node-chat` 的**分支判定**（任务是否 running / 该节点是否仍有活跃会话），以及前端 `@` 选节点 + 来源标注。

## 相关文档

- Spec：[063-node-agent-trace-mirror-spec.md](../spec/063-node-agent-trace-mirror-spec.md)
- Harness：[063-node-agent-trace-mirror-harness.md](../spec/063-node-agent-trace-mirror-harness.md)
- 实施范围与逐模块登记：[063-node-agent-trace-mirror-changes.md](../spec/063-node-agent-trace-mirror-changes.md)
- 验收操作单：[adr063-acceptance-runbook.md](../ops/adr063-acceptance-runbook.md)
- 相关前序：ADR-055（执行层加固）、ADR-061（M5a 用量与 steer、M5c 只读洞察）
