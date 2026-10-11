# Agent 节点通信协议模块 Spec

## 1. 功能描述

定义中央调度中枢与 Agent 节点之间的所有通信接口和消息格式，包括：

- 节点注册与认证。
- 定时心跳上报。
- 任务下发与确认。
- 任务进度实时上报。
- 任务结果/异常回传。

所有通信仅发生在中枢与节点之间，节点之间禁止直接通信。

## 2. 用户故事

- 作为 Agent 节点，我开机后应自动向中枢注册并上报能力。
- 作为中枢，我能通过心跳判断节点是否在线。
- 作为中枢，我能向指定节点下发任务并接收执行反馈。
- 作为用户，我能在前端实时看到 Agent 执行进度。

## 3. 核心流程

### 3.1 节点安装、注册与心跳

```
新设备准备
    ↓
从中枢获取安装脚本（按操作系统区分）
    ↓
执行安装脚本：下载 OPC Agent 模板 + 安装第三方依赖
    ↓
安装完成，自动生成 .env 配置（含中枢地址、注册 Token）
    ↓
启动 Agent 程序
    ↓
POST /api/v1/agent-nodes/register（携带 Token 和能力信息）
    ↓
WebSocket 连接建立
    ↓
每 5~10 秒发送 heartbeat 事件
    ↓
中枢更新节点状态
```

### 3.2 任务下发与执行

```
中枢选择目标节点
    ↓
WebSocket 发送 hub.task_dispatch 事件
    ↓
节点回复 task_accepted 或 task_rejected
    ↓
节点执行任务
    ↓
节点持续上报 agent.task_progress
    ↓
任务完成 → 上传交付物 → 上报 agent.task_result
```

### 3.3 断线重连与任务对账（ADR-020）

任一方离线后连接必须能自动恢复；节点断线不退出进程：

```
连接断开（中枢重启 / 网络抖动 / 半开连接 keepalive 检测约 40s）
    ↓
节点不退出进程，等待 RECONNECT_INTERVAL_SECONDS（默认 5s）
    ↓
重新 POST /register（按 client_id 幂等，原 token 仍有效）→ 重建 WebSocket
    ↓
重连成功：
  1. 按序补报断线期间缓冲的 task_accepted / task_completed / task_failed
     （中枢归属校验拒绝已改派/已终态任务的过期补报）
  2. 恢复状态上报：执行中任务上报 busy + current_task_id，否则转 idle
    ↓
中枢心跳对账（心跳携带 current_task_id 时）：
  - 任务正常归属该节点      → 无需处理
  - 任务 pending 且未改派   → 恢复归属（置 running），保留断线期间的工作成果
  - 任务已改派他人 / 已终态 → 推送 hub.task_cancel，节点本地 interrupt 中断
  - 任务不存在              → 推送 hub.task_cancel 通知节点
```

## 4. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 注册 Token 无效 | 返回 401，节点无法接入 |
| WebSocket 连接断开 | 节点应用内自动重连（不退进程），按 `RECONNECT_INTERVAL_SECONDS`（默认 5s）间隔重试直至恢复（ADR-020）；进程自身崩溃由 systemd `Restart=always`（`StartLimitIntervalSec=0`）兜底拉起 |
| 心跳超时（30 秒） | 中枢标记节点离线，其 assigned/running 任务重置为 pending；节点重连后按 §3.3 对账恢复归属或取消 |
| 任务下发无响应 | 10 秒后重新下发，3 次无响应则迁移 |
| 节点拒绝任务 | 中枢重新匹配其他节点 |
| 节点上报格式错误 | 中枢记录告警，要求节点重传 |
| 断线期间任务终态上报丢失 | 节点缓冲 task_accepted/completed/failed（上限 100 条），重连后补报；过期补报由中枢归属校验安全拒绝 |
| 节点执行的任务已被改派 | 中枢推送 `hub.task_cancel`，节点本地中断执行 |

## 5. 验收标准

- [ ] Agent 节点启动后 5 秒内完成注册和 WebSocket 连接。
- [ ] 中枢 30 秒内未收到心跳即标记节点离线。
- [ ] 任务下发后节点在 10 秒内回复确认。
- [ ] 进度上报实时显示在前端，延迟 ≤ 3 秒。
- [ ] 任务完成后交付物路径正确回传。
- [ ] 节点离线后，未完成任务能自动迁移。
- [ ] 中枢离线再恢复后，节点在 `RECONNECT_INTERVAL_SECONDS` 间隔内自动重连成功，进程不退出（ADR-020）。
- [ ] 节点断线超过 30 秒后重连：执行中任务若未被改派则恢复归属；已改派则节点收到 `hub.task_cancel` 并本地中断。
- [ ] 断线期间完成的任务，重连后补报 `agent.task_completed` 且中枢归属校验生效。

## 6. 新增接口：安装引导

### 6.1 获取 Agent 安装脚本地址

中枢提供各类型、各操作系统的安装脚本下载入口。

```http
GET /api/v1/agent-nodes/install-scripts?agent_type=programming&os=linux
```

响应：

```json
{
  "code": 0,
  "data": {
    "agent_type": "programming",
    "os": "linux",
    "script_url": "http://192.168.101.101:8000/static/install-scripts/install-agent-programming-linux.sh",
    "checksum": "sha256:xxxxxxxx",
    "version": "0.1.0"
  }
}
```

### 6.2 安装脚本执行流程

1. 节点设备下载对应操作系统的安装脚本。
2. 校验脚本 SHA256 一致性。
3. 执行脚本：
   - 创建节点工作目录。
   - 从中枢下载 OPC Agent 模板。
   - 安装第三方依赖（Python、FFmpeg、GPU 驱动等，按脚本内置命令）。
   - 生成 `.env` 文件，写入中枢地址和注册 Token。
4. 启动 Agent 服务，自动调用注册接口。

## 7. 关联文档

- [项目设计文档](../OPC超级个体%20·%20AI员工智能体协作平台｜项目设计文档（正式版）.md)
- [ADR-004 节点通信协议选型](../adr/004-communication-protocol.md)
- [ADR-006 Agent 节点部署与接入方案](../adr/006-agent-node-deployment.md)
- [api-standards.md](../standards/api-standards.md)
- [agent-node-standards.md](../standards/agent-node-standards.md)
- [004-agent-node-protocol-harness.md](./004-agent-node-protocol-harness.md)
