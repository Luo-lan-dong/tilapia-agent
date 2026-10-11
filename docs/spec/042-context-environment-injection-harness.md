# 环境上下文注入 v1 Harness

> 关联：[ADR-042](../adr/042-context-environment-injection.md) · [Spec](./042-context-environment-injection-spec.md) · [Changes](./042-context-environment-injection-changes.md)

## 1. 测试金字塔

```
      E2E（1）           真实对话 system 含环境块（抓包/prompt 断言）
       集成（3）          首轮 baseline / 变化增量 / 无会话
      单元（10+）         块生成 / 快照对比 / 增量构造 / 降级
```

## 2. 单元测试清单（tests/services/test_context_environment.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-1 | 时间行格式 | 含 YYYY-MM-DD、星期X、HH时、时区名 |
| UT-2 | 集群概况聚合 | 按 agent_type/status 分组统计文本正确；离线不计 |
| UT-3 | 概况查询失败降级 | 块仅含时间行，不抛异常 |
| UT-4 | 快照对比-相同 | current == snapshot → 不生成增量 |
| UT-5 | 快照对比-变化 | current != snapshot → 生成 `<environment-update>` 增量消息（role=system） |
| UT-6 | 首轮判定 | 无快照 → baseline 注入 + 快照落库 |
| UT-7 | 增量位置 | 追加在 prompt_messages 尾部、spec 上下文之前 |

## 3. 集成测试（tests/agents/test_orchestrator.py 增补）

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-1 | 首轮 baseline | 会话首轮对话 → LLM 收到的 system 含 `<environment>`；快照落库 |
| IT-2 | 环境未变不追加 | 第二轮（快照相同）→ LLM system 不变、无 `<environment-update>` |
| IT-3 | 环境变化出增量 | 修改快照块模拟变化 → 下一轮 prompt 尾部出现 `<environment-update>` 且快照更新 |
| IT-4 | 无会话回归 | session_id=None → 环境块注入但不落快照 |

## 4. E2E

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-1 | 问"今天是几号星期几" | 模型回答与真实日期一致（环境块生效） |

## 5. CI 门禁

- pytest tests/services/test_context_environment.py tests/agents/test_orchestrator.py 全绿；
- ruff format/check 通过；前端无改动。
