# LLM 客户端韧性 v1 Changes 变更清单

> 关联：[ADR-044](../adr/044-llm-resilience-v1.md) · [Spec](./044-llm-resilience-v1-spec.md) · [Harness](./044-llm-resilience-v1-harness.md)
> 规则：边开发边实时登记（修/增/减 + 作用说明），禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/core/config.py` | 修 | 增 `llm_retry_max_attempts`（默认 5，ge=1）+ `llm_retry_base_delay_seconds`（默认 1.0，ge=0） | 重试尝试次数与退避基数配置化 |
| `app/clients/deepseek.py` | 修 | ①增 `RETRYABLE_STATUS_CODES={429,500,502,503,504}` + 模块级 `_is_retryable_error()`；②DeepSeekClient 增 `_sleep_backoff()`（base×2^(n-1)±20% 抖动）/`_log_retry()`/`_with_retries()`（非流式重试包装）；③`chat_completion_raw`/`chat_completion_messages`：单次尝试提取为 `_send_once()` 闭包（post + 状态码检查一体，4xx/5xx 在重试单元内抛出才可触发退避）后包 `_with_retries`；④`chat_completion_stream`/`chat_completion_stream_raw`：while 重试循环 + `emitted` 标志（首帧 yield 前可重试，之后直接抛；`stream_raw` 拼装缓冲 content_parts/tool_calls_by_index/usage 随重试重置，tool_calls 增量不产出可安全重试）；⑤`_record_usage` 提取 `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` 落库 + `llm_prompt_cache_stats` debug 日志（命中率） | 瞬时故障自愈；流式防内容重复；cache 命中可观测 |
| `app/models/llm_usage_record.py` | 修 | 增 `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` 列映射（Integer 默认 0） | cache 观测 ORM |
| `alembic/versions/20260923_llm_cache.py` | 增 | 迁移 20260923_llm_cache（down_revision 20260921_env_ctx） | llm_usage_records 加 cache 两列 |
| `app/agents/orchestrator.py` | 修 | ①import 增 `RETRYABLE_STATUS_CODES`/`LLMProviderConfig`；②`chat_stream`：循环外 `first_llm_call`/`fallback_tried`/`fallback_client` 状态，`except DeepSeekAPIError` 内增降级分支（首次流式调用失败 + 未降级过 + 可重试状态码 → `_acquire_fallback_client` 换 client、warning 提示、meta 更新模型显示、model_window 换 fallback 窗口、continue 重跑本轮），`first_llm_call` 流成功后置 False，顶层 `finally` 关闭降级 client；③增 `_acquire_fallback_client()`（list_models 排除当前模型取 created_at 最早 + 构造 DeepSeekClient(scene=orchestrator) + orchestrator_model_fallback 日志） | 对话主链路持续性故障自愈 |
| `tests/clients/test_deepseek.py` | 修 | 增 UT-1 429 重试成功 / UT-2 400 不重试 / UT-3 5 次耗尽 / UT-4 网络异常重试 / UT-5 流式首帧前重试 / UT-6 首帧后不重试 / UT-7 cache 落库，共 7 项；`_fast_retry` 零退避注入 + `_make_seq_stream_client` 多阶段流式 mock | Harness client 层清单 |
| `tests/agents/test_orchestrator.py` | 修 | `FakeDeepSeekClient` 增 `api_error` 脚本项与 `close()`；增 UT-8 首轮失败降级成功 / UT-9 无其他模型不降级 / UT-10 降级再失败报错，共 3 项；`_seed_llm_providers` 测试数据辅助 | Harness orchestrator 层清单 |
| `docs/INDEX.md` / `docs/spec/040-orchestrator-agent-loop-roadmap.md` | 修 | ADR-044 三处登记 + 更新记录；P4 状态已完成 | 文档同步 |

## 登记记录

- 2026-09-23：创建骨架（ADR-044 四件套建立；方案经用户确认：仅对话主链路降级 / 5 次尝试退避 1s/2s/4s/8s / cache 落库+日志）。
- 2026-09-23：登记全部实施变更（上表）。联调修复 1 个设计缺陷：首版非流式重试单元只包 `post` 调用、状态码检查在外——429/5xx response 正常返回不抛异常导致重试永不触发（UT-1/UT-3 捕获）；修复为 `_send_once()` 闭包把「发请求 + 状态码检查」合并进重试单元。
- 2026-09-23：测试通过——tests/clients/test_deepseek.py 16 passed（9 存量 + 7 新增）、tests/agents/test_orchestrator.py 34 passed（31 存量/前序 + 3 降级新增）；alembic upgrade head 至 20260923_llm_cache；后端已重启加载 ADR-044 代码，健康检查 200。
