# ADR-016：节点显示名可自定义 + Agent 类型更名为 `code`

## 状态

Accepted

## 日期

2026-08-26

## 问题背景

2026-08-26 巡检发现两个节点身份相关问题：

1. **节点显示名不可控**：`InstallCommandModal`（「安装新节点」弹窗）只能让用户填注册 Token，无法在安装时声明节点的展示名。Agent 端 `config.py` 的 `_generate_name()` 默认用 `{hostname}-programming-{6位随机串}` 兜底，**当多台机器 hostname 相同（如** **`localhost.localdomain`）时，前端列表会同时出现多条完全相同的「节点名称」**（见用户截图 `localhost.localdomain-programming-j1169i` × 3），运维难以区分是哪台机器的哪个进程。
2. **Agent 类型与业务语义错位**：`agent_nodes.agent_type` 当前存 `programming`，但该 Agent 的实际能力是「**code** 写代码」（详见 ADR-012 tilacode 集成），与 `programming` 这一概念存在语义偏差。后续计划在后台做「Agent 类型管理」时（用户已确认），用 `code` 作为稳定的机器可读 code 字段更符合「后台 CRUD 类型」的设计语言。
3. **节点显示名在安装后无法二次编辑**：前端 `NodeDetailDrawer` 把 `name` 当只读字段，用户无法在运维过程中给节点重命名（区别于 Agent 端 `.env` 里的 `AGENT_NAME`，后者需要重装或重跑 install 脚本才能改）。

### 需求

1. 安装阶段可声明节点显示名，留空时按操作系统平台+版本兜底（例 `Ubuntu 22.04` / `CentOS 7.9` / `macOS 14.4` / `Windows 11`），不再用 `{hostname}-programming-随机串` 旧规则。
2. 中枢 `Nodes.tsx` / `NodeDetailDrawer` 允许二次编辑节点显示名（**仅** **`name`** **字段**；`agent_type` / `client_id` 在中枢仍为只读，避免破坏调度路由）。
3. `agent_type` 数据库值由 `programming` 迁移为 `code`，**保留列名** **`agent_type`** **不变**，便于后续后台类型管理直接编辑。
4. 涉及改动的所有 REST / 安装命令生成 / 节点注册 / Agent 启动路径按 `logging-standards.md` 加结构化日志（含 `event`、上下文字段、失败原因）。

## 决策

### 1. 安装阶段声明节点显示名：复用现有 `AGENT_NAME`

* **Agent 端**：`opc-agent-programming/app/config.py` 早已支持 `AGENT_NAME`（见 `agent_name: str | None`），无需改 Pydantic 模型。

* **install 脚本**：`opc-agent-programming/scripts/install-agent-programming-linux.sh` 新增 `--agent-name <NAME>` 参数，写到 `.env` 的 `AGENT_NAME` 行；与现有 `--hub-url` / `--token` 并列，留空则**不**写入 `.env`，由 Agent 启动时按新兜底规则生成。

* **中枢静态副本**：`opc-central-dispatch-hub/static/install-scripts/install-agent-programming-linux.sh`（与 Agent 端脚本同步）同步更新。

* **前端** `InstallCommandModal.tsx`：

  * 新增「节点名称」`Input` 字段（默认空）。

  * 「一键安装命令」拼接逻辑新增 `--agent-name "<value>"`（仅当 value 非空时）。

  * 拼装函数 `src/utils/installCommand.ts` 扩展参数。

### 2. 兜底命名：操作系统平台+详细版本

* **Agent 端** `_generate_name()` 重写：

  1. 优先 `platform.freedesktop_os_release()`（标准 `os-release` 文件），返回 `{NAME} {VERSION_ID}`，例 `Ubuntu 22.04`、`CentOS 7.9`、`macOS 14.4`、`Windows 11`。
  2. 解析失败回退 `platform.platform()`（`platform.system()` 太粗，舍弃）。
  3. 最终回退 `socket.gethostname()`，但**不再追加** **`-programming-`** **业务前缀**。

* **影响**：旧节点重装后会得到 OS 风格的新名字；已在中枢注册的旧节点 name **不变**（升级不触发 UPDATE）。

* **前端 / 中枢侧**无感知（只读 Agent 自报值）。

### 3. Agent 类型值 `programming` → `code`

* **数据库** `agent_nodes.agent_type` 列**保留**，只 `UPDATE` 现有行：先 `UPDATE ... SET agent_type='code' WHERE agent_type='programming'`，无 Alembic 列变更。

