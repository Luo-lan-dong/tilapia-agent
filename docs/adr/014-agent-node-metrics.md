# ADR-014：Agent 节点真实系统资源监控

## 状态

Accepted

## 日期

2026-08-18

## 问题背景

当前前端节点管理页面的「负载」列显示的是固定占位值（3% 或 —），并非 Agent 节点真实系统负载。随着节点数量增加，用户无法判断：

1. 哪些节点 CPU / 内存使用率过高。
2. 哪些节点磁盘即将写满。
3. 哪些节点网络 IO 异常。

因此需要为 Agent 节点引入真实的系统资源采集与上报能力，并在前端节点详情中展示完整监控指标。

## 决策

### 1. 采集方式：复用 psutil，集成到 Agent 心跳链路

参考现有 `opc-agent-programming/linux-python/services/monitor.py` 的实现，在 Agent 节点 `app/client/hub_client.py` 中新增系统资源采集器，基于 `psutil` 采集：

- CPU：总使用率、每核使用率、物理/逻辑核心数。
- 内存：总量、已用、可用、使用百分比；swap 总量/已用/百分比。
- 磁盘：根分区总量/已用/可用/使用百分比。
- 网络：累计收发字节/包数、MB 数。
- 综合负载：加权计算 CPU、内存、磁盘 IO、磁盘空间、系统 load average、网络速率的综合得分。
- 系统 load average：1m / 5m / 15m。

### 2. 上报方式：扩展 `agent.heartbeat` payload

在现有心跳事件 `agent.heartbeat` 的 payload 中新增可选 `metrics` 字段，复用现有 WebSocket 长连接，不新增独立事件或连接。理由：

- 心跳频率 5 秒，足以支撑监控刷新。
- 复用通道减少连接数和实现复杂度。
- 与现有 `status`/`current_task_id` 天然对齐。

心跳 payload 示例：

```json
{
  "event": "agent.heartbeat",
  "payload": {
    "client_id": "agent-programming-001",
    "status": "idle",
    "current_task_id": null,
    "load": 12.5,
    "metrics": {
      "cpu": { "percent": 15.2, "count_physical": 4, "count_logical": 8, "per_core": [...] },
      "memory": { "total_gb": 31.2, "used_gb": 8.5, "available_gb": 22.1, "percent": 27.3, "swap_total_gb": 2.0, "swap_used_gb": 0.0, "swap_percent": 0.0 },
      "disk": { "total_gb": 100.0, "used_gb": 45.2, "free_gb": 54.8, "percent": 45.2 },
      "network": { "bytes_sent": 123456, "bytes_recv": 654321, "mb_sent": 0.12, "mb_recv": 0.62 },
      "load_score": { "score": 12.5, "level": "idle", "breakdown": {...}, "load_average": {"1m": 0.5, "5m": 0.6, "15m": 0.4} },
      "timestamp": "2026-08-18T12:00:00Z"
    }
  },
  "timestamp": "2026-08-18T12:00:00Z"
}
```

### 3. 存储方式：`agent_nodes` 表新增 `metrics_snapshot` JSONB 字段

在中央调度中枢 `agent_nodes` 表中新增 `metrics_snapshot` 字段（`jsonb`），保存节点最近一次上报的完整监控快照。列表页和详情页均从此字段读取。

同时复用已有 `load` 字段，将 `metrics.load_score.score` 写入该字段，保持列表页接口不变。

MVP 阶段不保留历史时序数据；后续如需趋势图，可再引入独立 `agent_node_metrics_history` 表或外部时序数据库。

### 4. 查询方式：新增 `GET /api/v1/agent-nodes/{node_id}/metrics`

前端通过该接口获取节点最新监控详情，返回 `metrics_snapshot` 中的结构化数据。节点离线时仍返回最后一次快照，但需标注 `is_online` 与 `last_heartbeat_at`。

### 5. 前端展示：节点详情二级页面

在现有 `NodeDetailDrawer` 中增加「查看监控详情」入口，点击后进入二级展示页面（全屏 Drawer / Modal），显示：

- 综合负载与等级。
- CPU 使用率与每核使用率。
- 内存使用率与容量。
- 磁盘使用率与容量。
- 网络收发总量。
- 系统 load average。
- 数据时间戳。

### 6. 打包与分发

Agent 模板版本升级到 `0.4.0`：

