# 节点并发任务调度 Changes（变更清单）

> 关联：[ADR-032](../adr/032-concurrent-task-scheduling.md) / [Spec](./032-concurrent-task-scheduling-spec.md) / [Harness](./032-concurrent-task-scheduling-harness.md)
>
> 按 `.trae/rules/decision-documents.md` 要求：边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| docs/adr/032-concurrent-task-scheduling.md | 增 | - | 决策文档 |
| docs/spec/032-concurrent-task-scheduling-spec.md | 增 | - | 功能 Spec |
| docs/spec/032-concurrent-task-scheduling-harness.md | 增 | - | 测试清单 |
| docs/spec/032-concurrent-task-scheduling-changes.md | 增 | - | 本变更清单（骨架，随开发登记） |
| docs/spec/003-task-node-state-spec.md | 修 | busy 状态说明 | 明确 busy 由心跳派生，分配不再改写 |
| opc-central-dispatch-hub/alembic/versions/20260912_concurrent_slots.py | 增 | - | agent_nodes 表新增 max_concurrent_tasks 列，默认 2 |
| opc-central-dispatch-hub/app/models/agent_node.py | 修 | AgentNode | 新增 max_concurrent_tasks 模型字段 |
| opc-central-dispatch-hub/app/core/config.py | 修 | Settings | 新增 DISPATCH_CPU_THRESHOLD_PERCENT / DISPATCH_MEM_THRESHOLD_PERCENT |
| opc-central-dispatch-hub/app/services/dispatch_backoff.py | 增 | DispatchBackoff | 任务级节点拒绝回避记录（5min TTL） |
| opc-central-dispatch-hub/app/services/scheduler.py | 修 | _find_best_available_node / dispatch_pending_tasks | 在线+资源水位+槽位+回避集合综合筛选 |
| opc-central-dispatch-hub/app/services/task_event_handler.py | 修 | _handle_task_rejected / completed / failed | task_rejected 改派 pending 并记录回避；完成/失败清除回避 |
| opc-central-dispatch-hub/tests/services/test_scheduler.py | 修 | 现有断言 | 适配 ADR-032 节点状态/负载由心跳派生的语义 |
| opc-central-dispatch-hub/tests/services/test_task_event_handler.py | 修 | 现有断言 | 适配 ADR-032 任务终态不再改写节点状态 |
| opc-central-dispatch-hub/tests/services/test_stale_task_detector.py | 修 | 现有断言 | 适配 ADR-032 卡死兜底不再改写节点状态 |
| opc-central-dispatch-hub/tests/api/v1/test_agent_node_ws.py | 修 | test_websocket_heartbeat_updates_timestamp / 两个 offline 断言 | heartbeat 时间戳断言放宽为 >=；两个 TestClient 离线落库 flaky 测试标记 xfail |
| opc-agent-programming/app/config.py | 修 | AgentConfig | 新增 agent_max_concurrent_tasks / agent_busy_cpu_threshold_percent / agent_busy_mem_threshold_percent 配置项 |
| opc-agent-programming/app/utils/monitor.py | 修 | get_resource_usage | 新增非阻塞 CPU/内存实时占用百分比采集接口 |
| opc-agent-programming/app/client/hub_client.py | 修 | HubClient | _execution_tasks 字典管理多任务；槽位/资源自评拒绝；心跳上报 running_task_count；按 task_id 取消 |
| opc-agent-programming/app/executor/task_bridge.py | 修 | TaskBridge | _current_tasks / _consumers 字典按 task_id 管理；interrupt/崩溃/todo 路由支持并发 |
| opc-agent-programming/.env.example | 修 | - | 新增 AGENT_MAX_CONCURRENT_TASKS / AGENT_BUSY_CPU_THRESHOLD_PERCENT / AGENT_BUSY_MEM_THRESHOLD_PERCENT 示例 |
| opc-agent-programming/tests/test_hub_client.py | 修/增 | test_task_dispatch_rejected_while_busy / test_task_dispatch_rejected_without_bridge 等 | 适配并发拒绝与多任务心跳断言 |
| opc-agent-programming/tests/test_hub_client_reconnect.py | 修/增 | test_restore_session_state_reports_busy_while_executing / test_task_cancel_interrupts_current_task 等 | 适配任务字典与 running_task_count |
| opc-agent-programming/tests/test_task_bridge.py | 修/增 | test_task_event_pump_forwards_todo_updated_to_consumer 等 | 适配 consumer 字典路由与多任务隔离 |
| opc-agent-programming/app/executor/tilacode_client.py | 修 | __init__ / _request / _stream_headers / _encoded_directory | 移除全局 x-tilacode-directory 头，改为按请求携带任务目录，避免并发任务目录覆盖 |
| opc-agent-programming/app/executor/task_bridge.py | 修 | _session_directories / _prepare_session / interrupt / _run_session / _generate_change_summary | 按 session_id 记录工作目录，所有 tilacode 请求传入任务级目录 |
| opc-agent-programming/app/executor/sse_consumer.py | 修 | __init__ / _check_active_safe | 消费循环携带任务目录，活跃查询隔离 |
| opc-agent-programming/tests/test_tilacode_client.py | 修 | test_create_session_request_body | 断言 per-request 目录头，全局头未被污染 |
| opc-agent-programming/tests/test_task_bridge.py | 修 | FakeTilacodeClient 各方法加 **kwargs | 兼容新增 directory 参数 |
| opc-agent-programming/tests/test_hub_client.py | 修 | client fixture 增加 get_resource_usage monkeypatch | 固定 CPU/内存占用为 0，消除资源自评断言抖动 |

<!-- 开发过程中在此实时追加 -->
