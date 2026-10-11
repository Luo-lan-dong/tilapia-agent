# Agent 节点断线重连与任务对账 · 变更清单（Changes）

> 关联决策：[ADR-020-agent-node-reconnection.md](../adr/020-agent-node-reconnection.md)
>
> 按 `.trae/rules/decision-documents.md` 要求边开发边实时登记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 | 日期 |
|------|---------|--------------|---------|------|
| `opc-agent-programming/app/config.py` | 修 | `AgentConfig` 新增 `reconnect_interval_seconds` 字段（default=5, ge=1） | 断线重连重试间隔走 Pydantic Settings，防止误配为 0 | 2026-09-03 |
| `opc-agent-programming/app/client/hub_client.py` | 修 | 新增 `run_forever()`（应用内重连主循环）；新增 `restore_session_state()`（重连后状态恢复+补报）；`handle_messages()` 断线改为正常返回（删除进程退出逻辑）；`_send_event()` 断线/发送失败时缓冲生命周期事件；新增 `_build_ws_message()` / `_buffer_event()` / `_flush_pending_events()` / `_handle_task_cancel()`；`__init__` 新增 `_pending_events` 缓冲队列；模块头与事件常量更新（`hub.task_cancel`、缓冲事件集） | 核心重连实现：断线不退进程、每轮重新注册+建连、协程清理防泄漏、事件缓冲补报、取消指令处理 | 2026-09-03 |
| `opc-agent-programming/app/client/hub_client.py` | 修 | `register()` 超时改用 `_REGISTER_TIMEOUT_SECONDS` 常量 | 修复硬编码 30.0 与常量不一致的异味 | 2026-09-03 |
| `opc-agent-programming/app/main.py` | 修 | `_run_agent()` 重构：连接会话循环先启动、tilacode 并行就绪、`bridge_ready` 事件联动 `on_session_ready` 回调上报 idle；模块 docstring 更新 | 连接生命周期与 tilacode 启动解耦，桥接未就绪时派发被既有检查拒绝 | 2026-09-03 |
| `opc-central-dispatch-hub/app/schemas/ws_messages.py` | 修 | 新增常量 `HUB_TASK_CANCEL = "hub.task_cancel"` | 新增下行取消指令事件名 | 2026-09-03 |
| `opc-central-dispatch-hub/app/services/agent_node_ws_service.py` | 修 | `_handle_heartbeat()` 心跳处理末尾新增任务对账触发；新增 `_reconcile_reported_task()`（pending 未改派 → 恢复归属置 running；已改派/终态/不存在 → 推送 `hub.task_cancel`）；`_mark_node_online()` 移除无条件 `agent_reconnected` 日志（由端点层分支记录） | 重连任务对账：保留断线期间工作成果、防重复执行；顺带修复重连日志重复/语义失真问题 | 2026-09-03 |
| `opc-agent-programming/systemd/opc-agent-programming.service` | 修 | `[Unit]` 段新增 `StartLimitIntervalSec=0` | 防止中枢长时间离线时注册快速失败触发 systemd 启动限流后永久放弃 | 2026-09-03 |
| `opc-agent-programming/.env.example` | 修 | 新增 `RECONNECT_INTERVAL_SECONDS=5` 配置模板 | 配置项文档化 | 2026-09-03 |
| `opc-agent-programming/tests/test_hub_client_reconnect.py` | 增 | 新增 8 项测试：断线自动重连/注册失败重试/close 终止/事件缓冲补报/补报中断线再缓冲/状态恢复 busy 与 idle/task_cancel 中断与忽略 | 覆盖 ADR-020 全部核心行为 | 2026-09-03 |
| `docs/adr/020-agent-node-reconnection.md` | 增 | ADR-020 全文 | 决策记录 | 2026-09-03 |
| `docs/spec/004-agent-node-protocol-spec.md` | 修 | 新增 §3.3 断线重连与任务对账流程；§4 降级链路更新；§5 验收标准补 3 项 | 协议 Spec 同步 | 2026-09-03 |
| `docs/standards/api-standards.md` | 修 | §4.2 中枢 → Agent 事件表新增 `hub.task_cancel` | 接口规范同步 | 2026-09-03 |
| `docs/INDEX.md` | 修 | 1.2/1.3/六 登记本次决策 | 文档索引登记 | 2026-09-03 |

## 验证记录

| 验证项 | 结果 |
|-------|------|
| Agent 全量测试（跳过缺 respx 依赖的 test_tilacode_client.py） | 72 passed（64 存量 + 8 新增），无回归 |
| Hub 侧模块导入检查 | `ws_messages.HUB_TASK_CANCEL`、`agent_node_ws_service._reconcile_reported_task` 导入正常，无循环导入 |
| Agent `app.main` 导入与新配置项 | 正常，`reconnect_interval_seconds=5` |
| Hub 全量测试 | ⚠️ 环境不可用：依赖远程 PostgreSQL（192.168.101.101:5432）当前网络不可达，183 个 error 均为 DB 连接错误，非本次改动导致；待网络恢复后需补跑 |

## 变更记录

| 日期 | 说明 |
|------|------|
| 2026-09-03 | 创建骨架，关联 ADR-020 |
| 2026-09-03 | 实施完成，登记全部文件/函数级变更与验证记录 |
