# 上下文管理 v1 Spec（窗口元数据 + 占用展示 + 压缩与历史回查）

> 关联：[ADR-041](../adr/041-context-management-v1.md) · [Harness](./041-context-management-v1-harness.md) · [Changes](./041-context-management-v1-changes.md)
> 路线图：[040-orchestrator-agent-loop-roadmap.md](./040-orchestrator-agent-loop-roadmap.md) P1
> **修订 2026-09-27（P0）**：[ADR-041 §修订](../adr/041-context-management-v1.md) 已把「估算密度」改为真实 usage 自适应校准、把「触发判定的度量对象」改为实际发出的请求、把 `context_compacting` 改为条件化下发。本 Spec §3.2 / §3.3 / §5 与 §7 已同步；改动明细见 [041-context-management-p0-changes.md](./041-context-management-p0-changes.md)。

## 1. 功能描述

为 Orchestrator 对话链路提供上下文管理能力：按模型维护窗口大小、每轮真实计量占用
并以 SSE 实时展示、长对话自动压缩（原文保留 + 摘要投影 + history_recall 技能回查）。

## 2. 用户故事

- 作为用户，我在对话中实时看到上下文占用百分比（圆环），接近上限时心里有数。
- 作为用户，我的超长对话不再突然报错结束——系统自动压缩后继续。
- 作为用户，我追问早期细节时，模型能通过历史回查取回原文，而不是被摘要"曲解"。
- 作为运维，我在模型管理页为每个模型维护真实窗口大小。

## 3. 核心流程

```
chat_stream 一轮开始
  │
  ▼
组装 prompt（_build_messages 压缩投影）
  │   context_compaction 存在？
  │     └─ 是 → [checkpoint(user): <conversation-checkpoint> 摘要 + 回查提示]
  │            + messages[compaction.message_count:] 之后的消息
  ▼
预压判定：估算(实际发出的 prompt_messages，含 system+投影后 messages+tools)/密度 > context_window − 8192 − 20000？
  │     └─ 是且本次 head 确有新增 → yield context_compacting → ContextCompactionService.compact_if_needed() → 重新投影
  │     └─ 是但 head 无新增（既有压缩点已覆盖）→ 不压缩、不下发 context_compacting（防每轮空转提示）
  ▼
LLM 流式调用（ADR-040）
  │     └─ 400 且错误含 context/length？→ compact() → 重跑本轮（仅一次）
  ▼
流结束（usage.prompt_tokens 已采集）
  │
  ├─ 校准：measured_chars / usage.prompt_tokens → 指数滑动平均 → 写回 context_compaction.est_chars_per_token
  ▼
yield context_usage {used_tokens, window_tokens, percent} → 前端圆环
```

### 3.1 触发判定的度量对象（P0 修订）

判定输入必须是**当轮实际要发出的 `prompt_messages`**（已含 checkpoint 投影、环境增量、
三件套 system 块），不得使用会话原始 `messages`。理由：压缩只改 `context_compaction`
不改 `messages`，用原始历史判定会导致压缩后估算不回落、越线后每轮重复判定（实测见
[排查记录](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md) §三）。

### 3.2 估算密度校准（P0 修订）

```
密度来源（无校准）      : CHARS_PER_TOKEN = 2（保守常数，存量行为等价）
每轮可校准条件          : 该轮 LLM 调用返回了 usage.prompt_tokens ≥ 2000
观测密度                : measured_chars / usage.prompt_tokens
                          measured_chars = 当轮请求体的规范化字符数（与估算同口径）
平滑                    : next = prev + 0.3 × (observed − prev)；prev 缺省取常数 2
夹取                    : 夹在 [1.5, 6.0]（防单次异常 usage 造成长期偏差）
落点                    : task_building_sessions.context_compaction.est_chars_per_token
                          同一 JSONB 列新增键，不新增列、不写迁移
回退                    : 会话无该键 / JSONB 为空 / session_id 为 None → 用常数 2
```

设计依据：混合语料（工具回传以 JSON/英文为主、正文以中文为主）的真实密度在实测中为
2.48~2.54 chars/token，与固定常数 2 相差 1.66 倍；密度由数据自行收敛，避免再次人工拍定。

**压缩后的稳态（重要语义）**：判定读的是实际请求（checkpoint + recent 尾部），压缩生效后
请求体通常被压回触发线以下，因此**后续若干轮不会再判定压缩、也不会提示「正在压缩」**；
直到新内容重新把请求推过触发线才再次压缩。这是期望行为——修复前判定读原始全量历史，
压缩后估算不回落，才导致越线后每轮都提示。

