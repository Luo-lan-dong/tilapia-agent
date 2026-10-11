# ADR-012：基于 tilacode 集成编程执行层

## 状态

Accepted

## 日期

2026-08-08

## 问题背景

ADR-008 决策了 Linux 编程 Agent 节点的 MVP 范围：安装 → 自启 → 注册 → 心跳保持在线，任务下发后节点仅做占位回复，**不实现真实代码编写/执行能力**，真正的执行器后续迭代。

现在需要启动这个"后续迭代"——为编程 Agent 接入真实的代码执行能力，使其能够：

- 接收中枢下发的编程任务（需求文档、Spec 等）
- 自主读写文件、执行 bash 命令、多轮 LLM 对话
- 实时上报执行进度（工具调用、文本输出、思考过程）
- 任务完成后产出代码交付物

### 现有约束

- OPC Agent 现有代码为 Python（FastAPI + WebSocket），负责注册、心跳、任务接收
- 中枢已有 WebSocket 通信协议（`hub.task_dispatch` / `agent.task_progress` / `agent.task_result`）
- ADR-008 的安装脚本、systemd 自启、注册流程已上线，不能破坏

### tilacode 项目评估

`opc-agent-programming/tilacode/` 是一个 MIT 协议的开源 AI 编程代理（opencode-ai），具备：

- 完整的 LLM 多轮对话、工具调用（bash/edit/read/write/grep/glob）
- HTTP Server 模式（`tilacode serve`）暴露完整的 Session 管理 API
- 事件流订阅（session.events Stream）支持实时进度推送
- 会话持久化（SQLite）、历史恢复、会话 fork
- 插件机制、自定义 agent/tool 注册
- MIT 协议允许商业使用、修改、分发

技术栈为 Bun + TypeScript + Effect + SolidJS，与 Python 生态不重叠。

## 决策

### 1. 采用 tilacode 作为编程执行层

将 tilacode 以 HTTP Server 模式集成到 OPC Agent 内部，作为"执行层"。Python 外壳保持不变，新增"任务桥接层"负责将中枢的 WebSocket 任务指令转换为 tilacode 的 HTTP API 调用。

### 2. 对接模式：HTTP Server 长驻

Python 层启动并管理一个 tilacode server 子进程（监听 localhost），通过 HTTP API 调用：

- `session.create` + `session.prompt` 下发编程任务
- `session.events` 订阅执行事件流，转换为中枢的 `agent.task_progress` 上报
- `session.interrupt` 中断任务
- `session.history` 获取会话产物

### 3. 工作目录策略

每个任务创建独立工作目录 `/workspace/{task_id}/`，tilacode 在该目录下执行。任务完成后打包产物上传中枢，但**不清理目录**——因为任务可能不达标需要二次修改，保留工作目录支持续接修改。

### 4. LLM 配置策略

- **MVP 阶段**：使用 tilacode 自带的免费模型，默认模型为 **MiMo V2.5 Free**（`providerID: "opencode"`，`id: "mimo-v2.5-free"`，已按实际部署节点 `GET /api/model` 返回核实；context 200k / output 32k，支持工具调用），Python 层不干预 API Key
- **后期迭代**：改为由中枢下发 LLM 配置（API Key、模型选择），与 ADR-009 的 LLM Provider 管理对齐。Python 层启动 tilacode 时通过配置文件或环境变量注入

### 5. 任务桥接层职责

Python 层新增桥接模块，负责：

| 中枢事件 | 桥接动作 | tilacode API / 事件 |
|---------|---------|---------------------|
| `hub.task_dispatch` | 创建工作目录 + 创建/复用 session + 发送 prompt | `POST /api/session` + `POST /api/session/:id/prompt` |
| 任务执行中 | 订阅事件流，转换并上报进度 | `GET /api/session/:id/event` (SSE) |
| 工具调用完成 | 上报 `agent.task_progress`（工具名、状态） | 事件 `session.next.tool.success` / `failed` |
| LLM 文本输出 | 上报 `agent.task_progress`（文本内容） | 事件 `session.next.text.delta` / `ended` |
| 思考过程 | 上报 `agent.task_progress`（思考内容，可选） | 事件 `session.next.reasoning.delta` / `ended` |
| provider turn 完成 | 触发任务完成判定 | 事件 `session.next.step.ended` |
| 会话 error | 上报 `task_failed` | 事件 `session.error` |
| 权限请求 | build 模式配置层全权赋予；兜底自动 `once` | 事件 `permission.v2.asked`（实例级 SSE `/api/event`） |
| 模式切换 | 根据中枢指令切换 plan/build | `POST /api/session/:id/agent` |
| 中枢中断指令 | 调用中断 | `POST /api/session/:id/interrupt` |

