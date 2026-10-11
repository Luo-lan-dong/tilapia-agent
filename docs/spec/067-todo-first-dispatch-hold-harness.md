# ADR-067 待办先行与派发确认门禁 · Harness

## 1. 单元测试（UT-H）

| # | 用例 | 断言 |
|---|---|---|
| UT-H1 | todo_write 无任务：挂会话（session_id 写入，task_id NULL） | add 成功，rows 返回清单 |
| UT-H2 | todo_write 有任务：行为不变（task_id 写入） | 回归 |
| UT-H3 | build_wake/dispatch hold 模板：create_task 后任务 dispatch_hold=True | DB 断言 |
| UT-H4 | 待办迁移：create_task 后会话名下 NULL 待办 task_id 全部指向新任务 | DB 断言 |

## 2. 集成测试（IT-H，db_session）

| # | 用例 | 断言 |
|---|---|---|
| IT-H1 | create_task 置 hold | task.dispatch_hold is True |
| IT-H2 | 调度器 skip：hold 任务在 pending 池不被派发（mock 分类函数调用计数） | 未派发 |
| IT-H3 | dispatch_task 清除 hold | hold=False，正常派发 |
| IT-H4 | API 直建任务（无 hold）自动派发不回归 | 正常派发 |
| IT-H5 | todo 迁移旁路：迁移失败不阻塞创建任务 | create 成功 |

## 3. E2E

| # | 场景 | 判据 |
|---|---|---|
| E2E-H1 | 新会话说「开发登录页（阶段1）」：首技能前待办已出现（截图） | 前端 |
| E2E-H2 | AI 报告等待确认后等待 ≥3 个调度周期，任务仍 pending 未执行 | DB/前端 |
| E2E-H3 | 回复「确认派发」→ 任务执行、待办全部推进 | 前端+DB |

## 4. CI 门禁

中枢 ruff 全过；`pytest tests/services tests/api -q` 零新增失败；`check_docs.py` 零错误。
