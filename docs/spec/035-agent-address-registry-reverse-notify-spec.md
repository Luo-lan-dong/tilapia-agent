# Agent 地址上报与中枢重启主动反连 Spec（agent-address-registry + reverse-notify）

> 关联决策：[ADR-035](../adr/035-agent-address-registry-reverse-notify.md)
> Harness：[035-agent-address-registry-reverse-notify-harness.md](./035-agent-address-registry-reverse-notify-harness.md)
> Changes：[035-agent-address-registry-reverse-notify-changes.md](./035-agent-address-registry-reverse-notify-changes.md)

## 1. 功能描述

分两步消除「中枢重启/换端口后 Agent 重连慢」：

1. **第一步（并行探测）**：Agent 候选端点注册探测由串行改为并行竞速，
   单次超时 5s → 1s，重连最坏时间从分钟级压到 ~10s。
2. **第二步（地址上报 + 反连）**：安装时固化 Agent 静态 IP（用户确认），
   Agent 首次注册上报自身 API 地址与认证 Token；中枢入库；中枢重启后
   **主动反连** Agent 侧带认证的通知 API，告知新中枢地址，Agent 立即重连
   ——消除探测等待。

## 2. 用户故事

- US-1：作为运维者，中枢重启换端口后，所有在线 Agent 在 ~10s 内恢复连接
  （第一步）。
- US-2：作为运维者，安装 Agent 时按提示固化静态 IP；Agent 地址自动上报
  中枢（第二步）。
- US-3：中枢重启后，中枢主动通知各 Agent 新地址，Agent 秒级重连，
  无需等待探测轮转（第二步）。

## 3. 核心流程

### 3.1 第一步：并行探测（Agent 端）

```
重连触发（断线/周期）
  │
  ▼
生成候选端点环（锁定端点 → 首选 → 8000-8010）
  │
  ▼
并行发起注册探测（asyncio 竞速，单次超时 1s）
  │
  ▼
首个成功者 → 锁定为当前端点 → 建立 WS → 正常运行
（全部失败 → 等 RECONNECT_INTERVAL_SECONDS 后重试整环）
```

### 3.2 第二步：地址上报 + 反连

```
【安装阶段】
安装脚本：显示 IP/掩码/网关 → y 固化静态 IP / n 手动输入
  → 防火墙放行 AGENT_API_PORT（失败输出手动命令）
  → 生成 API_TOKEN 落盘配置
  │
【注册上报】
Agent 首次 register：payload.reported_address = {ip, api_port, api_token}
  → 中枢 agent_nodes 落库（reported_ip / reported_api_port / api_token）
  │
【中枢重启反连】
中枢 lifespan 启动完成
  → 查询有 reported 地址且非 online 的节点（并发上限 5）
  → POST https://{reported_ip}:{port}/internal/hub-notify
     Authorization: Bearer <api_token>
     body: {hub_public_url, hub_ws_url}
  → Agent 校验 token → 更新候选首选 + 立即重连
  → 失败静默（Agent 轮转兜底）
```

## 4. 协议变更

### 4.1 `agent.register`（上行，payload 扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| `reported_address` | object 可选 | `{ip, api_port, api_token}`；api_token 仅首次注册落库 |

### 4.2 REST 端点（Agent 侧，新增）

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/internal/hub-notify` | POST | Bearer api_token | 中枢通知新地址，Agent 立即重连 |
| `/internal/health` | GET | 无 | 健康检查（中枢反连前探活） |

### 4.3 数据库

`agent_nodes` 表新增：

| 字段 | 类型 | 说明 |
|------|------|------|
| reported_ip | VARCHAR(64) NULL | Agent 自报地址 |
| reported_api_port | INTEGER NULL | Agent 通知 API 端口 |
| api_token | VARCHAR(128) NULL | Agent 通知 API 认证 Token（仅首次注册落库） |

### 4.4 配置项

| 配置 | 默认 | 说明 |
|------|------|------|
| `AGENT_API_PORT` | 18080 | Agent 通知 API 监听端口 |
| `API_TOKEN` | （安装生成） | 通知 API 认证 Token |
| `REVERSE_NOTIFY_CONCURRENCY` | 5 | 中枢反连并发上限 |
| `_PROBE_TIMEOUT_SECONDS` | 1（原 5） | 注册探测超时（代码常量） |

## 5. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 旧 Agent 不上报地址 | 注册表列为空，中枢跳过反连，行为同现状 |
| Agent 防火墙未放行端口 | 反连失败静默；Agent 串行/并行轮转兜底 |
| api_token 不匹配 | notify 返回 401，中枢记录告警日志（疑似节点漂移/仿冒） |
| 中枢地址未变化 | 反连跳过（对比上次通知记录），零开销 |
| 并行探测全部失败 | 等待重连间隔后重试整环（现状语义） |

## 6. 验收标准

- [ ] 候选端口并行探测：中枢换端口后 Agent 重连 ≤ 10s。
- [ ] 安装脚本完成静态 IP 固化交互（y/n）与防火墙放行（或输出手动命令）。
- [ ] Agent 首次注册后 agent_nodes 落库 reported_address；重复注册不覆盖 api_token。
- [ ] 中枢重启后，中枢主动调用各 Agent notify API（日志可查），Agent 秒级重连。
- [ ] token 错误时 notify 返回 401 且中枢有告警日志。
- [ ] 旧 Agent（无上报）行为同现状；新增迁移可回滚。

## 7. 后续迭代（本期不做）

- Agent 通知 API 的 HTTPS 支持；
- 中枢主动反连的 Web 管理界面（手动触发按钮）。

## 8. 关联文档

- [ADR-035](../adr/035-agent-address-registry-reverse-notify.md)
- [ADR-031](../adr/031-hub-port-avoidance-and-agent-self-heal.md)（候选端点环复用）
- [ADR-020](../adr/020-agent-node-reconnection.md)（重连与幂等注册）
