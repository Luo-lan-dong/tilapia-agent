# 节点管理与 Linux 编程 Agent MVP Harness

## 1. 测试策略

| 层级 | 目标 | 工具 |
|------|------|------|
| 单元测试 | Agent 配置解析、注册 payload 生成、心跳事件格式 | pytest |
| 集成测试 | 安装脚本信息接口、节点详情接口、注册 Token 重置接口 | pytest + TestClient |
| 契约测试 | WebSocket 注册/心跳/任务下发事件格式 | pytest-asyncio + websockets |
| 端到端测试 | 真实 Linux 环境执行安装命令后节点上线 | 手动 |

## 2. 单元测试清单

- [ ] `config.py` 能从 `.env` 正确读取 `HUB_API_URL`、`AGENT_REGISTRATION_TOKEN`、`AGENT_CLIENT_ID`。
- [ ] `hub_client.py` 生成的注册请求体包含 `name`、`agent_type`、`capabilities`。
- [ ] `hub_client.py` 生成的心跳事件格式符合协议规范。
- [ ] 占位任务执行器收到 `hub.task_dispatch` 后回复 `task_accepted` 和 `task_completed`。

## 3. 集成测试清单

- [ ] `GET /api/v1/agent-nodes/install-scripts?agent_type=programming&os=linux` 返回正确的脚本 URL、checksum、版本。
- [ ] `GET /api/v1/agent-nodes/{node_id}` 返回节点详情，包含状态、最后心跳时间。
- [ ] `POST /api/v1/agent-nodes/{node_id}/token` 返回新的 `registration_token`。
- [ ] 安装脚本静态文件可通过 `/static/install-scripts/...` 访问。

## 4. 契约测试清单

- [ ] Agent 注册后 WebSocket 连接建立成功。
- [ ] Agent 每 5 秒发送 `agent.heartbeat`。
- [ ] 中枢发送 `hub.task_dispatch` 后，Agent 回复 `task_accepted`。
- [ ] Agent 随后发送 `agent.task_progress` 和 `agent.task_result` / `task_completed`。

## 5. E2E 测试场景

### 场景 1：Linux 安装并上线

1. 在前端 `/nodes` 页面获取 Linux 安装命令。
2. 在一台 Linux 机器（或 WSL）执行安装命令。
3. 安装完成后执行 `systemctl status opc-agent-programming` 查看服务状态为 active。
4. 返回前端，30 秒内看到新节点状态为 `idle`。
5. 重启机器，确认节点自动恢复为 `idle`。

### 场景 2：离线判定

1. 停止 Agent 服务 `systemctl stop opc-agent-programming`。
2. 等待 30 秒后，前端显示该节点状态为 `offline`。
3. 重新启动服务，节点恢复为 `idle`。

### 场景 3：任务下发占位

1. 创建一个类型为 `programming` 的任务。
2. 中枢将任务分配给在线的 Linux 编程 Agent。
3. Agent 日志显示收到任务并占位完成。
4. 中枢将任务状态更新为 `completed`，进度 100%。

## 6. CI 门禁

- [ ] `ruff` 代码风格检查通过。
- [ ] `pytest` 单元测试 + 集成测试全部通过。
- [ ] `python scripts/check_file_size.py` 文件长度检查通过。
- [ ] 前端 `npm run typecheck` 和 `npm run lint` 通过。

## 7. 关联文档

- [008-node-management-and-programming-agent-linux-spec.md](./008-node-management-and-programming-agent-linux-spec.md)
- [ADR-008 Agent 节点 Linux 编程 MVP](../adr/008-agent-node-linux-programming-mvp.md)
