# Agent 节点通信协议模块 Harness

## 1. 测试金字塔

```
        /\
       /  \
      / E2E \          少量：真实 Agent 节点上下线与任务执行
     /--------\
    /  集成测试  \       中量：WebSocket 连接、心跳、任务下发
   /--------------\
  /    单元测试     \      大量：消息解析、状态更新、超时判定
 /------------------\
```

## 2. 单元测试清单

| 测试对象 | 测试场景 | 通过标准 |
|---------|---------|---------|
| 注册请求解析 | 合法注册请求 | 正确提取 client_id、agent_type、capabilities |
| 注册请求解析 | 缺少必填字段 | 返回 400 错误 |
| 心跳更新 | 收到心跳 | 节点状态更新为在线，last_heartbeat_at 刷新 |
| 心跳超时判定 | 超过 30 秒未收到心跳 | 节点标记为离线 |
| 任务下发消息构造 | 构造 dispatch 事件 | JSON 格式符合规范 |
| 进度消息解析 | 解析 progress 事件 | progress_percent 在 0~100 之间 |
| 结果消息解析 | 解析 result 事件 | deliverables 路径格式正确 |

## 3. 集成测试清单

| 测试对象 | 测试场景 | 通过标准 |
|---------|---------|---------|
| 注册 + WebSocket 连接 | Agent 注册后建立 WS | 中枢识别节点在线 |
| 心跳维持 | 连续心跳 1 分钟 | 节点状态保持在线 |
| 任务下发 + 确认 | 中枢下发任务 | 节点收到并回复 accepted |
| 进度推送链路 | 节点上报 → 中枢 → 前端 | 前端收到 progress 事件 |
| 节点断线重连 | 模拟网络闪断 | 节点自动重连并恢复心跳 |
| 任务结果回传 | 节点上报 result | 中枢更新任务状态为完成 |

## 4. 契约测试清单

| 测试对象 | 测试场景 | 通过标准 |
|---------|---------|---------|
| 注册接口 | POST /api/v1/agent-nodes/register | 响应格式符合 api-standards |
| 心跳事件 | agent.heartbeat | payload 含 client_id、status、current_task_id |
| 任务下发事件 | hub.task_dispatch | payload 含 task_id、input、timeout_seconds |
| 进度事件 | agent.task_progress | payload 含 task_id、progress_percent、stage |
| 结果事件 | agent.task_result | payload 含 task_id、status、deliverables |

## 5. E2E 测试场景

### 场景 1：节点完整生命周期

1. 启动一个编程 Agent 节点。
2. 节点自动注册并建立 WebSocket。
3. 中枢前端显示节点在线且空闲。
4. 停止 Agent 进程。
5. 30 秒后中枢显示节点离线。
6. 重新启动 Agent。
7. 节点恢复在线状态。

### 场景 2：任务下发与执行反馈

1. 中枢创建「开发官网」任务。
2. 任务分配给在线编程 Agent。
3. Agent 收到任务并回复 accepted。
4. Agent 持续上报进度（0% → 50% → 100%）。
5. Agent 完成并上报结果，包含交付物路径。
6. 中枢任务状态变为 completed。

## 6. CI 门禁

- 单元测试覆盖率 ≥ 70%。
- WebSocket 契约测试全部通过。
- 消息格式 JSON Schema 校验通过。
- ruff / black / mypy 检查通过。

## 7. 关联文档

- [004-agent-node-protocol-spec.md](./004-agent-node-protocol-spec.md)
