# ADR-056: LLM 调用参数与窗口口径校正（P2）

## 状态

Accepted（含 1 项待用户确认的决策 D）

## 日期

2026-09-27

## 问题背景

[排查记录 2026-09-27](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md) §五记录了
三类「附带观测」，P0（[ADR-041 修订](./041-context-management-v1.md)）与
P1（[ADR-054](./054-compaction-boundary-and-prefix-reuse.md)）修完主因后，本 ADR 处理剩余项。

核对 [DeepSeek 官方 Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/) 与
[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/) 后，确认当前实现与模型真实能力存在
**五项口径偏差**：

| 编号 | 现状 | 官方事实 | 代码依据 | 危害 |
|------|------|---------|---------|------|
| a | 模型名 `deepseek-v4-flash` | 该名**已退役**，请求由 `DeepSeek-V4.1-Flash` 承接；推荐名 `deepseek-flash` | `llm_providers` 行（2026-09-27 05:58 创建） | 低（旧名仍接受），但无版本可预期性 |
| b | 代码硬编码 `max_tokens = 8192` | 该模型 **MAX OUTPUT = 384K** | `app/clients/deepseek.py:23,580` | **中高**：已实测 2 次 `completion_tokens == 8192` 触顶，长输出（三件套/收尾）可能被截断 |
| c | `llm_providers.context_window = 65536` | 该模型 **CONTEXT LENGTH = 1M** | 库内该行 | **中高**：压缩触发线按 64K 计算（`65536−8192−20000 = 37344`），把 93% 的窗口浪费掉——真实占用 3.7% 窗口即触发压缩 |
| d | 只发 `reasoning_effort`，**从不发 `thinking`**；`reasoning` 参数在编排链路恒为 `False` | 思考模式开关是 `thinking: {"type": "enabled"/"disabled"}`，`reasoning_effort` 只控强度（`low/high/max`）；**思考模式默认开启，默认 effort=high** | `app/clients/deepseek.py:585-587`、`orchestrator.py` 调用 `chat_completion_stream_raw` 未传 `reasoning` | **中**：开关从未显式声明，行为完全依赖 provider 默认值；实测 66 个会话返回过 `reasoning_content`，说明实际处于默认开启态 |
| e | 恒发 `temperature: 0.3` | **思考模式不支持 `temperature`**（设置了也不报错但无效）；`top_p` 仅在思考模式生效且下限 0.95 | `app/clients/deepseek.py:20,579` | 低（无效参数），但配置语义误导 |

### 由此暴露的一项**未爆发风险**（本 ADR 必须处理）

官方文档明确：**请求带 `tools` 时，`reasoning_content` 必须全量回传，否则 400**
（`guides/thinking_mode` §Tool Calls）。当前编排链路：

- `chat_completion_stream_raw` 收 `reasoning_delta` 后只 `yield` 给前端（`thinking` 事件），
  **不写入** `prompt_messages` 的 assistant 条目；
- 落库的 `thinking` 字段仅用于前端展示，`_build_messages` 回流时**剥离**（ADR-040 设计如此）。

**已核验**：近 4 天日志中 `orchestrator_deepseek_api_error` 全部为 tool 消息类 400（5 次，已由
ADR-054 修复）+ 鉴权/503 类，**没有** reasoning 未回传导致的 400。因此这是「风险」而非「现存故障」，
但它随 (d) 把 `thinking` 显式化后风险等级会上升，必须在同一决策内给出处置。

## 决策

### D1 · 模型名与窗口口径对齐官方（修 b/c）

- `llm_providers.context_window` 改为 **1000000**（1M），并把 `model` 改为 `deepseek-flash`。
  实施方式：**数据修正 + 迁移默认值更新**（新库 seed 与前端新建默认值同步），
  不改表结构；由用户在模型管理页可再编辑（ADR-030 能力不变）。
