# ADR-041: 中枢上下文管理 v1（窗口元数据 + token 计量占用展示 + 压缩与历史回查）

## 状态

Accepted（压缩投影边界与摘要热前缀复用见 [ADR-054](./054-compaction-boundary-and-prefix-reuse.md)；窗口口径与输出分档见 [ADR-056](./056-llm-params-calibration.md)）

## 日期

2026-09-21

## 问题背景

路线图 P1（缺陷 3）：中枢无任何 token 估算/压缩/溢出处理，DeepSeek 窗口 64K 撞到即
400 → error 事件结束。P0（ADR-040）工具轨迹回流后工具结果也进窗口，撞线更快。

用户同时提出两项新要求：

1. **实时显示上下文占用百分比**（对话面板圆环指示器）；
2. **压缩不能丢原文**：多轮压缩会累积失真/曲解原意，要求压缩摘要与原始历史都保留，
   正常情况用摘要，用户追问细节时模型能回查原文。

概念澄清（与用户对齐）：provider 窗口为「输入+输出共享」；输出上限由模型能力与
服务商硬限制决定（可调小不可调大）；「1M 上下文」指单次请求输入总量上限 100 万 token。
压缩后窗口中的原始消息被移出，"回查原文"必须通过工具实现（窗口有界、信息不丢）。

## 决策

与用户确认（2026-09-21）：

1. **窗口元数据**：`llm_providers` 新增 `context_window` 列（INTEGER，默认 65536），
   模型管理页可编辑——占用百分比与压缩触发线的分母按模型维护（ADR-030 一致）。
2. **token 计量（双轨）**：
   - 估算：`chars/4` 启发式（对齐 tilacode `Token.estimate`），用于压缩触发预判；
   - 真实：流式末帧 usage.prompt_tokens（ADR-040 已采集），用于占用展示。
3. **占用实时展示**：`chat_completion_stream_raw` 的 message 事件携带 usage；
   Orchestrator 每轮流结束后 yield `context_usage` SSE 事件
   `{used_tokens, window_tokens, percent}`；前端对话输入区上方渲染圆环。
4. **压缩（原文保留式）**：
   - `task_building_sessions` 新增 `context_compaction` JSONB 列存压缩点；
     **messages 列原始历史永不修改删除**；
   - 触发：a) 预压——组装 prompt 后估算超 `context_window − MAX_OUTPUT_TOKENS(8192)
     − COMPACTION_BUFFER_TOKENS(20000)`；b) 兜底——provider 400 且错误含
     context/length 关键词时压缩后重跑本轮（仅一次）；
   - 摘要：当前对话模型单次调用生成（max_tokens 4096），五段固定模板
     （Objective / Important Details / Work State / Next Move / Relevant Tasks & Nodes），
     尾部保留最近约 8000 token 作 recent，旧摘要锚定增量更新；
   - 投影：`_build_messages` 有压缩点时只投影压缩点之后的消息，头部注入 user 角色
     `<conversation-checkpoint>`（含摘要与回查提示）；
   - **历史回查技能 `history_recall`**（新内置技能，注册进 SKILL_REGISTRY）：参数
     `{keyword?, role?, limit?}`，按当前会话检索原始 messages（含工具轨迹，单条截断），
     模型按需取回原文，对冲多轮压缩累积失真。
5. **摘要模型**：当前对话模型（用户确认，无新配置项）。

明确不做：真实 tokenizer（chars/4 启发式足够）、独立消息表（原始历史保留在 messages
列已满足"原文不丢"）、按需注入式取回（用工具检索替代）。

## 备选方案

| 方案 | 结论 |
|------|------|
| A. messages 保留原文 + compaction 列 + history_recall 技能（本决策） | 采纳。无消息结构迁移、原文天然可查、与 tilacode「有界投影+工具取回」哲学一致 |
| B. 压缩时替换 messages 旧消息 | 否决。原文丢失，多轮压缩累积失真无对冲手段（用户明确否决） |
| C. 独立消息表 | 否决。需迁移+改前端恢复接口，JSONB 方案 A 已满足诉求 |
| D. 压缩全部历史（不留 recent） | 否决。最近上下文对连贯性最重要，tilacode 亦保留 recent |

## 影响

### 数据库

- Alembic 迁移一枚：`llm_providers.context_window`（INTEGER NOT NULL DEFAULT 65536）+
  `task_building_sessions.context_compaction`（JSONB NULL）。

### 中枢后端

- `app/clients/deepseek.py`：message 事件携带 usage。
- `app/services/context_compaction_service.py`（新增）：估算、触发判定、摘要生成、投影切分。
- `app/agents/orchestrator.py`：chat_stream 接入预压/兜底/context_usage 事件；
  `_build_messages` 支持压缩投影。
- `app/skills/history_recall.py`（新增）+ registry 注册。
- `app/models/llm_provider.py` / `task_building_session.py`：新列。
- 模型管理 API/Schemas：context_window 读写。

### 前端