### 6. 权限策略

#### 6.1 build 模式：全权赋予

MVP 阶段 build 模式用于实际代码开发，需要无人值守自动执行，因此采用**配置层全权赋予**：

- 在工作目录的 `.opencode/opencode.jsonc` 中配置 `"permission": { "*": "allow" }`
- 该规则会合并进 build agent 的 ruleset，源头上不再触发权限请求
- API 层仍订阅 `/api/event` 作为兜底，未覆盖的权限事件自动回复 `"once"`

#### 6.2 plan 模式：只读规划

plan 模式仅用于需求分析和方案规划，不执行代码变更：

- 使用 tilacode 内置 `plan` agent
- 保留默认 rules：edit 被拒绝（只允许 `.opencode/plans/*.md`），bash 等执行类工具默认 ask
- plan 产物（`.opencode/plans/*.md`）由 Python 层读取后上报中枢审批

#### 6.3 后期迭代

后续引入中枢 agent 审批权限时，将替换 `"*": "allow"` 为按权限类型细分的策略（如 bash 按命令白名单、edit 按文件路径审批）。

### 7. Session 生命周期策略

- **一任务一会话**：每个 `task_id` 对应一个固定 Session，Session id 生成规则为 `ses_task_<task_id>`（必须以 `ses` 开头）
- **会话持久化**：tilacode 将 Session 数据持久化在 `~/.local/share/opencode/` 的 SQLite 数据库中，更新/重装 tilacode 不丢失
- **会话续接**：同一 task 的后续命令直接对已有 Session 调用 `POST /api/session/:id/prompt`，无需新建
- **本地映射**：Python Agent 维护 `sessions.json` 记录 `task_id → session_id → workspace_dir → status`，便于程序化续接
- **任务完成后保留**：工作目录和 Session 均不清理，支持二次修改

### 8. plan / build 切换策略

- **由中枢决定**：`hub.task_dispatch` 携带 `mode: "plan" | "build"`，Agent 根据 mode 创建或切换 Session 的 agent
- **同一 Session 内切换**：通过 `POST /api/session/:id/agent` 在 plan 和 build 之间切换，保留工作目录上下文
- **典型流程**：
  1. 中枢要求规划 → Agent 切换为 plan agent → 生成 `.opencode/plans/*.md`
  2. Python 读取 plan 产物上报中枢
  3. 中枢审批通过后，下发 build 指令 → Agent 切换为 build agent → 按 plan 执行代码开发
- **直接开发**：若中枢指令明确要求直接开发，则直接以 build agent 运行，跳过 plan 阶段

### 9. tilacode 改造范围

经过源码探索，tilacode 现有 HTTP API 已能覆盖 MVP 需求，**无需修改 tilacode 源码**。需要做的只是：

- 在 Agent 模板中放入 `.opencode/opencode.jsonc` 默认配置（权限策略、默认模型）
- Python 层实现 tilacode 进程管理、HTTP 客户端、事件转换、工作目录管理
- （可选）如产物打包逻辑较复杂，可在 Python 层直接打包，无需新增 tilacode 端点

已确认可用且符合预期的 API：

- `POST /api/session`：创建/复用 Session
- `POST /api/session/:id/agent`：切换 agent（plan/build）
- `POST /api/session/:id/prompt`：发送 prompt
- `GET /api/session/:id/event`：Session 级 SSE 事件流
- `GET /api/event`：实例级 SSE 事件流（接收权限事件）
- `POST /api/session/:id/interrupt`：中断任务
- `GET /api/session/:id/history`：获取会话历史

注意：`POST /api/session/:id/wait` 和 `POST /api/session/:id/compact` 当前不可用（返回 503），任务完成判定不依赖这两个端点。

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| **A（本决策）：tilacode HTTP Server 集成** | 复用 tilacode 完整能力；多任务并发；会话复用；实时事件流；MIT 协议无限制 | 需管理两个进程（Python + tilacode）；技术栈分裂（Python + TS）；需改造 tilacode 暴露专用接口 | ✅ 选中 |
| B：tilacode CLI 子进程模式 | 对接简单（解析 stdout JSON）；隔离性好 | 每任务一个进程，资源开销大；会话续接需 `--session` 参数；任务中断需 kill 进程；产物获取需读 SQLite | ❌ 否决 |
| C：用 Python 重写编程执行器 | 技术栈统一（全 Python） | 工作量巨大；难追赶 tilacode 上游能力；LLM 工具链生态不如 TS 丰富 | ❌ 否决 |
| D：将 OPC Agent 整体迁移到 TypeScript/Bun | 直接用 tilacode 作为 Agent 主体；能力最强 | 废弃现有 Python 代码（注册/心跳/安装脚本）；违反 ADR-008 的 Python 技术栈决策；迁移风险大 | ❌ 否决 |

