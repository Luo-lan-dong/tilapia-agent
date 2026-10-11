# OPC 节点显示名自定义 + Agent 类型更名为 `code` · 业务规格说明

> 本文档配套 ADR-016，描述本特性在用户故事、核心流程、降级链路、验收标准层面的业务行为。
> 技术实现细节见 ADR-016 与 [agent-node-standards.md](../standards/agent-node-standards.md)。

## 1. 背景

* 节点列表里多个节点因为 hostname 都是 `localhost.localdomain` 而显示相同名称（`localhost.localdomain-programming-j1169i` × 3），运维无法一眼区分。

* Agent 实际能力是「写代码」，业务名 `programming` 与「后台可管理的类型 code」语义错位。

* 安装后无法二次修改显示名。

## 2. 用户故事

### US-1 安装阶段自定义节点显示名

作为 **运维**，我希望在「安装新节点」弹窗里能填一个节点显示名（如 `iMac-代码-001`），这样装好后节点在列表里一眼可辨；如果我不填，系统按操作系统平台+版本自动命名（如 `Ubuntu 22.04`）。

### US-2 中枢二次编辑节点显示名

作为 **运维**，我希望在节点详情抽屉里能直接修改显示名（比如发现 OS 兜底名不直观），改完立刻在列表里看到新名字。

### US-3 类型从 `programming` 迁移为 `code`

作为 **开发**，我希望 `agent_nodes.agent_type` 列里的值统一是 `code`，这样：

* 后台未来做「Agent 类型管理」时字段命名直接是 `code`。

* WS 上行 / 下行事件里的 `agent_type` 字段值与 `code` 业务语义一致。

### US-4 接口可观测性

作为 **运维**，我希望所有修改节点显示名的接口调用都有结构化日志，包含 `event` / `node_id` / 入参 / 成功/失败原因，方便链路追踪。

## 3. 核心流程

### 3.1 安装阶段自定义显示名（US-1）

```
┌─────────────┐  GET /install-scripts?agent_type=code&os=linux
│ 前端 弹窗    │ ─────────────────────────────────────────────────►
│ InstallCmd  │ ◄── InstallScriptResponse { script_url, hub_url, version }
│ Modal       │
│             │  用户填写：hub_url (auto) / token / nodeName (可选) / agent_type=code / os=linux
│             │
│             │  拼接 install 命令
│             │  curl -fsSL <script_url> | sudo bash -s -- \
│             │    --hub-url <hub_url> \
│             │    --token <token> \
│             │    [--agent-name "<nodeName>"]   ← 本特性新增
│             │
│ 运维 复制粘贴 │  在 Linux 终端执行
└──────┬──────┘
       ▼
┌─────────────────────┐
│ install 脚本执行     │
│  parse_args --agent-name
│  write_env_file 写入 AGENT_NAME=<nodeName>（留空则不写）
└──────┬──────────────┘
       ▼
┌─────────────────────┐
│ Agent 启动           │
│  AGENT_NAME 存在 → 用之
│  否则 _generate_name() → OS 平台+版本
└──────┬──────────────┘
       ▼
┌─────────────────────┐
│ Agent 走 WebSocket   │
│  Register 事件上报   │
│  name = 上述值       │
└─────────────────────┘
```

### 3.2 中枢二次编辑显示名（US-2）

```
┌─────────────────────┐
│ 前端 NodeDetailDrawer│
│  节点名称 改为可编辑  │
│  文本 → <Input>      │
│  「保存」按钮         │
└──────┬──────────────┘
       ▼ 用户点击保存
       ▼  PATCH /api/v1/agent-nodes/{node_id}
       ▼  body: { "name": "iMac-代码-001" }
       ▼
┌──────────────────────────────────────────┐
│ 中枢后端                                  │
│  1) 校验 name 非空 / 1-128 字符            │
│  2) 校验 (agent_type, name) 唯一           │
│  3) UPDATE agent_nodes SET name=:n WHERE id=:id
│  4) 记录日志                              │
│     node_name_update_started              │
│     node_name_updated (old/new)           │
│  5) 返回 AgentNodeResponse                │
└──────┬───────────────────────────────────┘
       ▼
┌─────────────────────┐
│ 前端                 │
│  setNode(detail)     │
│  message.success()   │
│  列表自动刷新 (5s 轮询)│
└─────────────────────┘
```