* **后端默认**：所有写死 `'programming'` 的代码（`AgentNodeManagementService` / install 脚本生成的 `.env` 模板 / Pydantic Schema 描述 / 错误码示例）改为 `'code'`。

* **Agent 端** `opc-agent-programming/.env.example` 注释 + `opc-agent-programming/scripts/install-agent-programming-linux.sh` 写入 `.env` 时的默认值改为 `code`。

* **中枢后端** **`ws_messages.py`** **与注册 handler**：

  * 收到 `agent_type != 'code'` 时，记录 `agent_type_unexpected` WARNING 日志但**不拒绝连接**（避免破坏历史旧版 Agent 重连），仅发邮件/通知给运维。

  * 安装命令生成时 `SUPPORTED_INSTALL_SCRIPTS` 集合 `(agent_type, os)` 改为 `('code', 'linux')`。

* **前端** `Nodes.tsx` / `NodeDetailDrawer` 渲染时直接显示 `code` 字符串；类型下拉框选项从 `programming` 改为 `code`（仅展示用，不可编辑）。

### 4. 中枢可二次编辑节点显示名：新增 `PATCH /api/v1/agent-nodes/{node_id}`

* **路径**：`PATCH /api/v1/agent-nodes/{node_id}`（**新建**，不走 `PUT`）。

* **请求体** `AgentNodeUpdateRequest`：

  ```json
  { "name": "iMac-设计-001" }
  ```

  字段**只有** **`name`** **一个**；`agent_type` / `client_id` 不在 PATCH schema 中出现，由 Pydantic 直接屏蔽，避免被改。

* **校验**：

  * `name` 必填，`min_length=1, max_length=128`。

  * **同一** **`agent_type`** **下** **`name`** **唯一**（partial unique index，新增 Alembic 迁移）。

  * 不允许把 `name` 改成空字符串 / 仅空白。

* **响应**：`AgentNodeResponse`（与 `GET /agent-nodes/{id}` 复用同一 schema）。

* **业务错误码**（沿用 `api-standards.md` A-BB-CCC）：

  * `202010`：节点显示名重复（同 agent\_type）。

  * `202011`：节点名称非法（仅空白 / 长度越界）。

* **鉴权（2026-08-26 拍板）**：与现有 `POST /agent-nodes/{id}/token`、`POST /tasks` 一致，**MVP 阶段不引入鉴权**，依赖内网 / Nginx Basic Auth 防护；不引入 `Depends(require_admin_user)`。后续做后台类型管理时再统一叠加 RBAC。

* **拒绝规则**：

  * 节点不存在 → `404 AgentNotFoundError`（沿用）。

  * 节点 `status == 'offline'` 时仍允许改（运维场景）。

  * 节点已被 `assigned_node_id` 关联到 `running` 任务时也允许改（不影响任务执行）。

* **日志**（必填）：

  * `node_name_update_started`（`event` / `node_id` / `user_id?` / `requested_name`）

  * `node_name_updated`（`event` / `node_id` / `old_name` / `new_name`）

  * `node_name_update_rejected`（`event` / `node_id` / `reason: duplicate|invalid|not_found` / `requested_name`）

  * `node_name_update_failed`（`event` / `node_id` / `exc_info=True`）

### 5. `agent_type` 唯一性约束（partial unique index）

新增 Alembic 迁移：

```python
op.create_index(
    "ux_agent_nodes_agent_type_name",
    "agent_nodes",
    ["agent_type", "name"],
    unique=True,
    postgresql_where=text("name IS NOT NULL"),
)
```

* 现有 `agent_nodes.name` 是 `nullable=False` 唯一索引无意义（全局唯一会强制全局不同名），用 partial index 实现「同 agent\_type 下唯一」。

* 迁移需处理存量冲突：先 `SELECT agent_type, name, COUNT(*) ... GROUP BY 1,2 HAVING COUNT(*) > 1`，若存在冲突 → 报错并要求人工加后缀 `-dupN`。

* 迁移脚本里直接 `UPDATE` 把重复行改名（带 `-dup1` 后缀），并把 `programming → code` 的值迁移合并到同一迁移脚本。

### 6. 前端：NodeDetailDrawer 加可编辑 name 字段

* `NodeDetailDrawer` 把 `节点名称` 那一行从 `<Descriptions.Item>{node.name}</Descriptions.Item>` 改成 `<EditableName />`。

