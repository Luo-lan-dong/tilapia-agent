# Steering v1 Changes 变更清单

> 关联：[ADR-045](../adr/045-orchestrator-steering-v1.md) · [Spec](./045-steering-v1-spec.md) · [Harness](./045-steering-v1-harness.md)
> 规则：边开发边实时登记（修/增/减 + 作用说明），禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `app/services/steering_service.py` | 增 | 模块级 `_queues` 注册表 + `open_stream`/`close_stream`/`enqueue`/`drain` | 会话级插话队列（单进程；重复 open 清空防残留串话） |
| `app/agents/orchestrator.py` | 修 | ①增 `_STEERING_EVENT` 常量与 `_apply_steered_messages()`（user 消息 append 进 prompt + steering_applied 前端事件 + _steering_messages 内部事件）；②`chat_stream`：meta 前 `open_stream`（保证前端解禁时可投递）、for→while 手动计数 `rounds_used`、三处边界 drain（收尾判定时含本轮 assistant 正文回填 + 延续对话 / doom 收尾前 / 工具轮完成后 / 轮上限收尾前）+ 提升即 `rounds_used=0`、`finally` `close_stream` | 执行中插话边界提升 + 配额重置（tilacode 对标） |
| `app/api/v1/endpoints/task_building.py` | 修 | ①增 `TaskBuildSteerRequest` + `POST /chat/steer` 端点（无活跃流 HTTPException 409，全局处理器转统一格式 code=100003）；②`_task_build_event_stream` 收集 `_steering_messages`；③`_persist_chat_exchange` 增 `steered_messages` 参数，插入顺序 = 历史→工具轨迹→插话→回复 | 插话投递入口与落库协调 |
| `tests/services/test_steering_service.py` | 增 | UT-1 未 open 拒绝 / UT-2 FIFO / UT-3 close 后拒绝 / UT-4 drain 幂等 / 重复 open 清空，共 5 项 | 注册表语义 |
| `tests/agents/test_orchestrator.py` | 修 | 顶层 import asyncio；增 UT-5 工具轮边界提升（prompt 断言 + steering_applied + close 注销）/ UT-6 收尾前提升延续对话 / UT-7 配额重置（max_rounds=2 反复重置），共 3 项 | Harness 集成清单 |
| `scripts/verify_steer_endpoint.py` | 增 | 无活跃流 409 语义验证脚本 | 端点连通性验收 |
| 前端 `types/taskBuilding.ts` | 修 | 事件类型增 `steering_applied`；ChatMessage 增 `queued?: boolean` | 类型支撑 |
| 前端 `api/taskBuilding.ts` | 增 | `steerSession(sessionId, content)`（ApiError 抛出供调用方降级） | 插话投递 API |
| 前端 `hooks/useTaskBuildingChat.ts` | 修 | 增 `steer()` 动作（入队 + 乐观排队气泡；失败返回 false 降级）；`steering_applied` case 按 content 匹配清除最早 queued 标记；返回值透出 steer | 插话状态机 |
| 前端 `pages/TaskNew.tsx` | 修 | `handleSend` sending 分支（steer → 失败降级 sendMessage）；`canSend` 去掉 `!sending` | 发送行为分支 |
| 前端 `components/TaskNewInputBar.tsx` | 修 | sending 且可发送时按钮变「插话」（send-button-steer，含提示文案）；否则保留「回答中」禁用态 | 插话入口 UI |
| 前端 `components/TaskNewChatPanel.tsx` | 修 | user 气泡 label 渲染「已排队」角标（queued-tag） | 排队可视化 |
| `docs/INDEX.md` / `docs/spec/040-orchestrator-agent-loop-roadmap.md` | 修 | ADR-045 三处登记 + 更新记录；P5 状态已完成；路线图 P0-P5 全部收官 | 文档同步 |

## 登记记录

- 2026-09-23：创建骨架（ADR-045 四件套建立；方案经用户确认：完整 Steering——队列提升 + 配额重置 + 三处边界检查点 + 前端解禁输入）。
- 2026-09-23：登记全部实施变更（上表）。关键时序决策：`open_stream` 移至 meta 事件**之前**——meta 到达前端即解禁输入，若 open 在 meta 后存在投递 409 竞态窗口。
- 2026-09-23：验证通过——tests/services/test_steering_service.py 5 passed + tests/agents/test_orchestrator.py 36 passed（41 项含存量）；前端 typecheck 通过；后端重启加载 ADR-045，`scripts/verify_steer_endpoint.py` 实测无活跃流返回 409（code=100003）✅。路线图 P0-P5 全部收官。
