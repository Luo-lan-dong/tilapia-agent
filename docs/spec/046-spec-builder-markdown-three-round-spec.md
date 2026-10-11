# 三件套生成协议重构 Spec（对话判定 + 三文档串行直出）

> 关联 ADR-046。上游决策：ADR-010（Spec Chat）、ADR-028（流式 UI 增强）、ADR-033（技能体系）。

## 1. 功能描述

把三件套（spec/tasks/checklist）的生成从"DeepSeek JSON Mode 单次调用"重构为"对话判定 + 三文档串行直出 Markdown"，根除偶发性 JSON 解析失败，并将文档生成升级为真流式。

## 2. 用户故事

- **US-1 追问**：作为用户，我描述需求后信息不足时，AI 直接向我追问（自然语言，无标签泄露），右侧文档面板保持原草案。
- **US-2 成稿**：信息足够时，AI 说一句过渡语，随后 spec → tasks → checklist 三份文档**逐字实时**出现在右侧面板（真流式），完成后「创建任务」按钮可用。
- **US-3 失败可见**：任一文档轮 LLM 失败时，对话区出现明确错误提示，可重试。
- **US-4 增量更新**：已有草案时再次生成，各文档基于旧草案修订完善，而非从零重写。

## 3. 核心流程

```
┌────────────────────────── Orchestrator / /spec / 旧链路 ──────────────────────────┐
│                                                                                   │
│  chat_stream(messages, current_specification, mode)                               │
│      │                                                                            │
│      ├── meta 事件（model/mode）                                                  │
│      │                                                                            │
│      ├── 轮1 对话判定（无 JSON Mode，reasoning 实时透传）                          │
│      │     system: CONVERSATION prompt（fast/thinking 两版）                      │
│      │     + 对话历史 + 当前三件套草案                                            │
│      │     输出：追问文本 / 过渡语 + 末行 <decision>need_info|ready</decision>    │
│      │     收完 → 解析标签（缺失兜底 need_info）→ 剥标签伪流式 chunk 下发         │
│      │                                                                            │
│      ├── need_info ──→ status{is_ready_to_create:false} ──→ done ──→ 结束        │
│      │                                                                            │
│      └── ready ──→ status{message:"正在编写规范文档…"}                            │
│            │                                                                      │
│            ├── 轮2 spec       system: DOC_SPEC prompt      真流式 spec delta      │
│            ├── 轮3 tasks      system: DOC_TASKS prompt(+spec)   真流式 tasks delta │
│            ├── 轮4 checklist  system: DOC_CHECKLIST prompt(+spec+tasks) delta     │
│            │     每轮：对话历史 + 草案 + 前序文档；content 增量→specification_delta │
│            │                                                                      │
│            └── 全量 specification + status{is_ready_to_create:true} + done        │
│                                                                                   │
│  任一轮 DeepSeekAPIError → error + done（终止，草案保持原状）                      │
└───────────────────────────────────────────────────────────────────────────────────┘
```

## 4. SSE 事件协议（与现状兼容，前端零改动）

| 事件 | data | 时机 |
|------|------|------|
| `meta` | `{model, mode}` | 开始 |
| `thinking` | `{delta}` | 各轮 reasoning 增量（thinking 模式） |
| `chunk` | `{delta}` | 轮 1 对话回复（剥标签后伪流式） |
| `status` | `{message}` | 进入文档轮时提示"正在编写规范文档…" |
| `specification_delta` | `{field, delta}` | 文档轮 Markdown 真流式增量（field=spec/tasks/checklist） |
| `specification` | `{spec, tasks, checklist}` | 三轮完成后的全量校准 |
| `status` | `{is_ready_to_create}` | need_info→false（即时）；成稿→true（三轮后） |
| `error` | `{message}` | 任一轮 LLM 失败 |
| `done` | `{}` | 结束 |

## 5. 降级链路

1. **decision 标签缺失** → 兜底 `need_info`（多一轮追问优于误生成）。
2. **任一轮 API 失败** → `error` + `done`；已流出增量由前端错误提示兜底，重试覆盖。
3. **重试**：不再有 JSON 重试轮；API 层既有重试退避（ADR-044）继续生效。

## 6. 验收标准

1. 追问路径：无标签泄露到对话区；文档面板不变；ready=false。
2. 成稿路径：三份文档按 spec→tasks→checklist 顺序真流式出现；deltas 拼接 == 全量 specification；ready=true。
3. 思考内容全量透传、不出现在正文。
4. 文档正文无 `<decision>` 标签、无代码块围栏污染（提示词约束）。
5. 轮 1 API 失败/文档轮 API 失败：error 事件携带原因，done 收尾。
6. 非流式 `/chat` 返回结构与改造前一致。
7. 全量测试通过；技能层/端点层回归无破坏。