## 决策原因

1. **最大化复用 tilacode 能力**：tilacode 已具备完整的 AI 编程能力（LLM 对话、工具调用、文件操作、会话管理），无需从零开发
2. **MIT 协议无商业限制**：可自由修改、分发、商业化，适合产品化
3. **HTTP Server 模式最优**：支持多任务并发、会话复用、实时事件流、优雅中断，远优于 CLI 子进程模式
4. **保护现有投资**：Python 外壳（注册/心跳/安装脚本/systemd）保持不变，不违反 ADR-008
5. **渐进式 LLM 配置**：MVP 先用 tilacode 自带配置快速跑通，后期再与中枢的 Provider 管理对齐

## 影响

### 对系统架构的影响

- Agent 节点从"单进程 Python"变为"双进程：Python 外壳 + tilacode server"
- 新增任务桥接层，负责 WebSocket ↔ HTTP 协议转换
- 工作目录管理成为 Agent 节点的新职责

### 对开发的影响

- 需要维护 tilacode 的定制改造（暴露专用接口）
- Python 层新增 tilacode 进程管理、健康检查、自动重启逻辑
- 需要设计工作目录的生命周期管理（创建、产物打包、续接、清理策略）
- 安装脚本需新增 Bun 运行时安装步骤

### 对安装部署的影响

- ADR-008 的安装脚本需扩展：除 Python 外还需安装 Bun 运行时
- tilacode 项目代码需打包到 Agent 模板中
- systemd 服务需管理 Python 主进程（Python 主进程负责拉起 tilacode server）

### 对安全的影响

- tilacode 可执行任意 bash 命令，需评估沙箱隔离方案（后续迭代）
- 权限自动批准模式（`--auto`）存在安全风险，MVP 阶段可接受，生产环境需收紧
- tilacode server 监听 localhost，不对外暴露，降低攻击面

### 对运维的影响

- Agent 节点资源占用增加（Python + Bun + tilacode SQLite）
- 需要监控 tilacode server 健康状态，崩溃时自动重启
- 工作目录磁盘占用需监控，达阈值时告警

### 对 ADR-008 的影响

本决策**不违反** ADR-008，而是实现 ADR-008 中明确提到的"真正的执行器后续迭代"。ADR-008 的安装/注册/心跳/占位执行机制保持不变，仅在任务执行环节替换占位逻辑为真实 tilacode 调用。

## 决策修订记录

### 2026-08-14 修订

本次修订基于对 tilacode 源码的实际探索，修正了早期文档中的假设：

1. **权限自动审批**：tilacode 非交互模式（`run --auto`）已具备自动权限回复能力；MVP 采用配置层 `"*": "allow"` + API 层兜底 `once` 的方案，无需改造 tilacode 源码
2. **事件类型修正**：SSE 实际事件为 `session.next.*` 系列和 `session.error`，`session.status` 不是 server SSE 事件；权限事件实际类型为 `permission.v2.asked`，且需通过 `/api/event` 接收
3. **任务完成判定**：不能依赖 `session.status: idle` 或 `POST /api/session/:id/wait`（当前返回 503），应通过 `session.next.step.ended` + 轮询 `/api/session/active` 或 `/api/session/:id/history` 判定
4. **Session 复用**：通过 `POST /api/session` 传入相同 id 即可复用；数据持久化在 `~/.local/share/opencode/`，更新/重装 tilacode 不丢失
5. **plan/build 切换**：tilacode 内置 `plan` 和 `build` agent，通过 `POST /api/session/:id/agent` 切换
6. **默认模型**：MVP 使用 tilacode 自带的免费模型 MiMo V2.5

### 2026-08-14 决策定稿确认

经用户最终确认，以下五点正式定稿（与上文修订一致）：

