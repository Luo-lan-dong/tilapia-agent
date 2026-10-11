# 跨 Agent 任务现场迁移与项目上下文记忆 Spec（task-handoff）

> 关联决策：[ADR-034](../adr/034-task-handoff-context.md)
> Harness：[034-task-handoff-context-harness.md](./034-task-handoff-context-harness.md)
> Changes：[034-task-handoff-context-changes.md](./034-task-handoff-context-changes.md)

## 1. 功能描述

解决任务链迭代中**原执行 Agent 离线、任务改派其他 Agent** 时的上下文断层：

1. **项目上下文记忆**：Agent 在任务完成时由 AI 生成 `project_context.md`
   （项目目标/目录结构/已完成/进行中/如何继续/注意事项），中枢按任务链
   （root_task_id）存储，链内覆盖更新为最新态。
2. **现场迁移（handoff）**：派发任务链任务时，中枢把该链全部产物目录打包
   （排除可重装目录）+ 最新 project_context.md，通过一次性下载端点提供给
   新 Agent；新 Agent 落盘解压到工作目录，**先阅读上下文记忆再执行任务**。

## 2. 用户故事

- US-1：Agent A 完成登录页开发后离线；我在对话里提出新修改要求，中枢把
  任务派给 Agent B——B 收到完整代码/文档/规则与 A 的上下文记忆，先读
  project_context.md 了解项目，再在同一工作目录结构上继续开发。
- US-2：同一任务链多轮迭代后，我在中枢对话页或任务详情能查看最新的
  project_context.md（项目当前状态一目了然）。
- US-3：任务链首个任务不触发任何迁移，行为与现状完全一致。

## 3. 核心流程

### 3.1 上下文记忆上报

```
Agent 任务执行完成（tilacode build 结束）
  │
  ▼
Agent 侧 AI 生成 project_context.md（prompt 约定章节）
  │
  ▼
agent.task_completed payload 携带 project_context（md 文本）
  │
  ▼
中枢 task_event_handler：
  upsert task_chain_contexts (root_task_id → context_md, source_task_id)
```

### 3.2 派发现场迁移（handoff）

```
调度器/智能派发选中节点 B
  │
  ▼
中枢判断触发条件：任务链存在历史产物（root_task_id 下有 artifacts）
  │
  ├─ 否 → 正常派发（无 handoff 字段，行为同现状）
  │
  └─ 是 → 构造 handoff：
       ① 聚合任务链全部产物 → tar.gz（排除 node_modules/.git/.tilacode/
          __pycache__/.venv）→ 暂存 data/handoff/{task_id}.tar.gz
       ② 生成一次性 token（10 分钟有效）
       ③ payload.handoff = {bundle_url, context_md(最新), size}
  │
  ▼
Agent B 收到 hub.task_dispatch：
  ① HTTP GET bundle_url（带 token）下载 tar.gz
  ② 校验 sha256 → 解压到任务工作目录（覆盖策略：包内文件覆盖已有同名文件）
  ③ project_context.md 落盘工作目录
  ④ tilacode 执行时以 project_context.md 作为恢复上下文初始输入
  ⑤ 确认就绪 → agent.task_accepted（流程与现状一致）
```

### 3.3 触发条件判定

中枢在构造派发 payload 时判定：`root_task_id` 对应链上存在任一历史任务
产物（artifacts 非空）即附带 handoff；Agent 解压幂等（目录已同步则跳过
同名文件），同 Agent 重复接收无副作用。

## 4. 协议变更

### 4.1 `agent.task_completed`（上行，payload 扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| `project_context` | string 可选 | Agent AI 生成的项目上下文记忆（md 文本） |

### 4.2 `hub.task_dispatch`（下行，payload 扩展）

| 字段 | 类型 | 说明 |
|------|------|------|
| `handoff.bundle_url` | string 可选 | 一次性下载端点（含 token，10 分钟有效） |
| `handoff.context_md` | string 可选 | 该链最新 project_context.md 全文 |
| `handoff.size` | int 可选 | bundle 字节数（Agent 预检磁盘空间） |

### 4.3 REST 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/tasks/{task_id}/handoff-bundle?token=` | GET | 一次性下载任务链产物包（token 校验 + 过期校验） |
| `/api/v1/tasks/{task_id}/context` | GET | 查询任务链最新 project_context.md（前端展示） |

### 4.4 数据库

新表 `task_chain_contexts`：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid PK | 主键 |
| root_task_id | uuid 唯一 | 任务链根任务 ID |
| context_md | text | 最新 project_context.md |
| source_task_id | uuid | 产生该上下文的任务 |
| created_at / updated_at | timestamptz | 时间戳 |

暂存目录：`data/handoff/`（bundle 文件，下载过期后清理）。

### 4.5 配置项

| 配置 | 默认 | 说明 |
|------|------|------|
| `HANDOFF_BUNDLE_TTL_SECONDS` | 600 | 一次性下载 token 有效期 |
| `HANDOFF_MAX_BUNDLE_BYTES` | 2147483648（2GB） | bundle 大小上限，超出拒绝打包 |

## 5. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| Agent 未上报 project_context（旧版本 Agent） | 链上下文缺失，handoff 仍带产物包；Agent 端无 md 则跳过上下文注入 |
| bundle 打包失败 / 超上限 | 派发不含 handoff 字段，日志告警；任务照常执行（退化为现状） |
| Agent 下载 bundle 失败 | 重试 1 次；仍失败则按现状新建空目录执行并向中枢告警（任务可跑但不带现场） |
| 下载 token 过期 | Agent 用 task_id 重新向中枢请求（或中枢在派发前预生成足够窗口） |
| 中枢重启丢失 data/handoff 暂存 | 临时文件按需重建（打包源 artifacts 落盘数据持久） |

## 6. 验收标准

- [ ] Agent A 完成任务后，中枢 task_chain_contexts 存在该链最新 context_md。
- [ ] A 离线后任务链新任务派发到 B：B 收到 handoff，下载解压后工作目录
      包含 A 的全部产物文件（node_modules 等排除项除外）与 project_context.md。
- [ ] B 执行前已阅读 project_context.md（tilacode 初始上下文包含其内容）。
- [ ] 下载 token 过期后再次请求返回 401/410；bundle 文件过期被清理。
- [ ] 任务链首个任务派发不带 handoff 字段（行为同现状）。
- [ ] 旧版本 Agent（无 handoff 处理）派发不受影响。
- [ ] 新增协议字段已登记 api-standards；迁移脚本可回滚。

## 7. 后续迭代（本期不做）

- 前端对话页展示 project_context.md 查看 Tab；
- 增量迁移（只传上次同步后的变更文件）；
- 产物聚合查询的前端任务详情聚合视图。

## 8. 关联文档

- [ADR-034](../adr/034-task-handoff-context.md)
- [ADR-028](../adr/028-task-workspace-binding-and-artifact-sync.md)（产物通道复用）
- [ADR-032](../adr/032-concurrent-task-scheduling.md)（改派场景来源）
