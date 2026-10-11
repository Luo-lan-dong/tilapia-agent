# 实施范围：任务终态自动唤醒编排（ADR-066）

> 规则：先登记实施范围，再动代码；每完成一个模块回填状态与验收结果。

## 0. 模块与登记表

| 模块 | 状态 | 涉及文件（预估） | 验收命令 / 观察点 |
|---|---|---|---|
| M1 开关 | ✅ 完成（2026-10-06） | `app/core/config.py`（`orchestrator_auto_wake_enabled: bool = True`，alias `ORCHESTRATOR_AUTO_WAKE_ENABLED`）、`app/models/system_settings.py`（`orchestrator_auto_wake_enabled` 列）、Alembic 迁移 `20261006_orchestrator_auto_wake`、`app/services/system_settings_service.py`（getter/setter）、`app/api/v1/endpoints/settings.py`（GET/PUT 开关端点）、前端设置页开关（后续视需要） | 中枢 `ruff` + `pytest tests/services tests/api -q`；settings API 读写 |
| M2 唤醒服务 | ✅ 完成（2026-10-06） | 新 `app/services/orchestrator_wake_service.py`（`schedule_wake`：前置检查矩阵/限流/steering 重试/触发消息模板/后台编排轮消费与落库，复用 `_persist_chat_exchange`）、`app/services/task_event_handler.py`（`_handle_task_completed`/`_handle_task_failed` 落库成功后钩子） | `pytest tests/services/test_orchestrator_wake.py`；真实任务 completed 后会话 messages 追加 |
| M3 提示词与防环 | ✅ 完成（2026-10-06） | 触发消息模板（唤醒服务内）+ 同链限流（root_task 24h ≤5 次、连续失败 ≤3 次，`tool_call_log_service` 审计） | UT-W1~W5 / IT-W6 |
| M4 前端 | ✅ 完成（2026-10-06） | 无需改动——唤醒轮消息走既有会话恢复/轮询渲染；设置页开关沿用 ai_dispatch 同页新增一项（`Settings.tsx` + API） | 设置页开关读写；会话打开可见唤醒轮 |
| M5 收尾 | ✅ 完成（2026-10-06） | E2E-W1/W2 + 文档 | 真实任务实测 + `check_docs.py` 零错误 |

## 1. 回归与红线（每模块完成都要跑）

- 中枢：`python -m ruff check app tests` + `python -m pytest tests/services tests/api -q`（既有 3 失败基线除外）。
- 红线：①唤醒轮不影响任务状态机与调度器（只读任务 + 写会话/待办/审计）；②唤醒失败静默降级不抛错；
  ③前端在线对话不受影响（steering 互斥）。

## 2. 进度记录

| 日期 | 模块 | 内容 | 验收结果 |
|---|---|---|---|
| 2026-10-06 | — | 立项：ADR-066 四件套（用户裁决方案 A，理念=平台自主性闭环） | 仅文档 |