1. **build 模式权限**：全权赋予（配置层 `"*": "allow"` + API 层兜底 `once`），确保无人值守任务不被权限请求卡住；由中枢 agent 进行权限审批列入后期迭代，不在 MVP 范围
2. **会话数据安全**：Session 持久化于 `~/.local/share/opencode/`（SQLite），与二进制安装目录分离——重新安装或升级 tilacode 二进制**不会删除历史会话**。前提：不删除数据目录、不更换 systemd 运行用户（HOME 变更会导致数据目录路径变更）
3. **Session 生命周期**：一 `task_id` 一 Session（`ses_task_<task_id>`），任务完成后保留；Python 层以 `sessions.json` 统一记录 `task_id → session_id → workspace_dir → status` 支持续接；升级流程必须保留 `data/sessions.json` 与 `workspace/`
4. **plan/build 切换**：由中枢 `hub.task_dispatch.mode` 决定——`build` 直接开发；`plan` 先规划并将 `.opencode/plans/*.md` 产物上报中枢审批；通过 `POST /api/session/:id/agent` 在同一 Session 内切换 agent，保留上下文
5. **默认模型**：MVP 采用免费模型 MiMo V2.5 Free（`providerID: "opencode"`，`id: "mimo-v2.5-free"`）。2026-08-14 已在部署节点实测 `GET /api/model`：共 27 个免费模型，其中 7 个 `active`（mimo-v2.5-free、nemotron-3.5-lightning-free、deepseek-v4-flash-free、laguna-s-2.1-free、hy3-free、nemotron-3-ultra-free、big-pickle），其余 20 个已 `deprecated`；早期文档中的 `xiaomi/mimo-v2.5` 为推测 id，以实测为准

## 任务迭代链与 plan 审批流（2026-08-15 增补决策）

### 1. 任务迭代链（同一项目多轮修改）

- **链式结构**：新任务记录 `parent_task_id`，中枢冗余存储 `root_task_id`（链根），派发时以 `base_task_id` 字段下发链根
- **会话归属升级**：Agent 一律复用**根任务**的工作目录与 tilacode Session（`ses_task_<root_task_id>`）——链上所有任务共享同一 Session，AI 上下文跨任务完整累积。原 §7"一任务一会话"决策在迭代场景下修正为"一**任务链**一会话"
- **增量说明 change_brief**：迭代必须在中枢**同一个任务构建会话**中进行（恢复会话描述增量需求），AI 更新三件套（全量）并产出 `change_brief`（本轮修改说明），随派发下发；**Agent 不做文档 diff 检测**（不可靠），prompt 显式携带修改说明
- prompt 语义：`base_task_id` 存在时 = "项目代码已存在，本轮只需：{change_brief}；完整文档见 docs/"

### 2. plan 审批流

- **模式选择**：execution_mode（plan/build）在**中枢**创建任务时指定
- **方案回传**：plan 任务完成后，Agent 读取工作目录 `.opencode/plans/*.md` 全文，随 `agent.task_completed`（payload 新增 `plan_content`）上报，中枢存入 `Task.plan_content`（产物上传机制落地前的回传方式）
- **批准**：`POST /tasks/{id}/approve-plan` → mode 改 build → `change_brief` 拼入批准方案全文 → 重新入队派发（同 Session 同目录直接开发）
- **驳回**：`POST /tasks/{id}/reject-plan`（body 携带 feedback）→ feedback 拼入 `change_brief` → 以 plan 模式重新入队派发，循环直至批准
- MVP 讨论方式 = 驳回意见异步循环；对话式评审界面后续迭代

### 3. 协议变更汇总

`hub.task_dispatch` payload 新增：`base_task_id`（可空，任务链根）、`change_brief`（可空，本轮修改说明/批准方案/驳回反馈，由中枢拼装）；`agent.task_completed` payload 新增：`plan_content`（可空，plan 模式的方案全文）。Task 模型新增 4 列：`parent_task_id`、`root_task_id`、`change_brief`、`plan_content`。

## 中枢对话式评审界面与 change_summary 回传（2026-08-17 增补决策）

### 1. plan 审批闭环前移至中枢构建页（原"后续迭代"提前实现）

原 §2「MVP 讨论方式 = 驳回意见异步循环；对话式评审界面后续迭代」。现提前实现：