**双口径约束（实现要求）**：越线判定用**实际请求**（投影后的 `prompt_messages`），
head 新增判定必须用**会话原始历史**（`messages`）——后者必须与
`_generate_summary` 的防重复判定同源，否则「事件不下发但摘要端推进」或反向错配。

压缩执行（compact）：

```
确定切分点：keep≈8000 token 的尾部消息为 recent，其余为 head（含工具轨迹）
  → head 序列化为带前缀文本（工具结果截 2000 字符）
  → 当前对话模型单次调用生成五段摘要（锚定旧摘要）
  → 落 context_compaction = {summary_text, message_count, created_at}
  → messages 列不动（原文永久保留，history_recall 可查）
```

## 4. 摘要模板

```markdown
## Objective
## Important Details
## Work State
### Completed / ### Active / ### Blocked
## Next Move
## Relevant Tasks & Nodes

规则：保留精确的节点名、任务 ID、文件路径、命令、错误串、数字指标；
用简洁列表不用散文；不提及"上下文被压缩过"；各段即使为空也保留标题。
```

## 5. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 摘要生成失败（LLM 报错） | 跳过本轮压缩继续对话（下次再触发）；不中断对话 |
| 压缩后仍 overflow（第二次 400） | error 事件收尾（极端场景，理论上摘要后必然缩小） |
| usage 缺失（自定义 provider 不回 usage） | percent 用估算值显示；本轮**不参与密度校准**（保留上一次校准值） |
| usage.prompt_tokens < 2000（噪声样本） | 不参与校准（短请求密度失真），保留上一次校准值 |
| 会话无校准值 / session_id 为 None | 回退常数 `CHARS_PER_TOKEN = 2`，行为与修订前等价 |
| 观测密度越界（<1.5 或 >6.0） | 夹取到边界后参与平滑，防单次异常 usage 造成长期偏差 |
| 越过触发线但既有压缩点已覆盖本次 head | **不压缩、不下发 `context_compacting`**（消除每轮空转提示） |
| history_recall 无匹配 | 返回"未找到相关历史消息"摘要给模型 |
| context_window 未配置（存量行默认值兜底） | 迁移默认 65536，模型管理页可改 |

## 6. 数据结构

### context_compaction 列（JSONB）

```jsonc
{
  "summary_text": "## Objective\n...",   // 五段摘要全文
  "message_count": 42,                    // 压缩点：messages[:42] 已被摘要
  "created_at": "2026-09-21T12:00:00Z"
}
```

### context_usage SSE 事件

```jsonc
{"event": "context_usage", "data": {"used_tokens": 40120, "window_tokens": 65536, "percent": 61.2}}
```

### history_recall 技能

- name：`history_recall`；display_name：`历史回查`
- 参数：`{keyword?: string, role?: "user"|"assistant"|"tool", limit?: int=10}`
- 行为：按 SkillContext.session_id 查会话原始 messages，keyword 对 content 子串匹配
  （不区分大小写），role 过滤，命中消息按序返回（单条 content 截 500 字符，标注序号）；
  未传 keyword 返回最近 limit 条。

## 7. 验收标准

- [ ] 模型管理页可编辑 context_window，保存后占用百分比按新窗口计算；
- [ ] 对话每轮结束前端圆环更新（真实 usage 优先）；
- [ ] 构造超长对话触发预压：压缩后对话继续且摘要 checkpoint 进窗口、messages 列原文不变；
- [ ] 压缩后追问"之前提到的 XX 细节"：模型调用 history_recall 取回原文回答；
- [ ] mock overflow 400：自动压缩重跑，对话不报错中断；
- [ ] 存量会话（无压缩点）行为与现状一致（回归）。

### 7.1 P0 修订新增验收标准

- [ ] **校准收敛**：连续多轮真实调用后，`est_chars_per_token` 从 2.0 收敛到
      `[1.5, 6.0]` 区间内的实测密度；同一请求体的估算值同轮起落在真实
      `prompt_tokens` 的 ±15% 内（修订前为 +66%）；
- [ ] **校准生效位置**：压缩触发判定用的密度来自上一轮校准值（不是常数 2）；
- [ ] **度量对象正确**：压缩生效后，下一轮的触发判定输入为投影后的 `prompt_messages`，
      估算值随之回落；构造「越线 → 压缩 → 再判定」序列时，第二次判定不得再返回真；
- [ ] **事件不再空转**：既有压缩点已覆盖本次 head 时，本轮**不出现** `context_compacting`
      事件，但 `context_compacted`（`compacted: false`）仍成对下发；
- [ ] **零迁移**：本次变更不含 Alembic 迁移，`task_building_sessions` 列结构不变；
- [ ] **前端零改动**：`context_compacting` / `context_compacted` / `context_usage` 事件名与
      字段名不变，前端无需发版。
