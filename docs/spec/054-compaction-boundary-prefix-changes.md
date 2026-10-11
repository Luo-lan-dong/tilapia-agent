# 压缩投影边界与摘要热前缀复用 Changes 变更清单（P1）

> 关联：[ADR-054](../adr/054-compaction-boundary-and-prefix-reuse.md) · [Spec](./054-compaction-boundary-prefix-spec.md) · [Harness](./054-compaction-boundary-prefix-harness.md)
> 规则：边开发边实时登记（修/增/减 + 函数级明细 + 作用说明），**禁止开发完成后一次性补记**。
> 范围约束：只覆盖 ADR-054 决策 A（安全切点）与决策 B（摘要热前缀复用）。**不含** `max_tokens` 分档、
> `reasoning_effort` 下发、工具结果裁剪（P2），也**不含**存量脏压缩点的数据修复（决策 F：用户明确不做）。

## 计划变更点（编码前骨架）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 |
|---|------|------------|-----------------|---------|
| 1 | `app/services/context_compaction_service.py` | 增 | `safe_cutoff(messages, cutoff) -> int` | 切点前移到工具轮之外；方向单调不增（防死循环） |
| 2 | `app/services/context_compaction_service.py` | 修 | `project_messages` 使用 `safe_cutoff` | 投影首条历史永不为 `tool`（治 400） |
| 3 | `app/services/context_compaction_service.py` | 增 | `build_summary_instruction(previous_summary) -> str` | 尾部压缩指令（五段模板 + 规则 + 旧摘要锚定），替代合成 system 角色 |
| 4 | `app/services/context_compaction_service.py` | 修 | `_generate_summary` 安全切点 + 前缀式调用 + 三级降级 | 复用真实请求热前缀；降级保证压缩永远可用 |
| 5 | `app/services/context_compaction_service.py` | 修 | `compact_if_needed` 落库 `message_count` 用安全切点 + 增 `cutoff_role` | 持久化值与投影行为自洽、可审计 |
| 6 | `app/clients/deepseek.py` | 修 | `chat_completion_messages` 增可选 `tools` 参数 | 前缀对齐需要 tools 参与 payload |
| 7 | `tests/services/test_context_compaction.py` | 增 | UT-28~UT-44 | 切点/不变量/前缀组装/降级链覆盖 |
| 8 | `tests/clients/test_deepseek.py` | 增 | tools 透传用例（UT-38 客户端侧） | payload 兼容性 |
| 9 | `tests/agents/test_orchestrator.py` | 增 | IT-7/IT-9 | 端到端无 400 中断 |

## 变更清单（实际实施明细）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/services/context_compaction_service.py` | 增 | 常量 `_SUMMARY_INSTRUCTION_TEMPLATE` | 尾部压缩指令模板（五段结构 + 规则 + 旧摘要锚定占位） |
| `app/services/context_compaction_service.py` | 增 | `safe_cutoff(messages, cutoff) -> int` | 切点前移到工具轮之外；先 clamp 后判定；找不到父级时原样返回（脏数据兜底） |
| `app/services/context_compaction_service.py` | 修 | `project_messages` 使用 `safe_cutoff`（双保险，兼容存量脏压缩点） | 投影首条历史永不为 `tool`（治 provider 400） |
| `app/services/context_compaction_service.py` | 增 | `build_summary_instruction(previous_summary=None) -> str` | 尾部指令正文；取代原合成 system 角色用法（`build_summary_prompt` 保留给降级路径） |
| `app/services/context_compaction_service.py` | 修 | `compact_if_needed` 增 `system_text`/`tools` 透传；落库 `message_count` 用安全切点 + 新增 `cutoff_role`；`context_compaction_done` 日志增 `cutoff_role` | 持久化与投影自洽、可审计 |
| `app/services/context_compaction_service.py` | 修 | `_generate_summary` 增 `system_text`/`tools` 参数：安全切点（`head = chat_messages[:cutoff]`）、切点为 0 时跳过、指令改 `build_summary_instruction`、调用改走 `_summarize_with_fallback` | 决策 A + B 落地 |
| `app/services/context_compaction_service.py` | 增 | `_summarize_with_fallback(client, system_text, head, instruction, tools)` | 三级降级：带 tools 真前缀 → 不带 tools 真前缀 → 原独立形态；含 `TypeError`（不支持 tools 关键字）兼容分支；空/空白返回视为失败；日志 `tier=prefix_with_tools/prefix/standalone` |
| `app/services/context_compaction_service.py` | 增 | 日志事件 `context_compaction_skipped_unsafe_cutoff` / `context_compaction_summary_done` / `context_compaction_summary_tier_failed` / `context_compaction_summary_prefix_tools_unsupported` | 压缩路径可观测（logging-standards 结构化事件） |
| `app/clients/deepseek.py` | 修 | `chat_completion_messages` 增可选 `tools` 参数并透传 payload；`deepseek_request_started` 日志增 `tools_count` | 前缀对齐需要 tools 参与 payload；不传时行为不变 |
| `app/agents/orchestrator.py` | 修 | `_compact_and_reproject` 的 `system_text` 文档收紧（必须是真实 system prompt，供前缀逐字重放） | 明确契约，避免调用方传拼装文本 |
| `tests/services/test_context_compaction.py` | 修 | 模块 docstring 增 ADR-054；`_FakeSummaryClient` 捕获 `tools`（`tool_args`）；新增 `_TieredSummaryClient`（按序控制成功/异常/空/TypeError） | 测试替身支撑降级链与前缀断言 |
| `tests/services/test_context_compaction.py` | 增 | UT-28~UT-35：`test_safe_cutoff_moves_before_tool_turn` / `_moves_for_any_tool_in_turn` / `_passthrough_when_not_tool` / `_is_monotonic_non_increasing` / `_clamps_and_tolerates_dirty_data` / `test_project_messages_never_starts_with_tool` / `test_project_messages_fixes_legacy_dirty_cutoff` / `test_compact_skips_when_safe_cutoff_is_zero` / `test_compact_persists_safe_cutoff_and_cutoff_role` | 切点规则、方向不变量、投影不变量、落库字段 |
| `tests/services/test_context_compaction.py` | 增 | UT-37~UT-44：`test_build_summary_instruction_content` / `test_summary_uses_real_prefix_with_tools` / `test_summary_falls_back_without_tools` / `test_summary_falls_back_to_standalone` / `test_summary_all_tiers_failed_skips_compaction` / `test_summary_empty_result_treated_as_failure` / `test_summary_typeerror_falls_back_to_next_tier` / `test_summary_without_system_text_uses_standalone`（+ 局部 `_compact_with` 辅助） | 前缀组装、tools 透传、指令文本、三级降级链 |
| `tests/clients/test_deepseek.py` | 增 | `test_chat_completion_messages_passes_tools` | 客户端侧 tools 透传与「不传则 payload 无该键」向后兼容 |
| `tests/agents/test_orchestrator.py` | 增 | IT-7 `test_compaction_cutoff_inside_tool_turn_does_not_break_chat`（运行时实测切点下标后把工具轮放在切点处）；IT-9 `test_legacy_dirty_cutoff_session_still_chats` | 端到端：切点落工具轮不再 400 中断；存量脏点可正常对话 |

