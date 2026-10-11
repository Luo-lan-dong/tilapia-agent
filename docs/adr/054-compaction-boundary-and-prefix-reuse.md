# ADR-054: 压缩投影边界与摘要热前缀复用（P1）

## 状态

Accepted

## 日期

2026-09-27

## 问题背景

[排查记录 2026-09-27](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md) 定位的三项根因中，
P0（[ADR-041 修订 2026-09-27](./041-context-management-v1.md)）已修「计量高估」「判定读错对象」
「事件空转」三项。剩余两项同属压缩链路，构成本 ADR 的范围：

### 缺陷 A：压缩投影切点切进工具轮 → provider 400 中断对话（主因）

`context_compaction_service.project_messages` 以 `context_compaction.message_count`
为 `cutoff` **硬切**，不校验角色的协议约束：

```python
cutoff = max(0, min(cutoff, len(messages)))
projected = [build_checkpoint_message(...)] + messages[cutoff:]
```

当 `messages[cutoff]` 是 `role: "tool"` 时，该工具消息的父级
`assistant(tool_calls)` 落在 `cutoff` 之前被切掉，投影序列变成
`user(checkpoint) → tool(...)`，违反 OpenAI/DeepSeek 的 tool 协议，provider 返回：

```
400 Messages with role 'tool' must be a response to a preceding message with 'tool_calls'
```

`orchestrator.py` 捕获后直接 `error` + `done` 收尾，用户侧表现为「任务没跑完就不动了」。
库内统计：已启用压缩的 7 个会话中 **3 个**切点落在 `tool` 上（`83ca6bf0` cutoff=28、
`d317b48d` cutoff=49、`8af060e1` cutoff=9），且压缩点**持久化在会话行**，
一旦踩中该会话每次新请求都在首次 LLM 调用即 400。

参考实现 `借鉴/deepseek-harness-master` 明确要求「每个被压缩区间都从第一个
非 system 节点开始」，且「不能切分一个不可分割的单元（如单个工具调用对）」
（`packages/compaction/compaction-basic/README.md` §Automatic triggers、§Known Limitations）。

### 缺陷 B：摘要调用无法复用热前缀（成本）

`_generate_summary` 构造的是**独立请求**：

- system 用合成提示「你是对话检查点摘要器，只输出摘要正文。」（与实际请求的 system 不同）；
- 只发一条 user 消息，内容是 `head + recent` 的序列化文本（不是逐字消息序列）；
- `chat_completion_messages` 不支持传 `tools`。

因此摘要请求与正常请求**没有共享前缀**，每次压缩都要以全价重灌数万 token。
实测各场景缓存命中率：`orchestrator` 75.8%（其中 69 次为 0%）、
`spec_chat` **2.1%**、`ai_dispatch` 2.7%。

参考实现的做法（`packages/compaction/compaction-basic/README.md` §Design philosophy 第 3 条、
`src/summarizer.ts:25-31`）：把「system + 上次请求的 tools + 被压缩区消息」
**逐字节重放**，压缩指令作为最后一条 user 消息追加，使辅助调用成为上一次真实请求的
真前缀 —— 只有尾部指令与摘要输出不被缓存命中。其 `tests/request-cache.e2e.ts`
用真实 API 断言「第 2 次请求起必须命中前缀缓存」。

## 决策

### 决策 A：投影切点必须落在工具轮边界之外（cutoff 前移到安全边界）

1. 新增纯函数 `safe_cutoff(messages, cutoff)`：把切点前移到「不切分工具轮」的位置。
   规则：若 `messages[cutoff]` 为 `role: "tool"`，则向前找到包含该工具消息的
   `assistant(tool_calls)`（即它前面最近的带 `tool_calls` 的 assistant），
   把切点前移到该 assistant **之前**；否则原样返回。
2. `project_messages` 使用 `safe_cutoff` 的结果切分：投影序列首条历史消息
   **永不为 `tool`**。
3. 切点校正方向固定为**前移**（保留更多上下文进摘要），保证校正后的 `cutoff`
   只可能变小 —— 与 `_generate_summary` 的防重复判定（`previous.message_count >= len(head)`）
   保持单调一致，不会出现「校正后反而判定重复而不压缩」的死循环。