- **方案展示**：中枢构建页（TaskNew）右侧文档面板新增「方案」Tab（与规范文档/任务清单/验收清单并列）。方案数据源为 `Task.plan_content`（任务派发后 Agent 回传），**不走构建对话 SSE**——构建对话的三件套（spec/tasks/checklist）语义不变，`TaskSpecification` 不新增 plan 字段
- **创建后不跳转**：构建页创建任务（plan/build 均适用）后停留本页，进入跟踪态：按 5s 轮询 `GET /v1/tasks/{id}`，「方案」Tab 实时展示任务状态；plan 任务 completed 后展示方案全文
- **审批闭环**：批准/驳回按钮直接放在「方案」Tab 内（复用既有 `approve-plan` / `reject-plan` 接口）；驳回填写原因 → 任务以 plan 模式重新入队 → 轮询继续，新方案返回后 Tab 更新；批准 → 转 build 开发 → 完成后 Tab 展示本轮修改说明。任务队列详情抽屉的审批入口保留（双入口共用同一接口）
- **URL 持久化**：创建成功后 URL 追加 `?task=<task_id>`，刷新后可恢复跟踪与会话

### 2. change_summary（本轮修改说明）回传

- **Agent 侧生成**：build 模式任务完成判定后，向**同一 Session** 追加一次总结 prompt（要求产出本轮修改说明，不改代码），消费完成后调 `GET /api/session/:id/history` 取最后一条 assistant 消息文本
- **协议**：`agent.task_completed` payload 新增 `change_summary`（可空，仅 build 模式携带）；Task 模型新增 `change_summary` 列（Text，可空），中枢校验与超长截断策略与 `plan_content` 一致
- **展示位置**：① 构建页「方案」Tab（跟踪态 build 完成后）；② 迭代场景（`/tasks/new?parent=`）蓝色「任务迭代模式」卡片下方，折叠展示父任务的 `change_summary`——用户填写下一轮 change_brief 前先看到上一轮 Agent 改了什么
- **降级**：总结 prompt 失败不阻断任务完成上报，`change_summary` 为 None 时前端不展示该卡片

### 3. 迭代会话恢复修复（落实本 ADR §1 既有决策）

既有决策"迭代必须在中枢同一个任务构建会话中进行"此前未在前端落实：`/tasks/new?parent=` 进入后为空白会话。本次修复：

- `TaskResponse` 新增返回 `session_id`；构建页检测到 `?parent=` 时自动拉取父任务详情，按 `session_id` 加载对应构建会话并恢复（messages + 三件套 + 对话模式）
- 父任务无关联会话（如 API 直接创建）时保持现状：空白会话 + 迭代卡片

### 4. 协议变更汇总

`agent.task_completed` payload 新增 `change_summary`；Task 模型新增 1 列 `change_summary`；REST 无新端点（`GET /v1/tasks/{id}` 响应新增 `session_id`、`change_summary` 字段，向后兼容）。

## tilacode 待办列表实时上报与进度依据（2026-08-24 增补决策）

### 1. 问题背景

tilacode 在执行任务时可通过 `todowrite` 工具维护会话级待办列表（todo），该列表比基于"工具调用次数"的进度估算更能反映真实执行进度。当前 Agent 仅按事件计数递增进度，存在两个问题：

1. **进度不稳定**：AI 有时不创建 todo 列表，导致基于事件计数的进度无法与实际任务阶段对齐。
2. **信息未回传中枢**：todo 列表仅停留在 tilacode 内部，中枢与前端无法实时查看当前执行项与剩余工作。

### 2. 决策

#### 2.1 todo 列表实时上报

- Agent 在消费 tilacode `session.next.*` 事件流时，识别 `todo.updated` 事件（事件类型由 tilacode 内部 `SessionTodo` 服务发布）。
- 将 `todos` 数组随 `agent.task_progress` 事件上报中枢，payload 新增 `todos` 字段。
- 中枢 `Task` 模型新增 `todos` JSONB 字段持久化；前端通过既有轮询 `GET /v1/tasks/{id}` 获取最新列表。

#### 2.2 todo 作为进度主要依据（带降级）

- 当 todo 列表存在且非空时，进度按 `completed / total` 计算（结果映射到 0~100 整数）。
- 当 AI 未创建 todo 列表（列表为空或未收到 `todo.updated`）时，回退到原有事件计数进度机制，避免进度停滞。
- 任务完成时由 SSE 消费循环的完成判定统一置 100，不依赖 todo 比例。

#### 2.3 前端展示

- `TaskNew` 右侧文档面板支持折叠为图标栏，默认展开，用户可手动收起。
- 在右侧新增「任务摘要」区域，实时展示当前跟踪任务的待办列表、进度条、当前执行项，与 tilacode 原生 UI 的 todo 面板保持一致体验。

### 3. 协议变更汇总

