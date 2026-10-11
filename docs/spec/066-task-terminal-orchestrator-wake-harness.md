# ADR-066 任务终态自动唤醒编排 · Harness

## 1. 测试金字塔

```
      E2E（真实环境）      1 条：任务完成 → 唤醒轮 → 待办推进 → 会话落库
     集成（pytest+DB）     唤醒服务判定矩阵 / 触发钩子 / 后台轮落库 / 限流计数
 单元（pytest, 纯逻辑）    触发消息模板 / 限流判定 / 开关回退语义
```

## 2. 单元测试清单（UT-W）

| # | 用例 | 断言 |
|---|---|---|
| UT-W1 | 触发消息模板渲染（completed/failed 两态） | 含任务标题/终态/change_summary 或失败原因/四条指令关键词 |
| UT-W2 | 限流判定：同链 24h 内第 5 次唤醒 | 第 1–4 次放行，第 5 次拒绝 |
| UT-W3 | 限流判定：连续失败唤醒 3 次后 | 第 4 次拒绝 |
| UT-W4 | 开关关闭（settings 行 False / env 回退 False） | 不唤醒 |
| UT-W5 | 无关联会话 / 会话不存在 | 不唤醒 |

## 3. 集成测试清单（IT-W，db_session fixture）

| # | 用例 | 断言 |
|---|---|---|
| IT-W1 | completed 终态触发：mock 编排流，断言 schedule_wake 后会话 messages 追加触发消息 + AI 回复 | messages 长度 +2；assistant 含汇报文本 |
| IT-W2 | failed 终态触发同上 | 触发消息含失败原因 |
| IT-W3 | verify_blocked / review / cancelled 终态 | 不触发 |
| IT-W4 | 无 session_id 任务 completed | 不触发 |
| IT-W5 | steering 活跃时调度 → 延迟后执行（fake timer/monkeypatch sleep） | 最终落库 |
| IT-W6 | 限流：同链 5 次后第 6 次 completed | 不触发 + 审计日志 |

## 4. E2E 测试清单（真实环境）

| # | 场景 | 判据 |
|---|---|---|
| E2E-W1 | 派发轻量任务（要求 todo 登记）→ 节点完成 → ≤10s 会话出现唤醒消息与 AI 汇报；中枢待办「跟踪执行」变 completed | API + DB 双验 |
| E2E-W2 | 浏览器打开该会话：可见「[系统自动唤醒]」消息、AI 汇报、待办 5/5 推进 | 截图 |

## 5. CI 门禁

- 中枢：`ruff check app tests` 全过；`pytest tests/services tests/api -q` 零新增失败（既有 3 失败基线除外）。
- 相关集合：`test_orchestrator_wake.py` 全绿 + 既有 skills/agents 关键用例零回归。
- 文档：`python scripts/check_docs.py` 零错误。