* 提交后调 `PATCH /api/v1/agent-nodes/{nodeId}`，成功后更新本地 `node` 状态；失败用 `App.useApp().message.error` 报错。

* 抽屉内「Agent 类型」仍为只读（用户确认：中枢不提供类型编辑）。

* 列表页 `Nodes.tsx` 暂不改行内编辑（仅在抽屉里可改），降低 UI 复杂度。

### 7. 接口日志规范

所有新增 / 修改的接口按 `logging-standards.md` §4 落结构化日志：

* 入口 `xxx_started` 含 `event` / `node_id` / 关键入参。

* 成功 `xxx_completed` / `xxx_updated` 含结果计数。

* 业务拒绝 `xxx_rejected` 含 `reason`。

* 异常 `xxx_failed` 含 `exc_info=True`。

* 中枢侧安装命令生成接口 `get_install_script` 在 hub\_url 解析路径上**已经**有 `install_script_get_started` / `install_script_get_completed` 日志，本次仅在 `agent_type='code'` 路径上确保日志的 `agent_type` 字段输出新值 `code`，不改结构。

## 备选方案

| 方案                                                        | 优势                                        | 劣势                                                                   | 结论   |
| --------------------------------------------------------- | ----------------------------------------- | -------------------------------------------------------------------- | ---- |
| A（本决策）：OS 平台+版本兜底 + PATCH 改 name + `agent_type='code'` 迁移 | 改动局限在配置 / 数据迁移 / PATCH 端点；`client_id` 永不动 | 存量 `programming` 值要 UPDATE                                           | ✅ 选中 |
| B：新增 `display_name` 列与 `name` 解耦                          | 不破坏旧 `name` 兜底逻辑                          | 两列含义重叠，前端要决定展示哪一个；调度需要再改                                             | ❌ 否决 |
| C：把 `agent_type` 列名也改为 `code_type`                        | 字面对齐                                      | 触发 Alembic 列重命名 + 后端模型 / API / 前端类型 / Agent 协议全链路改 key；与本次「仅改值」的目标冲突 | ❌ 否决 |
| D：把 `programming` 改名为 `code` 但**不**走数据迁移，仅依赖 Agent 上报     | 改少量代码                                     | 存量节点仍是 `programming`，新注册变 `code`，前端的 agent\_type 过滤会同时出现两个值          | ❌ 否决 |
| E：OS 兜底用 `platform.system()`（只 Linux/Darwin/Windows）      | 简单                                        | 粒度太粗，仍无法区分 `Ubuntu 22.04` 与 `CentOS 7.9`                             | ❌ 否决 |
| F：列表行内编辑 name                                             | UX 更好                                     | 需要单元格可编辑 + 防误触；用户已同意"抽屉内可编辑"                                         | ❌ 否决 |
| G：PATCH 端点引入 admin token / RBAC 鉴权                         | 防误用                                     | MVP 阶段所有写接口均不鉴权，本次保持一致；后续后台类型管理时再统一加                          | ❌ 否决（2026-08-26 拍板） |

## 决策原因

1. **OS 兜底能解决「同 hostname 多节点」乱象**：运维多机复用同一 hostname（如容器/镜像默认 hostname）时，旧兜底会产生重名；OS 详细版本天然唯一且对运维更可读。
2. **保留** **`agent_type`** **列名**：现有代码（`AgentNode.agent_type` 字段、`AgentNodeRegisterRequest.agent_type`、`ws_messages` 上报字段、前端 `agent_type` 字段）都按列名引用，重命名代价远超「改值」。
3. **PATCH 限** **`name`**：把 `client_id` / `agent_type` 锁死，避免运营误操作破坏调度路由；与 `api-standards.md` PATCH 语义（部分更新）一致。
4. **partial unique index**：PostgreSQL 标准做法，避免把 name 设成全局唯一（这会让多机无法重名）。
5. **结构化日志**：`logging-standards.md` §4 已强制，新增 / 修改接口的日志遗漏会被 lint / 审查阶段拦下。

## 影响

### 对系统架构的影响

* 中枢 `agent_nodes` 表新增 partial unique index（迁移期间需扫描存量冲突）。

* 中枢新增 PATCH 端点与 `AgentNodeUpdateRequest` schema。

* Agent 端 `_generate_name()` 兜底重写，install 脚本加 `--agent-name`。

### 对开发的影响