### 3.3 类型值迁移（US-3）

```
┌──────────────────────────────────────────┐
│ Alembic 迁移脚本                           │
│  1) 备份可能冲突的 (agent_type, name)      │
│  2) 冲突行改名: -dup1 / -dup2              │
│  3) UPDATE agent_nodes                   │
│     SET agent_type='code'                 │
│     WHERE agent_type='programming'        │
│  4) 创建 partial unique index             │
│     ux_agent_nodes_agent_type_name        │
└──────────────────────────────────────────┘
```

## 4. 降级链路

| 失败场景                                                   | 降级行为                                                   | 日志事件                                                   |
| ------------------------------------------------------ | ------------------------------------------------------ | ------------------------------------------------------ |
| Agent 端 `platform.freedesktop_os_release()` 解析失败       | 回退 `platform.platform()`                               | `agent_name_os_fallback`（DEBUG）                        |
| 再失败（如纯 Windows 容器无 os-release）                         | 回退 `socket.gethostname()`，**不再追加** **`-programming-`** | `agent_name_hostname_fallback`（DEBUG）                  |
| 中枢 PATCH 时 `name` 与同 `agent_type` 下其它节点重名              | 返回 `202010` 业务错误，前端弹错误，不入库                             | `node_name_update_rejected`（WARNING，reason=duplicate）  |
| 中枢 PATCH 时 `name` 长度越界 / 空白                            | 返回 `202011` 业务错误，前端弹错误                                 | `node_name_update_rejected`（WARNING，reason=invalid）    |
| 中枢 PATCH 节点不存在                                         | 沿用 `AgentNotFoundError` → 404                          | `node_name_update_rejected`（WARNING，reason=not\_found） |
| 中枢 PATCH 数据库异常                                         | 500，事务回滚，前端展示通用错误                                      | `node_name_update_failed`（ERROR，exc\_info）             |
| Alembic 迁移发现存量 `(agent_type, name)` 重复                 | 自动加 `-dupN` 后缀，记录每条改名                                  | `agent_type_legacy_dup_renamed`（WARNING）               |
| Agent 端 `AGENT_TYPE` 仍是 `programming`（旧版本）重连中枢         | 中枢记录 `agent_type_unexpected` WARNING，不拒绝连接             | `agent_type_unexpected`（WARNING）                       |
| InstallCommandModal 拼装 `--agent-name` 含特殊字符（空格、引号、`$`） | 拼接时用双引号包裹 + 转义；不传非法字符                                  | 前端 `install_command_build_warn`（DEBUG）                 |

## 5. 接口契约

### 5.1 PATCH /api/v1/agent-nodes/{node\_id}

**Request**：

```http
PATCH /api/v1/agent-nodes/3f7bb90f-05eb-49cc-8661-9d9e81b6d69c
Content-Type: application/json

{ "name": "iMac-代码-001" }
```