- 对话页：context_usage 圆环指示器（输入区上方）。
- 模型管理页：context_window 编辑字段。
- SSE 事件类型扩展（context_usage）。

### 兼容性

- 无 context_compaction 的存量会话行为不变；无 context_window 的存量 provider 行为
  默认 64K 不变（迁移带默认值）。
- 前端不认识 context_usage 事件时忽略即可（SSE 事件按名分发）。

## 实际实施情况

- 全部按决策实施（2026-09-21 完成），无决策偏差。
- 联调发现并修复 3 个问题：
  1. `_serialize_message` 截断后才估算 token，长消息成本被低估 → 切分判定失效
     （head=0）；拆分为真实成本估算（不截断）与展示序列化（截断 2000 字符）两条路径；
  2. tests/conftest 清库与数据守卫恢复未解除 tasks↔task_building_sessions 双向外键
     （tasks.session_id 为 ADR-028 引入，守卫此前未覆盖）→ 清库解除关联 +
     恢复改为「剥离关联插入、事务内回填」；
  3. 前端 App.tsx 存在其他会话遗留的懒加载重构半成品（`<Suspense>` 未闭合）→
     补全闭合解除 typecheck 阻塞。
- 端到端验证：context_usage 每轮流结束实时推送（真实 usage 3262/65536=5.0%）；
  压缩未触发场景对话与轨迹回流回归正常；压缩闭环（overflow→强制压缩→checkpoint
  投影→重跑成功）由 UT-12 单测覆盖。
- 测试：压缩服务 9 项 + history_recall 6 项 + orchestrator 增补 4 项全过；
  前端 typecheck 通过、lint 仅存量 2 项问题。

## 修订 2026-09-27（P0：上下文计量与压缩触发）

### 状态

Accepted（修订已批准，实施中；改动明细见 [041-context-management-p0-changes.md](../spec/041-context-management-p0-changes.md)）

### 背景

用户实测反馈「多轮对话才 30% 多就触发压缩」「每发送一次对话都显示正在压缩」。
排查记录 [2026-09-27-上下文计量失真与压缩误触发](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md)
以真实 provider usage 对照给出量化结论：

1. `estimate_messages_tokens`（`len(json.dumps(payload, ensure_ascii=False)) / CHARS_PER_TOKEN`）
   相对 provider 真实 `prompt_tokens` **系统性高估 1.66 倍**（实测 33,168 vs 20,137；
   34,443 vs 20,692）。混合语料（工具回传以 JSON/英文为主）真实密度为
   2.48~2.54 chars/token，而固定常数 `CHARS_PER_TOKEN = 2` 按中文正文取保守值，
   叠加 `json.dumps` 的键名/引号/转义字符全部计入字数，形成净高估。
2. 触发线 `context_window − 8192 − 20000` 折算为真实 token 仅约窗口的 **34%**，
   与「30% 多就压缩」的现象一致；`recent_keep` 的 8000（估算单位）折算真实仅约
   4,800 token，尾部保留比设计意图少约 40%。
3. 触发判定读取的是**会话原始 `messages`**，而实际请求是投影后的 `prompt_messages`；
   压缩只写 `context_compaction` 不改 `messages`，判定输入因此永不回落 ——
   **一旦越线就永久停在线上**，每轮都判定要压缩。
4. `context_compacting` 事件位于 `compact_if_needed()` 调用**之前**且无任何条件，
   即使本轮 `compacted: false` 也照样推送 —— 即用户看到的「每次都在压缩」。

### 决策（修订原「决策 §2 双轨计量」「§4 触发」两条）

> 原决策 §2「估算：chars/4 启发式」与 §4-a「预压触发」按本修订执行；
> 其余条款（原文保留式压缩、checkpoint 投影、history_recall、摘要模型）不变。

**修订 1：估算密度从固定常数改为按真实 usage 自适应校准（替代原 chars/4 与 chars/2）**

- 新增纯函数 `calibrate_chars_per_token(measured_chars, real_prompt_tokens, previous)`：
  按单次请求实际字符数与 provider 真实 `prompt_tokens` 求观测密度
  `measured_chars / real_prompt_tokens`，经指数滑动平均（α=0.3）平滑，
  并夹在 `[1.5, 6.0]` 保守区间内；
- 噪声防护：仅当 `real_prompt_tokens ≥ 2000` 时参与校准（短请求密度失真）；
- 落点：会话级持久化在既有 `task_building_sessions.context_compaction` JSONB
  （新增 `est_chars_per_token`、`est_sample_at` 两个键），**不新增列、不写迁移**；
- 未校准时回退 `CHARS_PER_TOKEN = 2`（与现状等价，保证存量会话行为不变）；
- 参考基准：OpenAI 兼容接口建议的英文 `chars/4` 与本平台中文偏多的事实之间，
  由数据自行收敛，不再靠人工常数拍定。

**修订 2：压缩触发判定改用「实际发出的请求」而非原始历史**

- 判定输入改为当轮组装完成的 `prompt_messages`（已含 checkpoint 投影、环境增量、
  三件套 system 块），即 `.trae/rules` 意义上的「度量对象 = 真实请求体」；