* **后端**：

  * `app/models/agent_node.py`：`name` 字段增加注释（同 agent\_type 唯一）。

  * `app/schemas/agent_node.py`：新增 `AgentNodeUpdateRequest`。

  * `app/api/v1/endpoints/agent_node_management.py`：新增 PATCH 路由。

  * `app/services/agent_node_management_service.py`：新增 `update_node_name` 方法。

  * 新增 Alembic 迁移 `20260826000000_rename_agent_type_to_code_and_add_unique_name.py`（合并 programming→code 与 partial unique index）。

* **前端**：

  * `src/types/agentNode.ts`：扩展 `AgentNode` 不变（已含 `name` 字段）。

  * `src/api/agentNodes.ts`：新增 `updateAgentNodeName` API client。

  * `src/components/InstallCommandModal.tsx`：新增「节点名称」输入框。

  * `src/utils/installCommand.ts`：扩展 `buildInstallCommand` 支持 `--agent-name`。

  * `src/components/NodeDetailDrawer.tsx`：将节点名称从只读改为可编辑表单。

* **Agent 端**：

  * `opc-agent-programming/app/config.py`：`_generate_name()` 重写。

  * `opc-agent-programming/.env.example`：`AGENT_TYPE=programming` → `AGENT_TYPE=code`。

  * `opc-agent-programming/scripts/install-agent-programming-linux.sh`：

    * `parse_args` 支持 `--agent-name`。

    * `write_env_file` 写入 `AGENT_NAME` 与 `AGENT_TYPE=code`。

    * `SUPPORTED_INSTALL_SCRIPTS`（中枢侧）改为 `('code', 'linux')`。

* **中枢静态副本** `opc-central-dispatch-hub/static/install-scripts/install-agent-programming-linux.sh`：与 Agent 端脚本同步。

* **前端常量**：Hub 侧 `agentNodes` API 调用时也需更新默认 `agent_type='code'`。

### 对安全的影响

* `registration_token` 仍按 ADR-006 / ADR-008 规则由中枢管理，PATCH 不涉及。

* PATCH 端点暂不强制鉴权（与现有 `POST /agent-nodes/{id}/token` 一致：MVP 阶段假设仅内网可达）；生产部署可后续叠加 `Depends(require_admin_user)`。

### 对运维的影响

* 升级后**存量节点**的 name 不变（Agent 不会主动 UPDATE 自己的 name）。

* 升级后**存量节点**的 agent\_type 自动从 `programming` 变 `code`（迁移脚本一次性 UPDATE）。

* 升级后**新装节点**：显示名 = 用户在弹窗填的名称（留空则 OS 兜底），类型固定 `code`。

* 用户重装 Agent 后会获得新 name（OS 兜底），前端可通过 PATCH 二次改名。

## 关联文档

* [ADR-006 Agent 节点部署与接入方案](./006-agent-node-deployment.md)

* [ADR-008 Linux 编程 Agent 节点 MVP 与节点管理](./008-agent-node-linux-programming-mvp.md)

* [ADR-014 Agent 节点真实系统资源监控](./014-agent-node-metrics.md)

* [004-agent-node-protocol-spec.md](../spec/004-agent-node-protocol-spec.md)

* [008-node-management-and-programming-agent-linux-spec.md](../spec/008-node-management-and-programming-agent-linux-spec.md)

* [agent-node-standards.md](../standards/agent-node-standards.md)

* [api-standards.md](../standards/api-standards.md)

* [database-standards.md](../standards/database-standards.md)

* [backend-component-standards.md](../standards/backend-component-standards.md)

* [frontend-component-standards.md](../standards/frontend-component-standards.md)

* [logging-standards.md](../standards/logging-standards.md)

* [environment-config-standards.md](../standards/environment-config-standards.md)

## 实际实施情况

### 2026-08-26 实施

