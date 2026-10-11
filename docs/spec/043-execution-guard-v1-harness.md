# 执行保护 v1 Harness（测试清单）

> 关联：[ADR-043](../adr/043-execution-guard-v1.md) · [Spec](./043-execution-guard-v1-spec.md) · [Changes](./043-execution-guard-v1-changes.md)

## 测试金字塔

```
E2E（1 项，手动脚本）
  └─ 集成（tests/agents/test_orchestrator.py，FakeDeepSeekClient 驱动 chat_stream）
       └─ 单元（签名规范化纯函数直测）
```

## 单元测试（UT）

| 编号 | 名称 | 断言 |
|------|------|------|
| UT-1 | 签名规范化：参数顺序无关 | `{"a":1,"b":2}` 与 `{"b":2,"a":1}` 签名相同 |
| UT-2 | 签名规范化：非法 JSON 参数回退原串 | 原样字符串参与签名，不抛异常 |
| UT-3 | 技能超时：挂起技能被中止 | FakeSkill `asyncio.sleep` 挂起 + 配置注入短超时（如 0.2s）→ step_finished status=failed、tool 消息 content 含「超时」、对话循环继续（后续轮仍执行） |
| UT-4 | 技能超时：未超时技能零影响 | 正常技能完成，事件序列与 ADR-040 基线一致 |
| UT-5 | doom：连续 3 轮相同调用触发收尾 | 第 3 轮技能 execute 未被调用（计数器=2）、warning 事件下发、done 前有最终回答、轨迹含「未执行」tool 消息 |
| UT-6 | doom：阈值内重复不触发 | 连续 2 轮相同后第 3 轮换调用 → 全部正常执行 |
| UT-7 | doom：交替重复不误伤 | A,B,A,B 循环 → 不触发收尾（走步数上限） |
| UT-8 | doom：参数变化不算相同 | 同名技能参数不同（page=1/page=2）→ 不触发 |

## 集成/E2E 测试

| 编号 | 场景 | 验证方式 |
|------|------|---------|
| E2E-1 | 真实 LLM 会话中技能正常执行回归 | `scripts/e2e_agent_loop_v1_20260921.py` 既有脚本回归通过（轨迹/流式行为不变） |
| E2E-2 | doom 端到端（可选，依赖模型配合） | 构造诱导性 prompt 让模型重复调用同一技能，观察 warning 事件与提前收尾；模型行为不确定，以 UT-5 为准 |

## CI 门禁

- `pytest tests/agents/test_orchestrator.py tests/services/ -x`（新增 UT 全过 + 存量回归）
- `ruff check` / `ruff format --check` 通过
- 前端零改动，不涉及 typecheck/lint 门禁

## 回归风险点

| 风险 | 缓解 |
|------|------|
| `asyncio.timeout` 包 async generator 的取消传播 | UT-3/UT-4 覆盖正反两面 |
| doom 补 tool 消息遗漏导致下一轮 400 | doom 命中后立即收尾 return，不存在下一轮；补消息保障轨迹完整（UT-5 断言） |
| 超时后共享 session 脏状态 | 超时路径 rollback（UT-3 中后续轮查询正常隐式验证） |