- 由此压缩一旦生效，估算随之回落，消除「越线后永久判定要压缩」；
- 触发线公式（`窗口 − 输出预留 − 缓冲`）与 `recent_keep` 自适应规则不变，
  修正的是**计价密度**与**度量对象**，不是阈值策略。

**修订 3：压缩提示事件改为「确将推进」才下发**

- 新增纯函数 `should_compact_now(chat_messages, tools, context_window, system_text, compaction, chars_per_token, pressure_messages=None)`：
  越线判定用**实际请求**（`pressure_messages`，缺省回退 `chat_messages`），
  head 新增判定用**会话原始历史**（`chat_messages`）——后者必须与
  `_generate_summary` 的防重复条件（`previous.message_count >= len(head)`）
  **同源**，避免「事件不下发但摘要端推进」这类错配；
- `context_compacting` 仅在其为真时下发；`context_compacted` 始终下发并携带
  `compacted` 布尔，保证前端事件成对、语义可判；
- 配套语义（写入 Spec §3.2）：压缩生效后请求体被压回触发线以下属**期望稳态**，
  此时不再判定压缩也不提示，直到新内容重新把请求推过触发线。

### 明确不做（本修订范围内）

- **不做** 真实 tokenizer 依赖（校准已能收敛，引入 tokenizer 增加体积与版本耦合）；
- **不做** 压缩切点前移到工具轮边界之外 —— 该缺陷（压缩切进工具轮导致 provider 400
  中断对话）已同批定位，但属投影规则变更，列入 P1 单项决策，**不得**在本 P0 中顺带修改；
- **不做** 摘要调用热前缀复用、`max_tokens` 分档、`reasoning_effort` 下发、工具结果裁剪
  （P1/P2，见排查记录「六」）。

### 影响

- `app/services/context_compaction_service.py`：新增 `measure_payload_chars`、
  `calibrate_chars_per_token`、`estimate_chars_per_token`、`should_compact_now`
  与 4 个校准常量（`CALIBRATION_MIN_PROMPT_TOKENS` / `CALIBRATION_ALPHA` /
  `CALIBRATION_MIN_CHARS_PER_TOKEN` / `CALIBRATION_MAX_CHARS_PER_TOKEN`）；
  `estimate_tokens` / `estimate_messages_tokens` / `should_pre_compact` 增加密度参数
  （默认值保持向后兼容）；`CHARS_PER_TOKEN` 降级为冷启动种子；
- `app/agents/orchestrator.py`：判定改用定稿后的 `prompt_messages` + 校准密度；
  `context_compacting` 条件化；成功拿到真实 usage 后回写校准值；
  环境快照/增量抽为可重复调用的局部 `_finalize_prompt()`（压缩重投影后需再调用，
  否则重投影会丢弃环境增量）；新增 `_compact_and_reproject` 统一预压与 overflow 两条压缩路径；
- **无数据库迁移**（复用既有 JSONB 列）；
- 前端零改动（事件名与字段不变，仅下发时机与 `compacted` 语义更准）。

### 实际实施情况（2026-09-27）

- 三项修订全部按决策实施，无决策偏离；实施中发现并修正一处设计错配（见下）。
- **发现并修正**：`should_compact_now` 初版用投影后的请求同时做压力判定与 head 切分，
  而摘要端 `_generate_summary` 的防重复读的是原始历史 —— 两者不同源会产生错配。
  已改为双口径入参（`pressure_messages` 判越线 / `chat_messages` 判 head 新增），
  并补 UT-24b/UT-24c 两个守卫用例。
- **测试**：`tests/services/test_context_compaction.py` 27 项全过、
  `tests/agents/test_orchestrator.py` 40 项全过（含新增 4 项）；
  ruff check/format 通过；真实库与前端零改动验证见 Changes 登记。
- 遗留（按决策留给 P1/P2）：压缩切点未强制落在工具轮边界（400 中断根因）、
  摘要调用未复用热前缀、`max_tokens` 分档、`reasoning_effort` 下发、工具结果裁剪。

### 关联文档（修订）

- [排查记录](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md)
- [041-context-management-v1-spec.md](../spec/041-context-management-v1-spec.md)（§3.1/§3.2/§5/§7.1 同步修订）
- [041-context-management-v1-harness.md](../spec/041-context-management-v1-harness.md)（UT-18~27、IT-5/6、门禁增补）
- [041-context-management-p0-changes.md](../spec/041-context-management-p0-changes.md)（变更清单）

## 关联文档

- [040-orchestrator-agent-loop-roadmap.md](../spec/040-orchestrator-agent-loop-roadmap.md)（P1）
- [ADR-040](./040-orchestrator-agent-loop-v1.md)（轨迹回流，压缩投影的前置）
- [ADR-030](./030-llm-models-management.md)（多模型管理，窗口元数据挂靠）
- [排查记录 2026-09-27](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md)（P0 修订的根因依据）
