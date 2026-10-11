# Agent 节点真实系统资源监控 Harness

## 1. 测试目标

验证 Agent 节点真实系统资源监控全链路的正确性、稳定性与兼容性，确保：

- Agent 端能正确采集并上报监控数据。
- 中枢能正确接收、解析、持久化监控数据。
- 前端能正确展示节点负载与监控详情。
- 旧版 Agent 不上报 metrics 时系统不报错。

## 2. 测试金字塔

| 层级 | 覆盖率目标 | 测试类型 |
|------|-----------|---------|
| 单元测试 | 核心采集/计算函数 100% 覆盖 | Agent 端 metrics 采集与 score 计算 |
| 集成测试 | 接口与数据库交互覆盖 | 中枢 heartbeat 处理、metrics 查询 API |
| 契约测试 | 前后端接口契约 | `/metrics` 响应结构、心跳 payload 结构 |
| E2E 测试 | 1 条核心 happy path | 从 Agent 上报到前端展示完整链路 |

## 3. 单元测试清单

### 3.1 Agent 端采集器

文件：`opc-agent-programming/tests/test_metrics_collector.py`（新增）

| 用例 | 输入 | 期望输出 |
|------|------|---------|
| 正常采集 CPU | 运行中系统 | `cpu.percent` 在 0~100 之间，`count_logical` ≥ `count_physical` ≥ 1 |
| 正常采集内存 | 运行中系统 | `memory.total_gb` > 0，`memory.percent` 在 0~100 之间 |
| 正常采集磁盘 | 运行中系统 | `disk.total_gb` > 0，`disk.percent` 在 0~100 之间 |
| 正常采集网络 | 运行中系统 | `network.bytes_sent` / `network.bytes_recv` ≥ 0 |
| 综合 score 计算 | 固定模拟指标 | 按权重公式计算，结果保留 1 位小数 |
| level 分级 | score 分别为 20/50/70/90 | 分别为 idle/normal/high/critical |
| 无 psutil 时回退 | 导入失败 | 采集函数返回 None，不抛异常 |

## 4. 集成测试清单

### 4.1 中枢 heartbeat 处理

文件：`opc-central-dispatch-hub/tests/services/test_node_heartbeat.py`（扩展）

| 用例 | 步骤 | 期望结果 |
|------|------|---------|
| 心跳带 metrics 时更新快照 | 构造心跳 payload 含 metrics，调用 `_handle_heartbeat` | `agent_nodes.metrics_snapshot` 非空，`load` 等于 score |
| 心跳无 metrics 时不覆盖旧快照 | 先写入快照，再发送无 metrics 心跳 | `metrics_snapshot` 保持不变，`load` 不变 |
| 心跳 load 与 metrics 同时存在时优先 metrics | payload 中 `load=80`，`metrics.load_score.score=20` | `load` 字段更新为 20 |
| 无效 metrics 格式不中断心跳 | payload 中 metrics 为字符串 | 记录 WARNING，心跳时间戳仍更新 |

### 4.2 中枢 metrics 查询 API

文件：`opc-central-dispatch-hub/tests/api/v1/test_agent_nodes.py`（扩展）

| 用例 | 步骤 | 期望结果 |
|------|------|---------|
| 查询在线节点 metrics | 创建节点并写入 metrics_snapshot，调用 GET /metrics | 返回 200，`is_online=true`，`metrics` 与写入一致 |
| 查询无快照节点 | 创建节点但不写快照，调用 GET /metrics | 返回 200，`metrics=null` |
| 查询不存在节点 | 调用 GET /metrics/{不存在的 uuid} | 返回 404，错误码 202001 |

## 5. 契约测试清单

| 检查项 | 验证方式 |
|--------|---------|
| `agent.heartbeat` payload 可包含 `metrics` 字段 | 中枢 `HeartbeatPayload` schema 校验 |
| `/metrics` 响应结构符合 `AgentNodeMetricsResponse` | FastAPI response_model 校验 |
| `metrics_snapshot` JSONB 结构与 Agent 上报结构一致 | Pydantic 模型双向序列化验证 |

## 6. E2E 测试清单

### 6.1 完整监控链路

前置条件：中枢已启动，至少一个 Agent 节点在线并上报 metrics。

| 步骤 | 操作 | 期望结果 |
|------|------|---------|
| 1 | 打开前端 `/nodes` | 列表页在线节点 load 列显示真实百分比 |
| 2 | 点击某节点「查看详情」 | Drawer 打开，显示基础信息 |
| 3 | 点击「查看监控详情」 | 二级页面打开，显示 CPU / 内存 / 磁盘 / 网络 / load average |
| 4 | 等待 5~10 秒 | 监控数值刷新，与节点真实负载趋势一致 |
| 5 | 停止 Agent 服务 | 节点状态变为 offline，监控详情页仍显示最后快照并标注离线 |

## 7. 回归测试清单

| 模块 | 回归点 |
|------|--------|
| 节点管理 | 节点注册、列表查询、Token 重置、安装脚本获取不受影响 |
| 任务调度 | 节点 busy/idle 状态切换、任务下发流程不受影响 |
| WebSocket | 旧版 Agent 心跳（无 metrics）仍能正常维持连接 |
| 前端 | Nodes 页面、NodeDetailDrawer 原有功能正常 |

## 8. CI 门禁

- [x] `opc-central-dispatch-hub` 后端测试全部通过：`pytest`
- [x] `opc-agent-programming` 单元测试全部通过：`pytest`
- [x] `opc-web-console` 前端构建成功：`npm run build`
- [x] 实际链路验证：至少一个在线 Agent 节点上报真实 metrics，前端 Modal 可展示完整指标
- [x] ruff / eslint 无新增错误

## 9. 关联文档

- [ADR-014 Agent 节点真实系统资源监控](../adr/014-agent-node-metrics.md)
- [014-agent-node-metrics-spec.md](./014-agent-node-metrics-spec.md)