**Response 200**：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "id": "3f7bb90f-05eb-49cc-8661-9d9e81b6d69c",
    "name": "iMac-代码-001",
    "agent_type": "code",
    "status": "idle",
    "load": 0.0,
    "registration_token": "...",
    "last_heartbeat_at": "2026-08-26T12:00:00Z",
    "created_at": "2026-08-18T01:28:32Z",
    "updated_at": "2026-08-26T12:34:56Z"
  },
  "request_id": "req_xxxxxxxx"
}
```

**Response 422（业务错误）**：

```json
{
  "code": 202010,
  "message": "节点名称在同 agent_type 下重复: code / iMac-代码-001",
  "data": null,
  "request_id": "req_xxxxxxxx"
}
```

**Response 404**：

```json
{
  "code": 202001,
  "message": "Agent 节点不存在: <node_id>",
  "data": null,
  "request_id": "req_xxxxxxxx"
}
```

### 5.2 Agent `_generate_name()` 输出格式

| 环境                         | 输出示例                                     |
| -------------------------- | ---------------------------------------- |
| Ubuntu 22.04 LTS           | `Ubuntu 22.04`                           |
| CentOS 7.9                 | `CentOS 7.9`                             |
| macOS 14.4                 | `macOS 14.4`                             |
| Windows 11 Pro             | `Windows 11`                             |
| Alpine 3.18（无 VERSION\_ID） | `Alpine`                                 |
| 容器化 / 无 os-release         | `Linux 5.15.0-...`（platform.platform 兜底） |
| 极端失败                       | `unknown`                                |

### 5.3 InstallCommandModal 拼装的 install 命令

留空 `nodeName` 时：

```bash
curl -fsSL <script_url> | sudo bash -s -- --hub-url <hub> --token <token>
```

填写 `nodeName = "iMac-代码-001"` 时：

```bash
curl -fsSL <script_url> | sudo bash -s -- --hub-url <hub> --token <token> --agent-name "iMac-代码-001"
```

> **注意**：`buildInstallCommand` 必须用单引号 + 字符转义，避免 shell 解析错误。当前 Agent `parse_args` 使用 bash 参数解析，引号会被 bash 剥除。中文 / 空格 / `$` 字符需要保证传入时已被前端双引号包住。

## 6. 验收标准

| 编号    | 验收项                                                                                                                           | 期望 |
| ----- | ----------------------------------------------------------------------------------------------------------------------------- | -- |
| AC-1  | 安装时填 `--agent-name "iMac-代码-001"`，Agent `.env` 写入 `AGENT_NAME="iMac-代码-001"`                                                  | ✔  |
| AC-2  | 安装时不填 `--agent-name`，Agent `.env` 无 `AGENT_NAME=` 行                                                                           | ✔  |
| AC-3  | Agent 启动时 `_generate_name()` 在 Ubuntu 22.04 上输出 `Ubuntu 22.04`                                                                | ✔  |
| AC-4  | Agent 启动时 `_generate_name()` 在 macOS 14.4 上输出 `macOS 14.4`                                                                    | ✔  |
| AC-5  | PATCH 改 name 成功后，列表页 5s 内轮询看到新 name                                                                                           | ✔  |
| AC-6  | PATCH 改 name 时传 `""` 或 `"   "`，返回 422 / `202011`                                                                              | ✔  |
| AC-7  | PATCH 改 name 与同 agent\_type 现有 name 重复，返回 422 / `202010`                                                                      | ✔  |
| AC-8  | PATCH 改 name 不会修改 `client_id` / `agent_type` / `status` 等其他字段                                                                 | ✔  |
| AC-9  | Alembic 迁移完成后，存量 `agent_type='programming'` 行数 = 0                                                                            | ✔  |
| AC-10 | Alembic 迁移完成后，`ux_agent_nodes_agent_type_name` 索引存在                                                                           | ✔  |
| AC-11 | InstallCommandModal 输入 `nodeName="iMac-代码-001"`，拼出命令含 `--agent-name "iMac-代码-001"`                                            | ✔  |
| AC-12 | NodeDetailDrawer 改 name 成功后抽屉内显示新值，列表 5s 内同步                                                                                  | ✔  |
| AC-13 | 涉及接口按 logging-standards 输出 4 个事件：`xxx_started` / `xxx_completed` 或 `xxx_updated` / `xxx_rejected`（仅拒绝路径）/ `xxx_failed`（仅异常路径） | ✔  |
| AC-14 | 旧版 Agent 上报 `agent_type='programming'` 时仍能注册，仅记录 WARNING                                                                      | ✔  |
| AC-15 | ruff check + pytest 全部通过；前端 `npm run typecheck` 通过                                                                            | ✔  |
| AC-16 | 端到端 E2E：在 3 台 hostname 相同的 Linux 容器上装 Agent，分别 PATCH 成不同 name，列表正确展示                                                          | ✔  |

## 7. 关联文档

* [ADR-016](../adr/016-agent-node-display-name-and-code-type.md)

* [016-agent-node-display-name-and-code-type-harness.md](./016-agent-node-display-name-and-code-type-harness.md)

* [004-agent-node-protocol-spec.md](./004-agent-node-protocol-spec.md)

* [008-node-management-and-programming-agent-linux-spec.md](./008-node-management-and-programming-agent-linux-spec.md)

* 相关规范：[agent-node-standards.md](../standards/agent-node-standards.md) / [api-standards.md](../standards/api-standards.md) / [database-standards.md](../standards/database-standards.md) / [backend-component-standards.md](../standards/backend-component-standards.md) / [frontend-component-standards.md](../standards/frontend-component-standards.md) / [logging-standards.md](../standards/logging-standards.md)