- 触发线随窗口自动变为 `1000000 − RESERVE − BUFFER`。为避免「窗口变大后永不需要压缩、
  单请求体积失控」，**输出预留与缓冲改为按窗口比例计算并设上下限**：
  - `COMPACTION_OUTPUT_RESERVE_TOKENS` 保持与 `max_tokens` 联动（见 D2）；
  - `COMPACTION_BUFFER_TOKENS` 改为 `max(20000, 窗口 × 5%)`，上限 100000。
- **不做**：不做「窗口多大就敢塞多大」——单请求体积仍受 provider 并发/延迟与成本约束，
  1M 只用于让压缩判定回到真实比例。

### D2 · `max_tokens` 分档 + 触顶告警（修 b）

- 新增配置 `llm_max_output_tokens`（默认 **32768**，`ge=1024`），替代硬编码 8192；
  保留 `MAX_OUTPUT_TOKENS` 常量作为**兜底默认值**（向后兼容），注释改为「默认档」。
- 分档（按调用场景传入，缺省用默认档）：
  | 场景 | 档次 | 理由 |
  |------|------|------|
  | orchestrator 主链路（流式对话） | 默认档 32768 | 长收尾/多工具轮回答 |
  | 三件套生成（spec_builder） | `min(默认档, 16384)` 或按配置 | 单文档直出，过长无益 |
  | 摘要（compaction） | 4096 | 五段摘要，固定上限 |
- **触顶告警**：`usage.completion_tokens >= max_tokens` 时记 WARNING
  `llm_output_truncated`（含 scene/model/prompt_tokens），使「被截断」从静默变为可观测。
- 明确**不做**：不直接调到 384K。超长输出会显著抬高超时与成本，且当前业务（编排对话/三件套）
  不需要；32768 已覆盖实测最长输出（8192 触顶场景）。

### D3 · 思考模式显式化 + 参数语义修正（修 d/e）

- `_build_payload` 改为按 `thinking` 显式控制：
  - `thinking=True` → `payload["thinking"] = {"type": "enabled"}` + `payload["reasoning_effort"] = <配置档>`；
  - `thinking=False` → `payload["thinking"] = {"type": "disabled"}`（**不发** `reasoning_effort`）。
- **思考模式下不下发 `temperature`**（官方明确无效）；非思考模式保留 `DEFAULT_TEMPERATURE`。
  同时把 `temperature` 提为可选参数（`None` = 不下发），供未来按场景配置。
- `reasoning_effort` 档位收敛为官方支持的 `low | high | max`（现值 `medium` 会被映射为
  `high`，语义模糊）：新增配置 `llm_reasoning_effort`（默认 `high`，枚举校验）。
- 编排主链路**是否开启思考**由 D4 决定（本决策只负责「显式声明」）。

### D4 · 编排链路的思考模式与 `reasoning_content` 回传 —— **已确认：路线 B**

与用户确认（2026-09-27）：**路线 B（保留思考 + 强制回传 `reasoning_content`）**。

落地口径（最小面，避免无谓体积）：

1. **持久化复用既有字段**：assistant 条目复用已有的 `thinking` 字段承载 `reasoning_content`
   （前端展示与协议回传是同一份文本，无需新增字段、无需迁移）。
   - `orchestrator` 在流式循环中把 `reasoning_delta` 累积进局部变量，写入
     `assistant_entry["thinking"]`（与既有 `_history_trajectory` 落库路径一致）；
   - 端点 `_persist_chat_exchange` 的既有「按回合附加 thinking」逻辑保持不变。
2. **回流条件化**：`_build_messages` 仅在**该请求带 tools** 时把 `thinking` 作为
   `reasoning_content` 回传（官方要求面）；不带 tools 的收尾轮不回传
   （官方明确「不带 tools 时即使传了也被忽略」），避免无谓体积。