## 登记记录

- 2026-09-27：创建骨架（ADR-054 四件套建立；Spec/Harness 同步完成，未写生产代码）。
- 2026-09-27：范围经用户确认（2026-09-27）——**存量 3 个脏压缩点会话不做数据修复**，
  只保证新建会话正常；故决策 F 明确写入 ADR，且投影侧保留动态安全切点，
  使存量脏点在新代码下也不再产生协议非法投影。
- 2026-09-27：实施完成并登记上表。实施要点：
  1. `safe_cutoff` 的「方向单调不增」被写成显式回归用例（UT-31，多组序列 × 全下标），
     因为它是「防死循环」的唯一守卫；
  2. 摘要降级链额外覆盖了 `TypeError`（客户端不支持 `tools` 关键字）——
     自定义 OpenAI 兼容 provider 可能不支持该参数，若直接抛错会让压缩整体失败；
  3. IT-7 的用例前提改为**运行时实测切点下标**再放置工具轮（初版按推算放置，实测
     切点落在 `user` 上导致前提不成立——推算受工具轮消息长度差异影响不可靠）。
- 2026-09-27：**反向验证**（证明测试真能抓住原缺陷）：把 `safe_cutoff` 临时改为恒等函数后，
  IT-7 在前提断言 `safe_cutoff(history, len(head)) < len(head)` 处失败、
  IT-9 在 `projected[1]["role"] != "tool"` 处失败（实际为 `'tool'`）；恢复实现后两条均通过。
- 2026-09-27：测试结果见下条。
- 2026-09-27：**真实后端端到端验证通过**（后端 PID 启动时间晚于全部改动，运行的是含 P0+P1 的新代码；
  测试会话用后已删除，用户既有 3 个脏会话未被触碰）：
  1. **场景构造**：自造 20 条等重历史并在「朴素切点」处放一轮双工具调用（首条历史
     估算 63,584 token > 触发线 37,344），使 `message_count` 必然落在 `tool` 上；
  2. **P1-A 通过**：事件序列 `meta → context_compacting → context_compacted → chunk →
     context_usage → done`，**无 `error`**（修复前该场景必 400 中断）；落库
     `message_count=18`（安全切点）、`cutoff_role=assistant`（原朴素切点角色为 `tool`）；
  3. **P0 校准通过**：落库 `est_chars_per_token=2.265`（种子 2.0 起算，观测密度
     `41045 chars / 14232 tokens ≈ 2.88`），日志 `context_calibration_updated` 与
     `context_compaction_done (cutoff_role=assistant)` 均出现；
  4. **P1-B 通过**：日志 `context_compaction_summary_done tier=prefix_with_tools
     with_tools=True replayed_messages=20`；`llm_usage_records` 显示摘要调用由
     「首次 4.0% 命中（冷启动）」变为**后续 99.7% 命中**（61,056 / 61,265 token），
     即真前缀复用生效（修复前摘要调用无共享前缀，命中率≈0）。
  - 验证期间自身踩到并排除的两个**测试数据**问题（非代码缺陷，记录以免复发）：
    ① `tool_calls` 缺 `id`/`type` 导致 provider JSON 反序列化失败；
    ② 同一 `tool_call_id` 配两条 `tool` 响应（一次调用只该有一条响应）导致
    provider 报「Messages with role 'tool' must be a response to a preceding
    message with 'tool_calls'」——即该报错的另一种触发途径，与 ADR-054 修的
    切点问题不同源，真实会话不会产生该形态。
