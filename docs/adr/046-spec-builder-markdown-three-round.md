# ADR-046 三件套生成协议重构：对话判定 + 三文档串行直出 Markdown

| 项目 | 内容 |
|------|------|
| 状态 | Accepted |
| 日期 | 2026-09-23 |
| 关联 | ADR-010（任务创建 Spec Chat）、ADR-033（技能体系）、ADR-028（流式 UI 增强）、ADR-040（真流式经验） |
| 变更清单 | [046-spec-builder-markdown-three-round-changes.md](../spec/046-spec-builder-markdown-three-round-changes.md) |

## 1. 问题背景

三件套（spec/tasks/checklist）自 ADR-010 起采用 **DeepSeek JSON Mode 单次调用**：模型在一个 JSON 对象的字符串字段里输出 `ai_reply` 与三份完整 Markdown 文档。实践暴露出结构性缺陷，导致**偶发性生成失败**（解析失败 → 重试一次 → 仍失败 → error）：

1. **转义地狱**：Markdown 全文（引号/换行/反斜杠）必须 JSON 转义，文档越长出错概率越高；
2. **截断即整包报废**：回复 + 三份文档 + 转义开销集中在一次输出中，被 max_tokens 截断后整个 JSON 无法解析（历史曾发生 4K 默认截断，靠调大 max_tokens 缓解）；
3. **伪流式放大失败感**：必须等完整 JSON 收完才能解析，用户盯着"正在编写规范文档…"等待很久后可能一场空。

## 2. 决策

将"一次 JSON Mode 调用生成全部内容"重构为 **"对话判定 + 三文档串行直出"** 四轮编排：

```
轮 1（对话判定轮）──── 信息不足 → 追问文本 → status{ready:false} → 结束
  │                    信息足够 → 过渡语 → 进入文档轮
  ▼
轮 2（spec）─────── 纯 Markdown 直出，真流式 specification_delta
  ▼
轮 3（tasks）────── 携带 spec 上下文，同上
  ▼
轮 4（checklist）── 携带 spec + tasks 上下文，同上
  ▼
全量 specification + status{ready:true} + done
```

### 核心规则

1. **轮 1 对话判定**：不启用 JSON Mode。提示词要求模型在输出末尾给出 `<decision>need_info|ready</decision>` 标签；正文收完后解析标签，剥离标签后伪流式下发 `chunk`。**标签缺失兜底视为 `need_info`**（安全侧：宁可多一轮追问，不可误生成文档）。
2. **文档轮直出 Markdown**：不启用 JSON Mode、无标签、无转义；`content` 增量直接包装为 `specification_delta` 事件（**真流式**），同时累计全文；thinking 模式下各轮 reasoning 均实时透传。
3. **串行依赖**：tasks 依赖 spec、checklist 依赖 spec+tasks，按序生成保证质量；每轮 `max_tokens` 配额独立（复用客户端全局 8192），单文档截断风险大幅下降。
4. **失败语义**：任一轮 `DeepSeekAPIError` → `error` + `done` 终止（与现状一致）；已流出的部分增量随前端错误提示可见，用户重试即可覆盖。
5. **协议兼容**：SSE 事件序列（meta/chunk/thinking/specification_delta/specification/status/error/done）与字段语义不变，**前端零改动**；技能层 `GenerateSpecDocsSkill` 转发机制不变；非流式 `/chat` 端点改为消费流式编排收集结果，返回结构不变。

## 3. 备选方案

| 方案 | 结论 |
|------|------|
| A. 标签分段协议（`<doc name="spec">` 等）单次调用 | 真流式且一次调用；但单次输出仍长、仍有截断风险，且流式标签切分解析器复杂度高，被否 |
| B. 对话判定 + 三文档串行直出 | **采纳**。结构上消灭转义与整包报废；单文档短而稳；实现清晰 |
| C. 保留 JSON 仅加容错 | 治标不治本，否 |

## 4. 影响

- **正面**：偶发解析失败根除（无 JSON）；文档生成真流式（体验对齐 ADR-040）；单文档失败域缩小。
- **代价**：成稿路径 LLM 调用 1→4 次，固定开销与历史重复传输使时延略增、成本略升（总生成 token 量相近）；追问路径仍为 1 次，不变。
- **删除**：`parse_spec_builder_response` / `RETRY_PROMPT` / JSON Mode 相关逻辑（无外部调用方）。
- **测试**：`test_spec_builder.py` 按四轮编排重写；技能层与端点层回归测试沿用。

## 5. 实际实施情况

已全量实施（2026-09-23），与决策一致，无偏差：

- `spec_builder_prompts.py`：判定轮提示词两版（fast/thinking）+ 三文档写作提示词；`<decision>` 标签契约与安全侧兜底规则落地。
- `spec_builder_parser.py`：`parse_decision` 实现标签剥离与 `need_info` 兜底；旧 JSON 解析器删除。
- `spec_builder.py`：四轮编排落地；文档轮真流式（`content` 增量 → `specification_delta`）；`chat()` 消费流收集、error 事件转抛 `TaskSpecificationError`（非流式端点错误语义保持）；前序文档上下文按 spec → tasks → checklist 逐轮携带。
- 事件协议与旧版完全兼容，前端/技能层/端点层零改动；`tests/agents/test_spec_builder.py` 按 Harness 重写 17 项全过，技能层与端点层回归 17 项全过。