- `pyproject.toml` 增加 `psutil` 依赖。
- `app/utils/monitor.py` 新增系统资源采集器。
- `app/client/hub_client.py` 在心跳 payload 中携带 `metrics` 与 `load_score`。
- 按 [BUILD.md](../../opc-agent-programming/tilacode/BUILD.md) 重新打包 `opc-agent-programming-0.4.0.tar.gz`。
- tilacode 二进制无变更，保持 `1.18.13`。

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| A（本决策）：扩展心跳 payload + JSONB 快照 | 复用现有通道；实现简单；列表页无接口变更 | 心跳 payload 增大；无历史趋势 | ✅ 选中 |
| B：新增独立 `agent.metrics` 事件 | payload 与心跳解耦；可单独调整频率 | 需新增事件处理逻辑；前端多订阅一路 | ❌ 否决 |
| C：新建 `agent_node_metrics` 历史表 | 可查询趋势 | MVP 不需要；增加存储与清理成本 | ⚠️ 延后 |
| D：Agent 直接暴露 HTTP `/metrics` 由中枢拉取 | 中枢可控采样频率 | 增加 Agent 攻击面；需处理防火墙/NAT | ❌ 否决 |
| E：使用 Prometheus / Node Exporter | 生态成熟 | 引入额外组件；MVP 范围过大 | ⚠️ 延后 |

## 决策原因

1. **改动最小**：复用心跳通道与 `agent_nodes` 表，避免新增连接、表、组件。
2. **实时性足够**：5 秒心跳间隔满足管理控制台实时刷新需求。
3. **与现有 UI 兼容**：`load` 字段继续复用，列表页无需改动即可显示真实负载。
4. **Agent 依赖可控**：仅需增加 `psutil`，主流 Linux 发行版均有预编译 wheel，安装稳定。
5. **保留演进空间**：后续可平滑切换到独立事件（方案 B）或历史表（方案 C），无需破坏现有协议。

## 影响

### 正面影响

- 前端负载显示由虚假占位值变为真实系统负载。
- 用户可在节点详情查看 CPU、内存、磁盘、网络、load average 等完整指标。
- 为后续任务调度基于负载做智能分发打下基础。

### 负面影响 / trade-off

- 单次心跳 payload 从约 100B 增加到 1~3KB；在节点数量大时略有带宽增加，MVP 阶段可忽略。
- `metrics_snapshot` 为 JSONB，无法直接对内部字段做 SQL 索引；后续若需按负载排序/筛选，需再设计。

### 需要做的变更

- 中枢后端：
  - `agent_nodes` 表新增 `metrics_snapshot` 字段（Alembic 迁移）。
  - `AgentNode` 模型、`AgentNodeResponse` schema 更新。
  - `ws_messages.py` 扩展 `HeartbeatPayload`。
  - `_handle_heartbeat` 解析并持久化 `metrics` 与 `load`。
  - 新增 `GET /api/v1/agent-nodes/{node_id}/metrics`。
- Agent 模板：
  - `pyproject.toml` 增加 `psutil`。
  - `app/client/hub_client.py` 增加 `metrics` 采集与心跳上报。
- 前端：
  - `AgentNode` 类型扩展 `metrics_snapshot`。
  - 新增监控详情 API 与二级展示组件。
- 文档：
  - 更新 `004-agent-node-protocol-spec.md` 心跳事件说明。
  - 更新 `008-node-management-and-programming-agent-linux-spec.md` 接口清单。

## 关联文档

- Spec：[014-agent-node-metrics-spec.md](../spec/014-agent-node-metrics-spec.md)
- Harness：[014-agent-node-metrics-harness.md](../spec/014-agent-node-metrics-harness.md)
- 相关规范：[api-standards.md](../standards/api-standards.md)、[agent-node-standards.md](../standards/agent-node-standards.md)、[database-standards.md](../standards/database-standards.md)、[logging-standards.md](../standards/logging-standards.md)
- 参考实现：[opc-agent-programming/linux-python/services/monitor.py](../../opc-agent-programming/linux-python/services/monitor.py)
- 打包指南：[BUILD.md](../../opc-agent-programming/tilacode/BUILD.md)

## 实际实施情况

- 已完成 Agent 端 `app/utils/monitor.py` 采集器与 `app/client/hub_client.py` 心跳上报，`metrics` 字段包含 CPU、内存、磁盘、网络、load_average 与综合 load_score。
- 已完成中枢 `agent_nodes.metrics_snapshot` JSONB 字段（Alembic 迁移 `20260818002321_add_metrics_snapshot_to_agent_nodes.py`）与 `GET /api/v1/agent-nodes/{node_id}/metrics` 接口。
- 已完成前端 `NodeDetailDrawer`「查看监控详情」入口与 `NodeMetricsModal` 二级展示页面。
- 已按 BUILD.md 打包 `opc-agent-programming-0.4.0.tar.gz` 并更新 manifest。
- 实际链路验证：在线 Agent 节点 `3f7bb90f-05eb-49cc-8661-9d9e81b6d69c` 成功上报真实 Linux 系统指标，`agent_nodes.load` 与 `metrics_snapshot` 均已更新，前端 Modal 可正确展示所有监控项。
- 回归验证：`tests/api/v1/test_agent_nodes.py` 与 `tests/services/test_node_heartbeat.py` 共 25 项测试通过；前端 `npm run typecheck` 通过，修改文件 eslint 无新增错误。

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-18 | 初始创建 | AI助手 |
