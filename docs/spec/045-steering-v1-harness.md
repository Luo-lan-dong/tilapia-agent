# Steering v1 Harness（测试清单）

> 关联：[ADR-045](../adr/045-orchestrator-steering-v1.md) · [Spec](./045-steering-v1-spec.md) · [Changes](./045-steering-v1-changes.md)

## 测试金字塔

```
E2E（手动：浏览器双窗口/单窗口插话，观察排队气泡与方向修正）
  └─ 端点（tests/api/，steer 端点 409/入队）
       └─ 集成（tests/agents/test_orchestrator.py，FakeClient + 真实队列驱动）
            └─ 单元（tests/services/test_steering_service.py，注册表语义）
```

## 单元测试（tests/services/test_steering_service.py）

| 编号 | 名称 | 断言 |
|------|------|------|
| UT-1 | 未 open 时 enqueue 返回 False | 无活跃流拒绝入队 |
| UT-2 | open 后 enqueue 成功、drain 按序取空 | FIFO 顺序 |
| UT-3 | close 后 enqueue 返回 False、drain 为空 | 流结束语义 |
| UT-4 | drain 幂等 | 第二次 drain 返回空 |

## 集成测试（tests/agents/test_orchestrator.py）

| 编号 | 名称 | 断言 |
|------|------|------|
| UT-5 | 工具轮边界提升 | 轮 1 调工具（执行前向队列投递）→ 轮 2 流式调用的 prompt 含插话 user 消息；steering_applied 事件下发 |
| UT-6 | 收尾前提升延续对话 | 单轮无工具回答 + 队列有插话 → 不 done，插话提升后模型再回答 → done |
| UT-7 | 配额重置 | max_rounds=2；每轮边界投递插话 → 跑满 2×N 轮才强制收尾 |
| UT-8 | 落库顺序事件 | `_steering_messages` 内部事件携带插话消息列表（端点消费） |
| UT-9 | 无插话零影响 | 队列空时事件序列与现状基线一致 |

## 端点测试（tests/api/ 或并入 orchestrator 集成）

| 编号 | 名称 | 断言 |
|------|------|------|
| UT-10 | steer 端点 | 活跃流入队 200；无活跃流 409 |

## E2E（手动验收）

| 场景 | 验证 |
|------|------|
| 浏览器插话 | 触发一个多技能任务，回复中插入修正消息 → 排队气泡出现 → AI 后续回答体现修正 |

## CI 门禁

- `pytest tests/services/test_steering_service.py tests/agents/test_orchestrator.py tests/clients/ -x`
- `ruff check` / `ruff format --check` 通过
- 前端：`npm run typecheck` 通过

## 回归风险点

| 风险 | 缓解 |
|------|------|
| while 手动计数改写引入轮数回归 | UT-9 基线对比 + 存量 max_rounds 测试回归 |
| 插话与 doom/轮上限收尾路径交互 | 收尾前 drain 统一处理（UT-6/UT-7 覆盖） |
| 落库覆盖冲掉插话 | 端点显式插入 steered 消息（UT-8 + 端点持久化断言） |
| 前端排队气泡与 steering_applied 乱序 | content 匹配清除标记；不匹配则忽略（防御） |
