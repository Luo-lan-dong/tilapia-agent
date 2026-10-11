# 压缩投影边界与摘要热前缀复用 Spec（P1）

> 关联：[ADR-054](../adr/054-compaction-boundary-and-prefix-reuse.md) · [Harness](./054-compaction-boundary-prefix-harness.md) · [Changes](./054-compaction-boundary-prefix-changes.md)
> 上游：[ADR-041](../adr/041-context-management-v1.md)（压缩与投影的原始设计）· [排查记录](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md)

## 1. 功能描述

修复中枢上下文压缩链路的两个缺陷：

1. **投影切点协议违规（缺陷 A，主因）**：压缩切点落在工具轮中间时，
   投影序列以孤儿 `tool` 消息开头，provider 返回 400 导致对话直接中断；
2. **摘要调用无法复用热前缀（缺陷 B，成本）**：摘要请求使用合成 system + 单条
   user 拼装文本，与正常请求无共享前缀，每次压缩全价重灌数万 token。

## 2. 用户故事

- **US-1（防中断）**：作为用户，我的长任务在自动压缩后必须能继续，不能因为
  历史里恰好在工具调用中间被压缩就报错停止（「任务跑不完就停」的根因）。
- **US-2（可审计）**：作为运维，我能从压缩点看出切点落在什么角色上
  （`cutoff_role`），并在压缩日志里看到是否走了前缀复用路径。
- **US-3（降本）**：作为运维，摘要调用的缓存命中率应显著高于现状（当前
  `spec_chat` 场景 2.1%），以降低长对话的压缩成本。
- **US-4（不回归）**：作为用户，压缩的「原文永不丢失、可回查」语义不变。

## 3. 核心流程

### 3.1 安全切点（缺陷 A）

```
落库时（compact_if_needed）：
  head, recent = split_for_compaction(messages, recent_keep)
  head 为空 → 不压缩（返回 None, 0）
  cutoff = safe_cutoff(messages, len(head))        ← 前移到工具轮之外
  cutoff == 0 → 不压缩（无内容可摘要）
  生成摘要 → 落 context_compaction = {summary_text, message_count: cutoff,
                                       cutoff_role: messages[cutoff].role, created_at}

投影时（project_messages，双保险）：
  cutoff = safe_cutoff(messages, message_count)
  projected = [checkpoint(user)] + messages[cutoff:]
  断言不变量：projected[0].role == "user"（checkpoint）
              projected[1] （若存在）role != "tool"
```

`safe_cutoff` 规则（纯函数）：

```
输入 messages, cutoff（先 clamp 到 [0, len(messages)]）
若 cutoff <= 0 或 cutoff >= len(messages) → 原样返回
若 messages[cutoff].role != "tool" → 原样返回
否则（切进了工具轮的应答区）：
    向前找最近的 j < cutoff 使 messages[j] 是带 tool_calls 的 assistant
    找到 → 返回 j（把整个工具轮划入 head）
    找不到（不应发生的脏数据）→ 返回 cutoff（交由 _build_messages 的孤儿过滤兜底）
```

**方向约束（必须）**：`safe_cutoff(messages, c) <= c`。前移保证校正后的 `cutoff`
单调不增，与 `_generate_summary` 的防重复判定（`previous.message_count >= len(head)`
→ 跳过）方向一致，不会出现「校正后判定重复 → 永不压缩」的死循环。

### 3.2 摘要热前缀复用（缺陷 B）

```
第一优先（真前缀）：
  messages = [真实 system prompt] + head(+recent)          ← 逐字重放
  messages += [{role: user, content: COMPACTION_INSTRUCTION(previous_summary)}]
  call: chat_completion_messages(messages, tools=本次请求的 tools)
  期望：provider 复用真实请求的 KV 前缀，仅尾部指令与输出不计入命中

第二优先（不带 tools 的前缀式）：
  同上，但 tools=None（兼容不支持 tools 的非流式调用）

第三优先（降级为 P1 之前的形态）：
  合成 system「你是对话检查点摘要器，只输出摘要正文。」+ 单条 user（build_summary_prompt(head, recent, previous)）
  任一优先级的返回为空/异常 → 进入下一优先级
  三档全失败 → 跳过本轮压缩（返回 None, 0），对话不中断
```

`COMPACTION_INSTRUCTION` 语义（对齐参考实现 `src/summarizer.ts:32-67`）：
说明「以上对话是待压缩的上下文，请输出检查点」+ 五段 Markdown 模板 + 规则
（保留精确标识符、不复述压缩过程、不调用任何工具）+ 旧摘要锚定段（若存在）。

## 4. 降级链路

| 异常场景 | 行为 |
|---------|------|
| `safe_cutoff` 前移后 cutoff 为 0 | 不压缩（无可摘要内容），对话继续 |
| `messages[cutoff]` 为 `tool` 但找不到父级 assistant（脏数据） | `safe_cutoff` 原样返回；投影由 `_build_messages` 的孤儿过滤兜底（既有能力） |
| 带 tools 的摘要调用 400/异常 | 回退为不带 tools 的前缀式调用（第二优先级） |
| 前缀式调用仍失败 | 回退为原独立调用形态（第三优先级） |
| 三档全失败 | 跳过本轮压缩（`None, 0`），下轮再试；对话不中断 |
| 摘要返回空串/纯空白 | 与失败同路径，进入下一优先级 |
| 存量脏压缩点（cutoff 落 `tool`） | 投影时 `safe_cutoff` 动态校正 → **新代码下也能正常打开**（虽不在本次数据修复范围，但不再 400） |

## 5. 数据结构

### context_compaction（JSONB，新增 `cutoff_role`）

```jsonc
{
  "summary_text": "## Objective\n...",
  "message_count": 47,          // 安全切点（已前移到工具轮之外）
  "cutoff_role": "assistant",   // 新增：切点处消息的角色，供审计
  "est_chars_per_token": 2.51,  // P0：校准密度
  "est_sample_at": "2026-09-27T...",
  "created_at": "2026-09-27T..."
}
```

## 6. 验收标准

- [ ] **切点安全**：构造「切点恰落在 `tool` 消息上」的历史，压缩后落库
      `message_count` 前移到该工具轮的 `assistant(tool_calls)` 之前；
- [ ] **投影合法**：同一场景下 `project_messages` 结果满足
      `projected[1].role != "tool"`（首条历史消息不是 tool）；
- [ ] **不中断**：该场景端到端跑一轮对话，事件序列无 `error`，以 `done` 收尾；
- [ ] **前移方向**：`safe_cutoff(m, c) <= c` 对任意输入成立（含边界 c=0 / c=len）；
- [ ] **存量脏点可开**：手工构造 `message_count` 落在 `tool` 上的会话，
      投影后不产生以 `tool` 开头的序列；
- [ ] **前缀复用**：摘要请求的 messages 首条为**真实 system prompt**、
      第二条起为被压缩区消息（逐字），末条为压缩指令；带 tools 时 payload 含 tools；
- [ ] **三级降级**：mock 第一优先级失败 → 走第二优先级；第二优先级失败 →
      走第三优先级；三档全失败 → `compacted: false` 且对话正常继续；
- [ ] **无迁移**：本次不含 Alembic 迁移；前端零改动。

## 7. 关联文档

- [ADR-054](../adr/054-compaction-boundary-and-prefix-reuse.md)
- [054-compaction-boundary-prefix-harness.md](./054-compaction-boundary-prefix-harness.md)
- [054-compaction-boundary-prefix-changes.md](./054-compaction-boundary-prefix-changes.md)