4. 压缩点落库时记录**校正后**的 `message_count`（而非原始 head 长度），
   使持久化值与投影行为自洽；同时写入 `cutoff_role`（校正后切点角色）便于事后审计。
5. `head` 因前移而变空时**不压缩**（沿用既有 `if not head: return None, 0`）。

### 决策 B：摘要调用改为「真前缀 + 尾部指令」两段式

1. `DeepSeekClient.chat_completion_messages` 增加可选 `tools` 参数（缺省不传，向后兼容）。
2. `_generate_summary` 改为：
   - 重放**真实 system prompt**（与正常请求同一份文本）+ 本次请求的 `tools`
     + 被压缩区消息序列（`head`，必要时含 `recent`）**逐字**；
   - 追加一条 user 消息承载压缩指令（含五段模板、规则、旧摘要锚定）。
     指令文本**不含摘要器 system 角色**，改为「对话已在上下文中，请输出检查点」的
     尾部指令语义（对齐参考实现 `COMPACTION_INSTRUCTION` 的定位）。
3. **降级链路**：带 `tools` 的摘要调用失败（自定义 provider 不支持 tools 的非流式调用、
   或返回空）时，**回退**为不带 tools 的前缀式调用；再失败则回退为 P1 之前的
   独立调用形态（合成 system + 单条 user），保证「压缩永远可用」这一底线不变。
4. 摘要调用的 model 与正常对话一致（不变，ADR-041 §5 决策保留）。

## 备选方案

| 方案 | 结论 |
|------|------|
| **A（本决策）** 前移切点 + 真前缀摘要 | **采纳**。切点前移是修正协议违规的最小改动，且天然与参考实现「区间从非 system 节点起、不切分不可分割单元」一致；摘要热前缀复用有参考实现与真实 API 测试支撑 |
| B. 切点**后移**到工具轮之后 | 否决。会把更多消息移出上下文（信息损失更大），且校正后 cutoff 变大，与 `_generate_summary` 防重复判定方向相反，易产生「校正后判定重复 → 永不压缩」的死循环 |
| C. 投影时丢弃孤儿 `tool` 消息（只过滤不校正） | 否决。`_build_messages` 已有孤儿过滤可兜底协议合法性，但过滤后**该工具调用的结果永久不可见**（模型看到问了没答），信息损失比前移更隐蔽；且 `message_count` 仍是脏值，防重复判定继续失真 |
| D. 用 `tool_choice="none"` 提升摘要质量 | 否决（本决策中）。摘要请求复用真实前缀时带 tools 是为了**缓存对齐**，不是为了让模型调用工具；加 `tool_choice` 会改变 payload 与真实请求不一致，反而破坏前缀一致性 |
| E. 摘要调用改为流式以复用 `chat_completion_stream_raw` | 否决。摘要不需要流式；流式的「首帧后不可重试」特性会让摘要失败率上升（ADR-044 约束） |
| F. 一次性批量修复存量脏压缩点 | 否决（用户决策 2026-09-27）。存量 3 个会话接受「打开即 400」，只保证新建会话正常；不写数据修复脚本，避免对生产会话行做不可逆写入 |

## 影响

### 中枢后端

- `app/services/context_compaction_service.py`：
  - 增 `safe_cutoff(messages, cutoff)`（纯函数）；
  - 改 `project_messages` 使用安全切点；
  - 增 `build_summary_instruction(previous_summary)`（尾部指令文本，替代原
    `_SUMMARY_TEMPLATE` 的 system 角色用法；`build_summary_prompt` 保留给降级路径）；
  - 改 `_generate_summary`：安全切点 + 前缀式调用 + 两级降级；
  - 改 `compact_if_needed`：落库 `message_count` 用校正后的切点，增 `cutoff_role`。
- `app/clients/deepseek.py`：`chat_completion_messages` 增可选 `tools` 参数。
- `app/agents/orchestrator.py`：`_compact_and_reproject` 的 `system_text` 语义收紧
  （必须是**真实** system prompt 文本，用于前缀重放；P0 已传入真实值，需在
  overflow 路径核对传入时机）。

