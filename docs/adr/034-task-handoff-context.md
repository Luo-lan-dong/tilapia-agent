# ADR-034: 跨 Agent 任务现场迁移与项目上下文记忆（task-handoff）

## 状态

Accepted

## 日期

2026-09-15

## 问题背景

任务链迭代（ADR-012/028）依赖**同一台 Agent** 上的工作目录与 tilacode 会话延续上下文。
当原执行 Agent 离线、任务改派到其他 Agent（ADR-032 改派机制）时存在两个断层：

1. **现场断层**：新 Agent 机器上没有项目代码/文档/规则，无法继续开发；
2. **记忆断层**：中枢只保存产物文件（ADR-028 artifacts），没有 Agent 的
   **执行上下文**（项目做了什么、当前状态、如何继续），新 Agent 即使拿到代码
   也不知道"这个项目之前进行到哪了"。

用户确认（2026-09-15）：采用 **A+B 组合**——完整现场迁移为必须（代码/文档/
规则全带上，仅排除 node_modules 等可在目标机重装的目录），并补充 Agent
执行上下文记忆；新 Agent 首次接手时先阅读上下文记忆再执行任务。

## 决策

1. **项目上下文记忆（project_context.md）**：
   - Agent 端在任务完成上报（`agent.task_completed`）前，由 tilacode AI 生成
     `project_context.md`（项目目标 / 目录结构 / 已完成 / 进行中 / 如何继续 /
     注意事项），作为任务交付物的一部分上报；
   - 中枢按**任务链**（root_task_id）维度存储，新任务完成时覆盖更新
     （链内始终是最新态）；
   - 新表 `task_chain_contexts`：`root_task_id` 唯一、`context_md` TEXT、
     `source_task_id`、时间戳。
2. **派发时的现场迁移（handoff）**：
   - 触发条件：派发任务链中的任务（root_task_id 已有历史产物）且目标 Agent
     尚无该链工作目录（Agent 侧无法本地判断时统一下发，由 Agent 幂等处理）；
   - 中枢将任务链产物目录聚合打包（`tar.gz`，排除 node_modules / .git /
     .tilacode / __pycache__ 等可重装目录，复用 ADR-028 artifacts 排除规则），
     附带该链 `project_context.md`；
   - 传输通道：中枢提供**带一次性 Token 的临时下载端点**
     `GET /api/v1/tasks/{task_id}/handoff-bundle?token=...`（超时自动失效），
     派发 payload 新增 `handoff` 字段（bundle_url + context_md 内联）；
   - Agent 端收到后：先落盘解压到任务工作目录（已存在文件跳过或按包内
     版本覆盖）→ **先阅读 project_context.md**（作为 tilacode 恢复上下文的
     初始输入）→ 再执行任务。
3. **产物聚合视图**：中枢 artifacts 查询（tree/file）支持按 `root_task_id`
   聚合任务链内全部任务的产物（派发打包与前端展示共用）。
4. **安全**：handoff bundle URL 使用一次性随机 Token（写入即用即焚），
   有效期默认 10 分钟。

## 备选方案

| 方案 | 结论 |
|------|------|
| A. 仅上下文摘要 md，不迁移产物 | 否决。用户明确要求完整现场：只有 md 没有"之前的代码/文档/规则"，多 Agent 交接后项目会越来越乱 |
| B. 仅产物迁移，无上下文记忆 | 否决。传统产物中没有 Agent 上下文，新 Agent 不知道项目进行到哪 |
| **C. A+B 组合（本决策）** | 采纳。摘要 md 必选（每轮覆盖）+ 产物打包迁移（跨 Agent 派发时） |
| D. Agent 间 P2P 直传 | 否决。原 Agent 已离线是触发场景之一，中枢中转是唯一可靠路径 |

## 影响

### 中枢

- 新增 `task_chain_contexts` 表 + Alembic 迁移；
- `task_event_handler`：`task_completed` 时接收并存储 `project_context.md`；
- 调度派发：`_build_dispatch_message` 增 `handoff` 字段构造（按触发条件）；
- 新增 handoff bundle 打包服务（产物聚合 + 排除规则 + 临时下载端点）；
- artifacts 查询支持按 root_task_id 聚合。

### Agent

- `hub_client`：处理 payload.handoff（下载 bundle → 解压到工作目录 → 幂等）；
- `task_bridge`：任务启动时若存在 project_context.md，注入 tilacode 初始上下文；
- 任务完成前生成 project_context.md（tilacode prompt 约定）并随交付物上报。

### 协议

- `agent.task_completed` payload 新增可选 `project_context`（md 文本）；
- `hub.task_dispatch` payload 新增可选 `handoff`（{bundle_url, context_md}）；
- api-standards §4.2 登记；新增下载端点登记 §2.1。

### 兼容性

- 旧 Agent 忽略 handoff 字段（照常新建会话执行，行为不劣于现状）；
- 首个链任务（无历史产物）不触发迁移，零开销。

## 关联文档

- [ADR-028 任务工作目录绑定与产物资源管理器](./028-task-workspace-binding-and-artifact-sync.md)
- [ADR-032 节点并发任务调度](./032-concurrent-task-scheduling.md)（改派触发场景）
- [ADR-012 tilacode 执行层集成](./012-tilacode-execution-layer-integration.md)（Session 复用）
- [034-task-handoff-context-spec.md](../spec/034-task-handoff-context-spec.md) /
  [harness](../spec/034-task-handoff-context-harness.md) /
  [changes](../spec/034-task-handoff-context-changes.md)