- `agent.task_progress` payload 新增 `todos` 字段（数组，元素 `{content, status, priority}`）。
- `Task` 模型新增 `todos` JSONB 列；`TaskResponse` 响应新增 `todos` 字段。
- 中枢 `_handle_task_progress` 在更新 `progress` 时，同步写入 `todos`；若 payload 中的 `progress` 与 `todos` 推导不一致，以 `todos` 推导值为准（当 todo 列表非空时）。

### 4. 实施修复记录（2026-09-12）

决策落地后经生产实测发现 `todo.updated` 事件长期未到达 Agent，根因与修复：

- **根因**：tilacode 实例级 SSE（`/api/event`）按 `x-tilacode-directory` 目录**严格过滤**事件（`event.location.directory === 订阅目录`），而 Agent 的全局 `_permission_pump` 以**工作区根目录**订阅，任务事件目录为**任务工作目录**（`{root}/{task_id}`），两者不匹配导致 `todo.updated`（及权限兜底事件）被全部丢弃。8 月底验证通过系验证环境目录恰好一致。
- **修复**：全局权限泵改为**任务级实例事件泵**——`execute_task` 内以任务工作目录订阅实例级 SSE（`_task_event_pump`，随任务结束取消、断流重连）；`TilacodeClient.stream_instance_events` 新增 `directory` 参数（不改共享客户端目录头）；`_instance_event_payload` 保持 `properties`/`data` 双载体兼容（线上实测事件数据位于 `data` 字段）。
- **验证**：本机 Win11 + tilacode 源码 serve + DeepSeek 实测，任务级目录订阅可收到完整 `todo.updated`（5 项清单的 in_progress→completed 流转）；Agent 全量 90 项测试通过。

## 后续迭代记录

以下需求已记录，将在后续迭代中实现，不在本次 MVP 范围内：

1. **LLM 配置中枢下发**：中枢通过 ADR-009 的 Provider 管理，向 Agent 下发 LLM API Key 和模型选择，Python 层注入到 tilacode 配置
2. **沙箱隔离**：限制 tilacode 的 bash 执行权限，防止恶意命令
3. **工作目录清理策略**：基于任务达标状态和保留期限的自动清理机制
4. **多 Agent 类型扩展**：tilacode 模式验证成功后，视频/文案/设计类 Agent 可参考同样的"外壳 + 执行层"架构

## 部署方案修订（ADR-013）

本 ADR「对安装部署的影响」章节原计划"安装脚本需新增 Bun 运行时安装步骤 + tilacode 项目代码需打包到 Agent 模板中"，该方案已被 [ADR-013](./013-programming-agent-binary-distribution.md) **修订**：

- ❌ 原方案：Agent 节点安装 Bun 运行时 + tilacode 源码打包到模板
- ✅ 新方案（ADR-013）：tilacode 预编译为独立二进制（`--skip-embed-web-ui`，~130MB），手动构建后复制到中枢 `static/binaries/`，Agent 节点一行命令拉取，无需 Bun/Node 运行时

本 ADR 的其余决策（HTTP Server 集成模式、任务桥接层、工作目录策略、LLM 配置策略）不受影响，继续有效。

## 关联文档

- [ADR-008 Linux 编程 Agent 节点 MVP 与节点管理](./008-agent-node-linux-programming-mvp.md)
- [ADR-006 Agent 节点部署与接入方案](./006-agent-node-deployment.md)
- [ADR-009 LLM Provider 配置管理](./009-llm-provider-management.md)
- [004-agent-node-protocol-spec.md](../spec/004-agent-node-protocol-spec.md)
- [agent-node-standards.md](../standards/agent-node-standards.md)
- [012-tilacode-execution-layer-integration-spec.md](../spec/012-tilacode-execution-layer-integration-spec.md)

## 修订记录

- **2026-09-16（ADR-036）**：§3 工作目录策略的默认命名 `{workspace}/{task_id}`（UUID）修订为可读命名——中枢创建新链根任务时自动生成 `中文标题-任务ID前8位` 的 `output_dir` 随派发下发；链上子任务继承链根目录（本 ADR §任务迭代链"复用根任务工作目录与 Session"语义不变）。详见 [ADR-036](./036-session-task-chain-and-spec-command.md)。
- **2026-09-17（ADR-037）**：§1 "AI 更新三件套（全量）"的落盘路径修订为版本化目录 `docs/specs/<净化中文标题>-<任务ID前8>/`（按轮次共存，不再覆盖 docs/ 固定路径）；prompt 文案同步指向本轮版本目录。详见 [ADR-037](./037-agent-spec-docs-versioned-dir.md)。
