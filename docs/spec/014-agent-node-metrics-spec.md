# Agent 节点真实系统资源监控 Spec

## 1. 功能描述

为 OPC 中央调度中枢提供 Agent 节点真实系统资源监控能力：

- Agent 节点周期性采集 CPU、内存、磁盘、网络、系统 load average 等指标。
- 通过现有 WebSocket 心跳通道将监控快照上报到中枢。
- 中枢持久化最新快照，并在前端节点管理页面展示综合负载与详细监控指标。

本 Spec 覆盖 Agent 采集端、中枢接收/存储/查询端、前端展示端的全链路。

## 2. 用户故事

- 作为运维人员，我可以在节点列表看到每个 Agent 节点的真实负载，而非虚假占位值。
- 作为运维人员，我可以点击某个节点查看详情，并进入二级页面查看 CPU、内存、磁盘、网络、load average 等完整监控指标。
- 作为调度器开发者，我可以基于节点负载做后续任务分发优化。

## 3. 核心流程

### 3.1 监控数据采集与上报

```
Agent 节点启动
    ↓
注册到中枢 / 建立 WebSocket
    ↓
每 5 秒调用 psutil 采集系统资源
    ↓
组装心跳 payload（含 status / current_task_id / load / metrics）
    ↓
WebSocket 发送 agent.heartbeat
    ↓
中枢解析并持久化到 agent_nodes.metrics_snapshot 与 load 字段
```

### 3.2 前端查看监控详情

```
用户进入 /nodes 页面
    ↓
前端轮询 GET /api/v1/agent-nodes
    ↓
列表显示真实 load（来自 agent_nodes.load）
    ↓
用户点击「查看详情」
    ↓
NodeDetailDrawer 展示基础信息
    ↓
用户点击「查看监控详情」
    ↓
GET /api/v1/agent-nodes/{node_id}/metrics
    ↓
二级页面展示 CPU / 内存 / 磁盘 / 网络 / load average
```

## 4. 数据模型

### 4.1 metrics_snapshot 字段结构

存储在 `agent_nodes.metrics_snapshot`（JSONB）中，结构如下：

```json
{
  "cpu": {
    "percent": 15.2,
    "count_physical": 4,
    "count_logical": 8,
    "per_core": [10.0, 20.0, 15.0, 5.0, 18.0, 12.0, 22.0, 8.0]
  },
  "memory": {
    "total_gb": 31.2,
    "used_gb": 8.5,
    "available_gb": 22.1,
    "percent": 27.3,
    "swap_total_gb": 2.0,
    "swap_used_gb": 0.0,
    "swap_percent": 0.0
  },
  "disk": {
    "total_gb": 100.0,
    "used_gb": 45.2,
    "free_gb": 54.8,
    "percent": 45.2
  },
  "network": {
    "bytes_sent": 123456,
    "bytes_recv": 654321,
    "packets_sent": 1234,
    "packets_recv": 5678,
    "mb_sent": 0.12,
    "mb_recv": 0.62
  },
  "load_score": {
    "score": 12.5,
    "level": "idle",
    "weights": {
      "cpu": 0.35,
      "memory": 0.25,
      "disk_io": 0.15,
      "disk_space": 0.10,
      "load_avg": 0.10,
      "network": 0.05
    },
    "breakdown": {
      "cpu": { "score": 15.2, "weight": 0.35 },
      "memory": { "score": 27.3, "weight": 0.25 },
      "disk_io": { "score": 0.5, "weight": 0.15 },
      "disk_space": { "score": 45.2, "weight": 0.10 },
      "load_avg": { "score": 6.25, "weight": 0.10 },
      "network": { "score": 0.1, "weight": 0.05 }
    },
    "load_average": {
      "1m": 0.5,
      "5m": 0.6,
      "15m": 0.4
    }
  },
  "timestamp": "2026-08-18T12:00:00Z"
}
```

### 4.2 综合负载 score 计算规则

参考 `opc-agent-programming/linux-python/services/monitor.py` 的 `get_load()` 实现：

| 指标 | 权重 | 说明 |
|------|------|------|
| CPU 使用率 | 0.35 | `psutil.cpu_percent(interval=None)` |
| 内存使用率 | 0.25 | `psutil.virtual_memory().percent` |
| 磁盘 IO | 0.15 | 基于两次采样的 `disk_io_counters` 读写速率换算为百分比 |
| 磁盘空间 | 0.10 | `psutil.disk_usage('/').percent` |
| 系统负载 | 0.10 | `load1 / cpu_count * 100`，上限 100 |
| 网络 | 0.05 | 基于两次采样的 `net_io_counters` 收发速率换算为百分比 |

`score = Σ(score_i * weight_i)`，保留 1 位小数。

`level` 分级：

| score 范围 | level |
|-----------|-------|
| ≤ 30 | idle |
| ≤ 60 | normal |
| ≤ 80 | high |
| ≤ 100 | critical |

## 5. 接口清单

### 5.1 扩展心跳事件

事件名：`agent.heartbeat`

扩展后的 payload：

```json
{
  "client_id": "agent-programming-001",
  "status": "idle",
  "current_task_id": null,
  "load": 12.5,
  "metrics": { ... }
}
```

- `metrics` 为可选字段，旧版 Agent 不上报时不影响心跳处理。
- `load` 为可选字段；中枢优先使用 `metrics.load_score.score`，否则使用 payload 中的 `load`，fallback 为 0.0。

### 5.2 查询节点监控详情

```http
GET /api/v1/agent-nodes/{node_id}/metrics
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "node_id": "uuid",
    "is_online": true,
    "last_heartbeat_at": "2026-08-18T12:00:00Z",
    "metrics": { ... }
  },
  "request_id": "req_xxxxxxxx"
}
```

- 节点存在但无 metrics 快照时，`metrics` 为 `null`。
- 节点不存在时返回 404，错误码 `202001`。

## 6. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| Agent 未安装 psutil | 心跳不上报 metrics，列表页 load 显示 0%，详情页提示未采集 |
| 采集过程抛异常 | Agent 捕获并记录 ERROR 日志，本次心跳不上报 metrics，不中断连接 |
| 中枢解析 metrics 失败 | 记录 WARNING 日志，忽略本次 metrics，不影响心跳时间戳更新 |
| 节点离线 | 详情页仍展示最后一次快照，并标注离线状态与最后心跳时间 |
| JSONB 字段过大 | 对 metrics_snapshot 大小做上限校验，超限则丢弃并告警 |

## 7. 验收标准

- [x] Agent 节点心跳中包含真实的 `metrics` 数据。
- [x] 中枢 `agent_nodes.metrics_snapshot` 字段在心跳到达后更新。
- [x] 节点列表页 load 列显示真实综合负载 score。
- [x] 节点详情页可查看基础信息，并进入二级监控详情页。
- [x] 监控详情页展示 CPU、内存、磁盘、网络、load average。
- [x] 旧版 Agent（不上报 metrics）仍可正常注册和心跳，不报错。
- [x] Agent 模板 tarball 升级到 0.4.0，安装后自动具备监控采集能力。

## 8. 关联文档

- [ADR-014 Agent 节点真实系统资源监控](../adr/014-agent-node-metrics.md)
- [014-agent-node-metrics-harness.md](./014-agent-node-metrics-harness.md)
- [004-agent-node-protocol-spec.md](./004-agent-node-protocol-spec.md)
- [008-node-management-and-programming-agent-linux-spec.md](./008-node-management-and-programming-agent-linux-spec.md)
- [api-standards.md](../standards/api-standards.md)
