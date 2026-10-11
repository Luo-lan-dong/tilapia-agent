# 上下文管理 P0（计量与压缩触发）Changes 变更清单

> 关联：[ADR-041 §修订 2026-09-27](../adr/041-context-management-v1.md) · [Spec](./041-context-management-v1-spec.md) · [Harness](./041-context-management-v1-harness.md)
> 根因依据：[排查记录 2026-09-27](../issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md)
> 规则：边开发边实时登记（修/增/减 + 函数级明细 + 作用说明），**禁止开发完成后一次性补记**。
> 范围约束：本清单只覆盖 P0 三项修订；**不含**压缩切点边界修正（P1）、摘要热前缀复用（P1）、`max_tokens`/`reasoning_effort`/工具裁剪（P2）。

## 计划变更点（编码前骨架，逐项开发后回填实际明细）

| # | 文件 | 计划变更类型 | 计划函数/类级明细 | 作用说明 |
|---|------|------------|-----------------|---------|
| 1 | `app/services/context_compaction_service.py` | 增 | `estimate_chars_per_token(compaction) -> float` | 从会话压缩点读校准密度，缺失回退常数 |
| 2 | `app/services/context_compaction_service.py` | 增 | `calibrate_chars_per_token(measured_chars, real_prompt_tokens, previous) -> float \| None` | 观测密度 + EMA 平滑 + `[1.5,6.0]` 夹取 + ≥2000 噪声门槛 |
| 3 | `app/services/context_compaction_service.py` | 增 | `measure_payload_chars(messages, tools, system_text) -> int` | 与估算同口径的规范化字符数（校准用） |
| 4 | `app/services/context_compaction_service.py` | 修 | `estimate_tokens` / `estimate_messages_tokens` 增 `chars_per_token` 参数（默认 `CHARS_PER_TOKEN`） | 密度可注入；默认保持既有行为（向后兼容） |
| 5 | `app/services/context_compaction_service.py` | 修 | `should_pre_compact` 增 `chars_per_token` 参数并透传 | 触发判定用校准密度 |
| 6 | `app/services/context_compaction_service.py` | 增 | `should_compact_now(messages, tools, window, system_text, compaction) -> bool` | 越线 + head 确有新增（与 `_generate_summary` 防重复同源），供事件条件化 |
| 7 | `app/agents/orchestrator.py` | 修 | `chat_stream`：密度取自 `estimate_chars_per_token`；判定改用 `prompt_messages`；`context_compacting` 条件化；usage 后回写校准值 | P0 三项修订落地 |
| 8 | `tests/services/test_context_compaction.py` | 增/修 | UT-18~UT-24（7 项）；UT-1 改为参数化断言 | 校准与触发逻辑的单测覆盖 |
| 9 | `tests/agents/test_orchestrator.py` | 增 | UT-25~UT-27（3 项）+ IT-5/IT-6 | 判定对象、事件条件化、校准回写闭环 |

## 变更清单（实际实施明细，边开发边登记）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/services/context_compaction_service.py` | 增 | 常量 `CALIBRATION_MIN_PROMPT_TOKENS=2000` / `CALIBRATION_ALPHA=0.3` / `CALIBRATION_MIN_CHARS_PER_TOKEN=1.5` / `CALIBRATION_MAX_CHARS_PER_TOKEN=6.0` | 校准门槛、平滑系数与夹取区间 |
| `app/services/context_compaction_service.py` | 修 | `CHARS_PER_TOKEN` 注释降级为「冷启动种子」 | 记录 1.66x 高估结论与校准入口 |
| `app/services/context_compaction_service.py` | 增 | `measure_payload_chars(messages, tools, system_text)` | 与估算同口径的规范化字符数（校准路径唯一字符口径） |
| `app/services/context_compaction_service.py` | 修 | `estimate_tokens(text, chars_per_token=CHARS_PER_TOKEN)` | 密度可注入；缺省保持既有行为 |
| `app/services/context_compaction_service.py` | 修 | `estimate_messages_tokens(..., chars_per_token=CHARS_PER_TOKEN)` 改为委托 `measure_payload_chars` | 估算与校准强制同口径，消除两套字符计算漂移 |
| `app/services/context_compaction_service.py` | 增 | `calibrate_chars_per_token(measured_chars, real_prompt_tokens, previous)` | 观测密度 + EMA 平滑 + 夹取 + 噪声门槛；样本不可用返回 `previous` |
| `app/services/context_compaction_service.py` | 增 | `estimate_chars_per_token(compaction)` | 读取会话校准值（含夹取与类型校验，显式排除 `bool`），缺失回退种子 |
| `app/services/context_compaction_service.py` | 修 | `should_pre_compact(..., chars_per_token=CHARS_PER_TOKEN)` | 触发判定使用校准密度（参数透传，缺省兼容） |
| `app/services/context_compaction_service.py` | 增 | `should_compact_now(messages, tools, window, system_text, compaction, chars_per_token)` | 越线 ∧ head 相对既有 `message_count` 有新增；与 `_generate_summary` 防重复判定同源 |
| `app/agents/orchestrator.py` | 修 | 导入增 `calibrate_chars_per_token` / `estimate_chars_per_token` / `measure_payload_chars` / `should_compact_now`；移除已不用的 `should_pre_compact` | 接线 |
| `app/agents/orchestrator.py` | 修 | `chat_stream`：新增局部 `_finalize_prompt()`（环境快照/增量，幂等可重复调用），预压判定改读**定稿后的 `prompt_messages`** 与校准密度，`context_compacting` 改由 `should_compact_now` 把关，压缩后重投影并再次 `_finalize_prompt()` | P0 三项修订落地：度量对象、密度、事件条件化 |
| `app/agents/orchestrator.py` | 增 | `OrchestratorAgent._compact_and_reproject(...)` | 统一预压与 overflow 兜底两条压缩路径（含 `tool_call_logs` compaction 记录），消除重复实现 |
| `app/agents/orchestrator.py` | 增 | `OrchestratorAgent._calibrate_session_density(building_session, payload_chars, usage)` | 成功拿到真实 usage 后合并写回 `est_chars_per_token`/`est_sample_at`（仅会话已有压缩点时；失败仅告警） |
| `app/agents/orchestrator.py` | 修 | overflow 兜底分支改用 `_compact_and_reproject`，重投影后补 `_finalize_prompt()` | 与预压路径共用实现；避免重投影丢弃环境增量 |
| `tests/services/test_context_compaction.py` | 修 | 原 UT-1 → `test_estimate_tokens_chars_per_2`：改为参数化断言（显式 2/4 + 缺省等价），补充 `CHARS_PER_TOKEN == 2` | 常数降级为种子后的回归保护 |
| `tests/services/test_context_compaction.py` | 增 | UT-18~UT-24：`test_estimate_tokens_density_ratio_is_linear` / `test_measure_payload_chars_same_scale_as_estimate` / `test_calibrate_chars_per_token_ema_from_seed` / `_clamps` / `_noise_guard` / `test_estimate_chars_per_token_reads_compaction` / `test_should_pre_compact_uses_calibrated_density` / `test_should_compact_now_requires_new_head` / `test_should_compact_now_true_when_head_grew` | 校准公式/夹取/噪声门槛/读取回退/密度参与判定/推进判定共 9 项 |
| `tests/agents/test_orchestrator.py` | 增 | UT-25 / IT-6 `test_no_compacting_event_when_cutoff_closed`；UT-26 `test_compacting_event_when_head_grew`；UT-27 / IT-5 `test_calibration_persists_est_chars_per_token`；边界 `test_calibration_skipped_without_usage_or_compaction` | 判定度量对象、事件成对语义、校准回写与保留 |

