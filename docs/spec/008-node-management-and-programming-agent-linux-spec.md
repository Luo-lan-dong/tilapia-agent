# 节点管理与 Linux 编程 Agent MVP Spec

## 1. 功能描述

本模块包含两部分：

1. **前端节点管理页面**：让用户查看已注册的 Agent 节点、获取 Linux 安装命令、复制注册 Token。
2. **Linux 编程 Agent 模板（opc-agent-programming）**：一条命令完成安装，安装后自动注册到中枢并持续心跳，MVP 阶段不执行真实编程任务。安装脚本自动检测发行版（Debian/Ubuntu、RHEL/CentOS/Fedora、Arch、Alpine 等），安装前检查 Python、venv、pip、curl、tar 等依赖，已存在且版本满足要求则跳过。

## 2. 用户故事

- 作为用户，我可以在前端看到所有已注册节点的状态和最后心跳时间。
- 作为用户，我可以获取 Linux 编程 Agent 的一键安装命令，并在新机器上执行。
- 作为运维，安装完成后 Agent 自动加入系统自启，重启机器后仍能自动上线。
- 作为中枢，我能实时感知哪些 Agent 节点在线、离线或繁忙。

## 3. 核心流程

### 3.1 新节点接入

```
用户在前端点击「安装新节点」→ 选择 Linux
    ↓
前端调用 GET /api/v1/agent-nodes/install-scripts?agent_type=programming&os=linux
    ↓
中枢返回脚本下载地址、checksum、版本
    ↓
前端组装一键安装命令并展示
    ↓
用户在 Linux 机器执行命令
    ↓
脚本检测 Linux 发行版、按需安装缺失依赖（Python >= 3.10、venv、pip、curl、tar），优先选择 python3.11/python3.10 等满足版本要求的解释器、下载 OPC Agent 模板、生成 .env、注册 systemd 服务
    ↓
服务启动 → 调用 POST /api/v1/agent-nodes/register
    ↓
建立 WebSocket 连接 → 每 5 秒发送 heartbeat
    ↓
中枢标记节点为 idle，前端列表刷新后显示在线
```

### 3.2 节点心跳与状态

- 节点每 5 秒通过 WebSocket 发送 `agent.heartbeat` 事件。
- 中枢 30 秒未收到心跳则标记为 `offline`。
- 节点被分配任务后标记为 `busy`，任务释放后恢复 `idle`。

### 3.3 任务下发占位（MVP）

- 中枢向节点发送 `hub.task_dispatch`。
- 节点回复 `task_accepted`，打印日志，随后立即上报 `task_completed`（不真正执行）。
- 该行为仅用于验证通信链路，真实执行器后续替换。

## 4. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 安装脚本下载失败 | 提示用户检查网络，重试安装命令 |
| 注册 Token 无效 | 安装脚本退出并打印错误，服务启动失败 |
| WebSocket 断开 | 节点自动重连，重试 5 次后进入离线状态 |
| 依赖安装失败 | 脚本显式报错并退出，不继续后续步骤；无法识别发行版时提示手动安装依赖 |

## 5. 验收标准

- [ ] 前端 `/nodes` 页面可展示节点列表、状态、最后心跳时间。
- [ ] 前端可获取 Linux 编程 Agent 一键安装命令。
- [ ] 在 Linux 机器执行安装命令后，节点自动注册并显示为 `idle`。
- [ ] 安装脚本在 Debian/Ubuntu、RHEL/CentOS/Fedora 等主流发行版上可自动选择包管理器；Python 等依赖已存在且版本满足要求时跳过安装；RHEL 等默认 Python 为 3.9 的系统可自动选择 python3.11/3.10。
- [ ] 重启 Linux 机器后，Agent 自动启动并恢复心跳。
- [ ] 停止 Agent 服务 30 秒后，中枢将节点标记为 `offline`。
- [ ] 任务下发后节点回复 `task_accepted` 和 `task_completed`（占位执行）。

## 6. 接口清单

### 6.1 获取安装脚本信息

```http
GET /api/v1/agent-nodes/install-scripts?agent_type=programming&os=linux
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "agent_type": "programming",
    "os": "linux",
    "script_url": "http://192.168.101.101:8000/static/install-scripts/install-agent-programming-linux.sh",
    "checksum": "sha256:xxxxxxxx",
    "version": "0.1.0"
  },
  "request_id": "req_xxxxxxxx"
}
```

### 6.2 节点详情

```http
GET /api/v1/agent-nodes/{node_id}
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "id": "uuid",
    "name": "agent-programming-001",
    "agent_type": "programming",
    "status": "idle",
    "capabilities": ["react", "fastapi"],
    "load": 0,
    "last_heartbeat_at": "2026-08-04T12:00:00Z",
    "created_at": "2026-08-04T12:00:00Z",
    "updated_at": "2026-08-04T12:00:00Z"
  },
  "request_id": "req_xxxxxxxx"
}
```

### 6.3 重新生成注册 Token

```http
POST /api/v1/agent-nodes/{node_id}/token
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "registration_token": "xxxxxxxx"
  },
  "request_id": "req_xxxxxxxx"
}
```

## 7. 项目结构

### 7.1 中枢后端

新增/修改：

- `app/api/v1/endpoints/agent_nodes.py`：新增详情、安装脚本、Token 重置身口
- `static/install-scripts/install-agent-programming-linux.sh`：Linux 安装脚本
- `static/agent-templates/programming/0.1.0/`：Agent 模板压缩包

### 7.2 Agent 模板

```
opc-agent-programming/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   └── client/
│       ├── __init__.py
│       └── hub_client.py
├── scripts/
│   └── install-agent-programming-linux.sh
├── systemd/
│   └── opc-agent-programming.service
├── pyproject.toml
├── .env.example
└── README.md
```

### 7.3 前端

- `src/pages/Nodes.tsx`：节点管理页面
- `src/components/NodeDetailDrawer.tsx`：节点详情抽屉
- `src/components/InstallCommandModal.tsx`：安装命令弹窗
- `src/api/agentNodes.ts`：补充详情、安装脚本、Token 重置接口

## 8. 关联文档

- [ADR-008 Agent 节点 Linux 编程 MVP](../adr/008-agent-node-linux-programming-mvp.md)
- [004-agent-node-protocol-spec.md](./004-agent-node-protocol-spec.md)
- [agent-node-standards.md](../standards/agent-node-standards.md)
- [api-standards.md](../standards/api-standards.md)
