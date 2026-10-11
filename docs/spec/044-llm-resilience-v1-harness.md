# LLM 客户端韧性 v1 Harness（测试清单）

> 关联：[ADR-044](../adr/044-llm-resilience-v1.md) · [Spec](./044-llm-resilience-v1-spec.md) · [Changes](./044-llm-resilience-v1-changes.md)

## 测试金字塔

```
E2E（手动：断网/换 key 场景观察降级提示与对话继续）
  └─ 集成（tests/agents/test_orchestrator.py，FakeClient 注入降级链路）
       └─ 单元（tests/clients/test_deepseek.py，respx/mock transport 驱动重试）
```

## 单元测试（client 层，tests/clients/test_deepseek.py）

| 编号 | 名称 | 断言 |
|------|------|------|
| UT-1 | 429 后成功：重试焂住 | mock 连续 429→200，返回正常结果；调用 2 次；重试日志存在 |
| UT-2 | 400 不重试 | mock 400，仅调用 1 次立即抛 DeepSeekAPIError |
| UT-3 | 5 次全失败 | mock 全 500，尝试 5 次（可配注入小退避避免测试慢），抛最后一次 |
| UT-4 | 网络异常可重试 | mock ConnectError→200，重试成功 |
| UT-5 | 流式首帧前失败可重试 | mock 流式 429→成功流，完整收到增量 |
| UT-6 | 流式首帧后失败不重试 | mock 正常首帧后断流，异常抛出且不重建流（内容不重复） |
| UT-7 | cache 字段落库 | usage 带 hit/miss 字段时 _record_usage 写入对应列 |

## 集成测试（orchestrator 层，tests/agents/test_orchestrator.py）

| 编号 | 名称 | 断言 |
|------|------|------|
| UT-8 | 首轮失败降级成功 | 主 client 抛 5xx（重试耗尽语义）→ warning 事件含「降级」、meta 事件 model=fallback、fallback 完成回答、done 正常 |
| UT-9 | 无其他模型不降级 | llm_providers 仅一条时失败直接 error，无 warning |
| UT-10 | 降级模型再失败报错 | fallback 也抛错 → error 事件结束，不无限降级 |

## CI 门禁

- `pytest tests/clients/ tests/agents/test_orchestrator.py -x`（新增 UT 全过 + 存量回归）
- `ruff check` / `ruff format --check` 通过
- alembic 迁移可升级（session fixture 自动 upgrade head 验证）
- 前端零改动，无 typecheck/lint 门禁

## 回归风险点

| 风险 | 缓解 |
|------|------|
| 重试拖慢正常失败场景的报错（400 等） | 不可重试类立即抛（UT-2） |
| 流式重试导致内容重复 | emitted 标志：首帧 yield 后不再重试（UT-6） |
| 降级重跑造成事件重复 | 仅首次流式调用失败降级（此时前端只有 meta），UT-8 断言事件序列 |
| 退避睡眠拖慢测试 | 测试经 _fake_settings 注入 base_delay≈0 |