## 登记记录

- 2026-09-27：创建骨架（ADR-041 修订 + Spec/Harness 同步完成，编码前只建骨架，未写生产代码）。
- 2026-09-27：范围与不做项已与用户确认口径——P0 只做计量密度校准、判定度量对象、事件条件化三项；压缩切点边界与摘要缓存复用留 P1。
- 2026-09-27：实施完成并登记上表。**范围外改动**（开发中发现、未新增决策）：
  1. 环境快照/增量逻辑从「压缩之后一次性执行」抽为可重复调用的局部 `_finalize_prompt()`——原顺序下压缩重投影会把环境增量丢弃（压缩后重投影由 `_build_messages` 覆盖 `prompt_messages`）；此抽取是 P0「判定度量对象须为实际请求」的必然前提，不改变既有语义；
  2. 新增 `_compact_and_reproject` 合并两条压缩路径——避免预压与 overflow 兜底的实现漂移，行为等价（`tool_call_logs` 的 `name` 仍分别为 `pre_compact` / `overflow_compact`）。
- 2026-09-27：测试结果——`tests/services/test_context_compaction.py` 27 项全过；`tests/agents/test_orchestrator.py` 40 项全过（含新增 4 项与既有 36 项回归）。
- 2026-09-27：定向回归（受本变更影响的 7 个模块）**103 项全过**：
  `tests/services/test_context_compaction.py`、`tests/agents/test_orchestrator.py`、
  `tests/services/test_context_environment.py`、
  `tests/agents/test_orchestrator_prompts_backend_first.py`、
  `tests/agents/test_orchestrator_attachments.py`、
  `tests/clients/test_deepseek.py`、`tests/clients/test_deepseek_stream_tools.py`
  （2 条 warning 为存量：starlette TestClient 弃用提示 + 某用例未 await 的协程告警）。
  另：全量 `pytest tests`（80 个文件，含真实库/网络用例）单轮超过 15 分钟未能跑完，
  按「定向回归 + 受影响模块全覆盖」的口径收口，未取全量绿灯。
- 2026-09-27：静态检查——改动文件 `ruff check` 全过、`ruff format` 已格式化；
  `ruff check app` 余 2 项为**存量**（`app/services/resource_sync_service.py:410/449`，
  与本次变更无关，未修改该文件）。
- 2026-09-27：**实施中发现并修正的一处设计错配**（已回写 Spec/Harness 口径）：
  `should_compact_now` 初版用「投影后的请求」同时做压力判定与 head 切分，但
  `_generate_summary` 的防重复判定读的是**会话原始历史** —— 两者不同源时会出现
  「事件不下发但摘要端也不推进」或反向错配。修正为双口径入参：
  `pressure_messages`（实际请求，判越线）与 `chat_messages`（原始历史，判 head 新增），
  并为该错配补回归用例 UT-24b/UT-24c。
- 2026-09-27：**另一处被测试前提暴露的真实语义**（已写入 Harness UT-25/IT-6 说明）：
  压缩生效后，请求体会被压到「checkpoint + recent 尾部」，往往**重新低于触发线**；
  此时不再判定推进、不再提示「正在压缩」，直到新内容再次把请求推过触发线才压缩。
  这正是修复后的期望行为（修复前读原始全量历史，因此越线后每轮都提示）。