3. **思考开关显式化**：新增配置 `orchestrator_thinking_enabled`（默认 **true**），
   编排主链路据此下发 `thinking: {"type": "enabled"|"disabled"}`；
   关闭时同时不下发 `reasoning_effort`。
4. **风险兜底**：若 provider 因 reasoning 缺失返回 400，`is_context_overflow_error`
   不会误判（无长度关键词），错误按现状 `error` 收尾；新增日志
   `llm_thinking_replay_missing`（回传为空但历史存在 assistant(tool_calls) 时提示），
   使该协议缺口可观测。

**若 D4 选路线 A（未采纳，记录备查）**：`thinking=False` 即可规避回传要求，无需持久化；
但前端「思考过程」卡片会恒为空，且实测 66 个会话已产出 `reasoning_content`，
关闭会使既有体验回退——故未采纳。

## 备选方案

| 方案 | 结论 |
|------|------|
| **D1~D3 + D4 择一（本决策）** | 采纳 |
| 不动窗口、只调 `max_tokens` | 否决。窗口口径错误是「压缩在 3.7% 就触发」的直接原因，只调输出上限解决不了 |
| 窗口直接设为 1M 且把 buffer 也线性放大 | 否决。缓冲按比例+上下限更稳；纯线性会把小窗口场景的缓冲压得过小 |
| `max_tokens` 直接调到 384K | 否决。成本/超时风险大，实测最长输出仅 8192，按需分档足够 |
| 保持隐式思考（什么都不发） | 否决。行为依赖 provider 默认值，模型换代即静默变化；且 `temperature` 无效参数会继续误导 |
| 用 `top_p` 替代 `temperature` 做随机性控制 | 暂缓。思考模式下 `top_p` 有效区间仅 0.95~1.0，可控空间极窄，收益不足以引入新参数 |

## 影响

### 中枢后端

- `app/clients/deepseek.py`：`_build_payload` 增 `thinking`/`max_tokens`/`temperature` 参数；
  `chat_completion_stream_raw` 增 `thinking` 透传；触顶告警。
- `app/core/config.py`：新增 `llm_max_output_tokens`、`llm_reasoning_effort`（枚举校验）。
- `app/services/context_compaction_service.py`：`COMPACTION_BUFFER_TOKENS` 改为按窗口计算；
  摘要调用使用 4096 档。
- `app/agents/spec_builder*.py`：三件套生成使用 16384 档（如启用分档）。
- D4 路线 B 时：`task_building_sessions.messages` 的 assistant 条目增 `reasoning_content`；
  `_build_messages` 回流带上；`orchestrator` 从 `reasoning_delta` 累积。

### 数据库

- 数据修正（非结构变更）：`llm_providers` 当前行的 `model` → `deepseek-flash`、`context_window` → 1000000。
  实施方式：Alembic 数据迁移（`UPDATE ... WHERE model = 'deepseek-v4-flash'`，幂等、可回滚）
  + 迁移默认值同步（新建行默认窗口）。
- D4 路线 B **无需新增字段**（复用既有 `thinking`），故**无需结构迁移**。

### 兼容性

- `thinking`/`max_tokens` 新增参数均有默认值，未显式传参的调用方行为按默认档变化
  （这是**有意的行为变更**：输出上限 8192 → 32768）；
- 旧模型名仍被 provider 接受，改名不破坏调用；
- `_build_messages` 新增 `reasoning_content` 回传仅作用于带 tools 的请求，且仅当该
  assistant 条目确有 `thinking`；旧会话（无 thinking）行为不变；
- 前端零改动（`thinking` 事件与字段语义不变）。

## 待确认事项

无（D4 已确认路线 B）。实施中若发现与官方文档不符之处，按 `.trae/rules/decision-documents.md`
「开发过程中发现需要偏离 ADR → 立即停止并更新 ADR」处理。

## 关联文档

