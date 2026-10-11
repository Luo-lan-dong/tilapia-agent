# 三件套生成协议重构 Changes 变更清单

> 关联 ADR-046 / spec-builder-markdown-three-round-spec。已全量实施。

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|----------|---------------|----------|
| app/agents/spec_builder_prompts.py | 改 | 删 `FAST_SYSTEM_PROMPT`/`THINKING_SYSTEM_PROMPT`/`RETRY_PROMPT`；增 `CONVERSATION_SYSTEM_PROMPT_FAST/_THINKING`（判定轮：追问或过渡语 + 末行 `<decision>` 标签）、`DOC_SPEC/TASKS/CHECKLIST_SYSTEM_PROMPT`（三文档直出 Markdown，禁开场白与围栏）、`DECISION_NEED_INFO/READY`、`_DECISION_OPEN/_CLOSE` | JSON 输出契约 → 判定标签 + 直出 Markdown 契约 |
| app/agents/spec_builder_parser.py | 改 | 删 `parse_spec_builder_response`/`_normalize_flat_fields`/`_validate_parsed`；增 `parse_decision`（剥 `<think>`/`<decision>` 标签，缺失或未知值兜底 `need_info`） | JSON 解析器退役，换轻量判定标签解析（安全侧兜底） |
| app/agents/spec_builder.py | 改 | 重写 `chat_stream` 为四轮编排（判定轮 `_receive_conversation_round` 正文收完解析+伪流式 chunk；文档轮 `_stream_doc_round` content 增量→`specification_delta` 真流式）；`_build_conversation_messages`/`_build_doc_messages`（tasks 轮携 spec、checklist 轮携 spec+tasks，均含草案）；`chat` 改为消费 chat_stream 收集结果（error 事件转抛 `TaskSpecificationError`，返回结构不变）；删 `_chat_with_retry`/`_receive_stream_chunks`/`_yield_parsed_events`/`chat` 内 JSON 解析重试 | JSON Mode 单调用 → 对话判定 + 三文档串行直出，根除解析失败，文档生成真流式 |
| app/api/v1/endpoints/task_building.py | 无改动 | — | `/chat` 返回结构不变、`/chat/stream` 事件协议不变 |
| app/skills/spec_generation.py | 无改动 | — | 技能事件转发机制与四轮编排事件流兼容 |
| opc-web-console（前端） | 无改动 | — | SSE 事件协议（meta/chunk/thinking/specification_delta/specification/status/error/done）完全兼容 |
| tests/agents/test_spec_builder.py | 改 | 按 Harness §2 重写 17 项：parse_decision 5 项、成稿路径（事件序/无标签泄露/delta 拼接==全量/上下文携带/无 JSON Mode）4 项、追问路径 2 项、thinking 透传 1 项、失败语义 3 项、非流式兼容 3 项 | 覆盖四轮编排、真流式、失败兜底与非流式兼容 |

## 验证记录

- `tests/agents/test_spec_builder.py` 17 passed
- 回归 `tests/skills/test_spec_generation.py` + `tests/api/v1/test_task_building.py` + `tests/api/v1/test_task_building_session.py` 17 passed
- ruff check / format 全过