### 数据库

**无迁移**（`context_compaction` 为 JSONB，新增 `cutoff_role` 键）。
**无存量数据修复**（决策 F）。

### 兼容性

- 存量压缩点：`message_count` 仍被读取，`safe_cutoff` 在校正**落库时**生效，
  投影时**也**重新校正一次（双保险，保证任何来源的脏值都不会产生协议非法投影）；
- `chat_completion_messages` 新参数为可选，存量调用方行为不变；
- 前端零改动（事件与字段不变）。

## 关联文档

- [排查记录 2026-09-27](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md)（缺陷 A/B 的根因与证据）
- [ADR-041](./041-context-management-v1.md)（本 ADR 修订其「压缩投影」条款）
- [054-compaction-boundary-prefix-spec.md](../spec/054-compaction-boundary-prefix-spec.md)
- [054-compaction-boundary-prefix-harness.md](../spec/054-compaction-boundary-prefix-harness.md)
- [054-compaction-boundary-prefix-changes.md](../spec/054-compaction-boundary-prefix-changes.md)
- 参考实现：`借鉴/deepseek-harness-master/packages/compaction/compaction-basic/README.md`、
  `src/summarizer.ts`、`tests/request-cache.e2e.ts`

## 实际实施情况（2026-09-27）

### 决策落实

- **决策 A（安全切点）**：`safe_cutoff` 按设计实现（clamp → 非 `tool` 原样返回 →
  向前找带 `tool_calls` 的 assistant → 找不到则原样返回）；`project_messages` 双保险校正；
  落库 `message_count` 用校正值并记录 `cutoff_role`（日志同步）。切点前移后为 0 时
  按设计跳过压缩（新增日志 `context_compaction_skipped_unsafe_cutoff`）。
- **决策 B（热前缀复用）**：`chat_completion_messages` 增可选 `tools`；
  `_generate_summary` 改为「真实 system + 被压缩区消息逐字 + 尾部指令」，
  三级降级实现在 `_summarize_with_fallback`，每档结果记日志（`tier=` 便于观测命中来源）。

### 实施中的两处偏离（均为稳健性加强，不改变决策语义）

1. **降级链新增 `TypeError` 分支**：自定义 OpenAI 兼容 provider 或历史替身客户端
   可能不接受 `tools` 关键字。若原样抛错会让「带 tools 档」直接导致压缩失败，
   因此捕获 `TypeError` 后跳到下一档（不带 tools 的前缀式），而非计入失败。
2. **IT-7 用例前提改为运行时实测**：初版按「每条等重」推算切点下标并放置工具轮，
   实测切点落在 `user` 上（工具轮三条消息的成本与单条 user 不同，推算不可靠）。
   改为先构造纯 user 基线、实测 `len(head)` 后再把工具轮放到该下标。

### 验证

- **反向验证**（证明新增测试确能抓住原缺陷）：把 `safe_cutoff` 临时改为恒等函数后，
  IT-7 与 IT-9 均失败（IT-9 断言 `projected[1]["role"] != "tool"` 实际为 `'tool'`）；
  恢复实现后两条均通过。
- **测试**：`tests/services/test_context_compaction.py` 44 项、`tests/agents/test_orchestrator.py` 42 项、
  `tests/clients/test_deepseek.py` 17 项；定向回归 7 个受影响模块合计 **123 项全过**。
- **静态检查**：改动文件 `ruff check` / `ruff format` 全过。
- **无数据库迁移**、**无存量数据修复**（决策 F）、**前端零改动**。

### 遗留

- 存量 3 个脏压缩点会话：按用户决策不做数据修复。其投影已被动态 `safe_cutoff` 校正，
  因此不会再因该脏值产生 400；但 `message_count` 值本身仍是旧的（不影响正确性）。
- P2 项（`max_tokens` 分档、`reasoning_effort` 下发、工具结果裁剪、窗口口径核对）未启动。

- 变更清单：[054-compaction-boundary-prefix-changes.md](../spec/054-compaction-boundary-prefix-changes.md)
- 索引登记：[INDEX.md](../INDEX.md)