- [排查记录 2026-09-27](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md)（§五 附带观测）
- [ADR-044](./044-llm-resilience-v1.md)（LLM 客户端韧性：重试/降级/cache 观测，本 ADR 与其互补）
- [ADR-041](./041-context-management-v1.md)（窗口元数据与压缩触发线）
- 官方依据：[Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)、
  [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、
  [Context Caching](https://api-docs.deepseek.com/guides/kv_cache)
- [056-llm-params-calibration-spec.md](../spec/056-llm-params-calibration-spec.md)
- [056-llm-params-calibration-harness.md](../spec/056-llm-params-calibration-harness.md)
- [056-llm-params-calibration-changes.md](../spec/056-llm-params-calibration-changes.md)

## 实际实施情况（2026-09-27）

### 决策落实

- **D1 窗口口径**：`compaction_buffer_tokens` 按 `max(20000, 窗口×5%)` 封顶 100000 实现；
  数据迁移已应用（`llm_providers` → `model=deepseek-flash / context_window=1000000`）；
  通用默认窗口取 **128000**（见下「偏离」第 1 条）。
- **D2 输出分档**：`llm_max_output_tokens`（默认 32768）替代硬编码 8192；
  `SUMMARY_MAX_OUTPUT_TOKENS=4096` 已接入摘要调用；`SPEC_BUILDER_MAX_OUTPUT_TOKENS=16384`
  常量就位（三件套链路为串行 Markdown 直出，其调用点接入该档列为遗留）；
  触顶告警 `llm_output_truncated` 覆盖流式与非流式两条路径。
- **D3 思考模式显式化**：`_build_payload` 参数矩阵按决策实现（思考模式不下发 `temperature`）；
  `llm_reasoning_effort` 收敛为 `low|high|max` 枚举校验。
- **D4 路线 B**：`reasoning_content` 回传按决策实现，但**分两处写入**（见下「偏离」第 2 条）。

### 实施中的两处偏离（均为稳健性加强，不改变决策语义）

1. **通用默认窗口取 128000 而非 1000000**（`LLMProviderConfig` / Schema / ORM 默认值）：
   `context_window` 是 ADR-030 的**多模型通用字段**，把通用默认设为某个具体模型的上限，
   会在新增小窗口模型时过度乐观；当前 deepseek 行由迁移精确设为 1M。
2. **`reasoning_content` 需在构造 `assistant_entry` 时同时写入**（决策原文只提到
   `_build_messages` 回流）：同一轮对话内的后续请求直接复用 `prompt_messages`
   （不重走 `_build_messages`），故 `thinking`（供下一条 HTTP 请求重建历史）与
   `reasoning_content`（供同轮续行）两处都要有。该遗漏由 UT-58 捕获。

### 验证

- **测试**：deepseek 客户端 26 项 + 压缩服务 46 项 + orchestrator 46 项，合计 **118 项全过**；
  改动文件 ruff check/format 全过（`app` 余 2 项为 `resource_sync_service.py` 存量）。
- **连带修复**：`chat_completion_stream_raw` 新增参数导致 6 处测试替身签名不同步
  （30 项用例 `TypeError`），已全部同步。
- **端到端（真实后端 + 真实 API）**：工具调用链路无 `error`；`window_tokens=1000000`
  （真实占用 0.7%）；`thinking` 增量 141 字符并落库于 `assistant(tool_calls)` 条目；
  `model=deepseek-flash`、无 `llm_output_truncated`；主链路缓存命中 98%（ADR-054 继续有效）。
- 详见 [Changes 登记记录](../spec/056-llm-params-calibration-changes.md)。

### 遗留

- 工具结果超长裁剪（排查记录 P2 第 8 项）未立项；
- `SPEC_BUILDER_MAX_OUTPUT_TOKENS` 已定义但三件套调用点尚未接入该档（当前走默认档）；
- 其他模型（如 `deepseek-v4-pro`）的窗口与最大输出需按官方口径在模型管理页维护。