- **后端**：
  - `app/schemas/agent_node.py` 新增 `AgentNodeUpdateRequest`（仅 name 字段，1-128 字符）；`AgentNodeRegisterRequest.agent_type` 描述从 "programming/video/copywriting" 改为 "code/video/copywriting"。
  - `app/services/agent_node_management_service.py` 新增 `update_node_name` 方法（含 strip 校验、重复预检 + IntegrityError 兜底、idempotent 同名短路）；`SUPPORTED_INSTALL_SCRIPTS` 改为 `('code', 'linux')`。
  - `app/api/v1/endpoints/agent_node_management.py` 新增 `PATCH /api/v1/agent-nodes/{node_id}` 路由，含 `node_name_update_started` / `node_name_update_completed` / `node_name_update_failed` 结构化日志。
  - `app/core/errors.py` 新增错误码 `AGENT_NODE_NAME_DUPLICATE = 202010` / `AGENT_NODE_NAME_INVALID = 202011`。
  - 测试：`tests/api/v1/test_agent_node_name_update.py` 13 项全过；`tests/api/v1/test_agent_nodes.py` 1 项（`test_get_install_script_success`）从 `programming` 改为 `code` 期望值。
- **数据库迁移**：`alembic/versions/20260826000000_rename_agent_type_to_code_and_add_unique_name.py` 已执行。
  - 实际迁移输出：`renamed 2 legacy duplicate (agent_type, name) rows` + `migrated 4 agent_type rows from 'programming' to 'code'`。
  - 部分唯一索引 `ux_agent_nodes_agent_type_name` 创建成功（`USING btree (agent_type, name) WHERE (name IS NOT NULL)`）。
  - 旧表里的 `localhost.localdomain-programming-j1169i` 3 条按 `created_at` 升序保留首条，其余 2 条改名为 `-dup1` / `-dup2`，与 ADR §5「处理存量冲突」一致。
- **Agent 端**：
  - `opc-agent-programming/app/config.py` 新增 `_read_os_release()`；`_generate_name()` 重写为 `/etc/os-release` → `platform.platform()` → hostname 兜底，**不再追加 `-programming-` 前缀**；`agent_type` 默认值改为 `code`。
  - `opc-agent-programming/.env.example` 改 `AGENT_TYPE=code`。
  - `opc-agent-programming/scripts/install-agent-programming-linux.sh` 新增 `--agent-name` 参数；`write_env_file()` 改为：留空时不写入 `AGENT_NAME`（让 Agent 启动时按 OS 兜底），并显式写入 `AGENT_TYPE=code`；`generate_agent_name` 函数保留（已标注废弃）以避免老模板残留引用。
  - `opc-central-dispatch-hub/static/install-scripts/install-agent-programming-linux.sh` 与 Agent 端脚本同步。
  - 测试：`opc-agent-programming/tests/test_config.py` 9 项全过。
- **前端**：
  - `src/api/agentNodes.ts` 新增 `updateAgentNodeName(id, name)`。
  - `src/utils/installCommand.ts` `buildInstallCommand` 新增可选第 4 参数 `agentName`，留空不追加 `--agent-name`，非空时用双引号包裹 + 转义内部双引号。
  - `src/components/InstallCommandModal.tsx` 新增「节点名称」`Input` 字段（maxLength=128），默认空；agent_type 默认改为 `code`，下拉选项从 `programming` 改为 `code`。
  - `src/components/NodeDetailDrawer.tsx` 「节点名称」从只读改为可编辑表单（Input + 保存/取消按钮 + Enter 提交）；Agent 类型仍为只读 Text（按用户确认）。
  - `npm run typecheck` 通过；我改的 4 个文件 `npx eslint` 干净。
- **文档**：`docs/adr/016-agent-node-display-name-and-code-type.md` / `docs/spec/016-agent-node-display-name-and-code-type-spec.md` / `docs/spec/016-agent-node-display-name-and-code-type-harness.md` 已在 `docs/INDEX.md` 登记。

### 全量回归

- 中枢后端 pytest：159 passed in 72.94s。
- Agent 端 pytest：74 passed in 4.26s。
- 前端 tsc -b：0 errors。
- Alembic `upgrade 20260826_node`：成功，4 行迁移 + 2 行去重 + 索引创建。
- 既有 4 个节点 `agent_type` 全部为 `code`；前端列表里 `localhost.localdomain-programming-j1169i` 等老名字保留（与决策一致），运维可通过 NodeDetailDrawer 编辑或重装时传 `--agent-name` 覆盖。

## 变更记录

| 日期         | 变更内容                                                                                                                                                                                                      | 变更人  |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 2026-08-26 | 初始创建：节点显示名可自定义（安装阶段 + 中枢 PATCH 二次编辑），OS 平台+版本兜底替换旧 `{hostname}-programming-随机串`；`agent_type` 值 `programming` → `code`（保留列名）；新增 partial unique index `(agent_type, name)`；所有改动接口按 logging-standards 加结构化日志 | AI助手 |

