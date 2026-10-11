# 中枢 AI 技能体系与智能调度 Spec（Orchestrator 统一对话 + Skills + AI 派发决策）

> 关联决策：[ADR-033](../adr/033-ai-skills-and-smart-dispatch.md)
> Harness：[033-ai-skills-and-smart-dispatch-harness.md](./033-ai-skills-and-smart-dispatch-harness.md)
> Changes：[033-ai-skills-and-smart-dispatch-changes.md](./033-ai-skills-and-smart-dispatch-changes.md)

## 1. 功能描述

将中枢从「规则调度器 + 单一文档生成能力」升级为「带技能体系的 AI Agent
平台」，本期包含三块能力：

1. **技能框架**：技能 = 中枢 AI 在对话中可自主调用的工具（function
   calling）。代码注册 + 数据库启用开关，侧边栏「技能」页统一管理。
2. **统一对话链路**：「新建对话」页由新的 OrchestratorAgent 承接，AI 根据
   用户意图自主调用技能；现有三件套文档生成封装为 `generate_spec_docs`
   技能，流式渲染体验保持不变。
3. **AI 智能调度**：自动调度在规则预筛出的合格候选集内引入 AI 批量决策
   （默认关闭，设置页可开），决策全程落审计表；对话中亦可让 AI 直接
   查节点 / 派任务，每一步以步骤卡片形式展示在对话消息内。

## 2. 用户故事

- US-1：作为平台用户，我在对话中说"看下现在哪些节点空闲"，AI 调用
  `find_idle_nodes` 技能返回节点清单，对话中能看到"调用了什么工具、
  参数、结果"的步骤卡片。
- US-2：作为平台用户，我在对话中说"把这个任务派到最合理的节点"，AI 调用
  `smart_dispatch` 技能完成派发，并解释选择理由。
- US-3：作为平台用户，我在对话中描述需求（如"做一个登录功能"），AI 调用
  `generate_spec_docs` 生成三件套文档，右侧面板流式渲染，体验与升级前一致。
- US-4：作为平台用户，我在「技能」页查看全部技能的用途、参数、返回结构，
  并可禁用某个技能（禁用后 AI 不再调用）。
- US-5：作为运维者，我在系统设置页开启"AI 智能调度"后，pending 任务在
  多候选节点场景下由 AI 综合决策；每次决策可在审计表中追溯
  （候选快照 / AI 原始输出 / 是否回退 / 最终选择 / 理由）。
- US-6：作为运维者，LLM 配置缺失或 AI 决策失败时，系统自动回退现有规则
  调度，任务照常派发，不阻塞、不派错节点。

## 3. 核心流程

### 3.1 技能对话执行流程（统一链路）

```
用户在「新建对话」发送消息
  │
  ▼
OrchestratorAgent（function calling 循环，最多 ORCHESTRATOR_MAX_TOOL_ROUNDS 轮）
  │
  ├─ LLM 判断需要调用技能
  │    │
  │    ▼
  │  SSE: step_started {step_id, seq, skill_name, args_summary}
  │  执行 Skill.execute(session, args)
  │    ├─ 成功 → SSE: step_finished {step_id, status=success, result_summary, duration_ms}
  │    └─ 异常 → SSE: step_finished {step_id, status=failed, error_summary, duration_ms}
  │  工具结果回传 LLM，继续下一轮（可连续调用多个技能）
  │
  ├─ 判断意图为"生成规范文档" → 调用 generate_spec_docs 技能
  │    └─ 内部复用 SpecBuilderAgent，SSE: specification_delta 持续下发（体验不变）
  │
  └─ 无需技能 → 直接流式回答（SSE: chunk）
  │
  ▼
SSE: status(final) 会话落库（task_building_sessions 结构不变）
```

### 3.2 AI 智能调度流程（混合模式）

```
调度循环（10s 周期，现有）
  │
  ▼
扫描 pending 任务（dispatch_type=auto 且过派发窗口，现有逻辑）
  │
  ▼
候选节点预筛（ADR-032 逻辑泛化为 _list_available_nodes）：
  在线 + 资源水位 < 阈值 + 在跑数 < 槽位上限 + 回避集合排除
  │
  ├─ AI_DISPATCH_ENABLED=false 或无 LLM 配置 ──→ 纯规则分配（现状，行为不变）
  │
  ├─ 某任务候选唯一 ──→ 直接规则分配该任务（零 token 快速路径）
  │
  └─ 存在多候选任务 且 开关开启 且 LLM 可用
       │
       ▼
      本轮多候选任务【批量合并为一次】LLM 调用
      输入：任务摘要 + 各任务候选节点关键字段
      （名称/CPU/内存/在跑数/负载分/agent_type，节点 ID 以枚举白名单注入）
       │
       ▼
      解析输出 JSON：{decisions: [{task_id, node_id, reason}]}
       │
       ├─ node_id ∈ 该任务候选白名单 → 采纳 AI 选择
       ├─ node_id 非法 / 解析失败 / 超时(8s) / 异常
       │    → 该任务回退负载升序排序，【不重试】
       ▼
      同一事务内逐任务分配（行锁）→ WS 下发 hub.task_dispatch
       │
       ▼
      dispatch_decisions 审计落库（含回退标记）
```

### 3.3 技能管理流程

```
侧边栏「技能」→ /skills 页面
  │
  ├─ 技能列表：名称 / 一句话描述 / 参数摘要 / 返回值摘要 / 启用开关
  └─ 详情抽屉：完整参数 JSON Schema + 返回结构 + 调用示例
       │
       ▼ 启用开关 PATCH /api/v1/skills/{skill_key}
      hub_skills.enabled 更新 → Orchestrator 下一轮对话生效（工具集动态过滤）
```

## 4. 协议变更

### 4.1 SSE 事件（api-standards §4.3 扩展）

| 事件 | 方向 | payload | 说明 |
|------|------|---------|------|
| `step_started` | 下发 | `step_id` / `seq` / `skill_name` / `args_summary` | 技能开始执行 |
| `step_finished` | 下发 | `step_id` / `seq` / `status`(success\|failed) / `result_summary` / `duration_ms` | 技能执行结束 |
| `chunk` / `specification_delta` / `status` / `error` | 下发 | 不变 | 复用现有事件 |

### 4.2 REST 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/skills` | GET | 技能列表（名称/描述/参数摘要/返回摘要/启用状态） |
| `/api/v1/skills/{skill_key}` | GET | 技能详情（完整 parameters JSON Schema + returns） |
| `/api/v1/skills/{skill_key}` | PATCH | 启用/禁用（`{enabled: bool}`） |
| `/api/v1/settings/ai-dispatch` | GET | 读取 AI 智能调度开关状态 |
| `/api/v1/settings/ai-dispatch` | PUT | 更新开关（热更新，存 system_settings） |

统一响应格式遵循 api-standards §2.3；新增业务错误码分段 `204xxx`
（技能域，如 204001 技能不存在），实施时回填 api-standards §3 码表。

### 4.3 数据库

- `hub_skills`：`id` PK / `skill_key` VARCHAR(64) 唯一 / `name`
  VARCHAR(128) / `description` TEXT / `enabled` BOOLEAN 默认 true /
  `created_at` / `updated_at`。种子数据：5 个 MVP 技能。
- `dispatch_decisions`：`id` PK / `task_id` FK / `batch_id` UUID（同轮
  批量决策标识）/ `candidates` JSONB（候选快照）/ `ai_raw_output` TEXT /
  `ai_choice_node_id` UUID NULL / `validated` BOOLEAN / `fallback`
  BOOLEAN / `final_node_id` UUID / `reason` TEXT / `duration_ms` INTEGER /
  `created_at`。索引：`idx_dispatch_decisions_task (task_id, created_at)`。
- 迁移脚本按 database-standards 以日期前缀命名（如
  `20260914_ai_skills_and_dispatch_decisions.py`）。

### 4.4 配置项（Pydantic Settings，.env.example 同步）

| 配置 | 默认 | 说明 |
|------|------|------|
| `AI_DISPATCH_ENABLED` | false | 自动调度 AI 决策总开关（设置页可热更新，env 为初始值） |
| `AI_DISPATCH_TIMEOUT_SECONDS` | 8 | 批量决策 LLM 调用超时预算，超时回退规则 |
| `ORCHESTRATOR_MAX_TOOL_ROUNDS` | 8 | 对话 Agent 单次回复最大技能调用轮数（防死循环） |

模型复用 ADR-030「使用中模型」配置，不新增模型配置项。

## 5. MVP 技能清单

| skill_key | 名称 | 参数 | 返回 | 实现来源 |
|-----------|------|------|------|---------|
| `find_idle_nodes` | 查找空闲节点 | `agent_type?`（按类型过滤） | 合格候选节点清单（名称/CPU/内存/在跑数/负载分） | ADR-032 预筛逻辑泛化 |
| `smart_dispatch` | 智能派发任务 | `task_id` / `preference?`（用户偏好描述） | 派发结果 + 选择理由 | AI 决策链路复用 |
| `get_node_metrics` | 查询节点资源详情 | `node_id?`（缺省返回全部在线节点） | 节点 metrics 快照 | 包装现有 service |
| `get_task_status` | 查询任务状态 | `task_id?` / `status?` | 任务/队列概况 | 包装现有 service |
| `generate_spec_docs` | 生成三件套规范文档 | 对话上下文（内部透传） | spec/tasks/checklist 三件套 | 包装 SpecBuilderAgent |

## 6. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| 无使用中 LLM 配置 | 自动调度回退纯规则；对话技能返回明确错误提示，文档生成链路不受影响 |
| AI 决策输出含白名单外节点 ID | 该任务回退负载升序分配，不重试；审计标记 fallback=true |
| AI 决策超时（8s）/ 调用异常 | 本轮全部 AI 决策任务回退规则分配，调度循环不阻塞 |
| 技能执行异常 | step_finished(status=failed) 下发，LLM 收到错误结果可自行调整或向用户说明 |
| 技能被禁用 | Orchestrator 工具集动态过滤，LLM 看不到该工具，自然改用其他方式回答 |
| function calling 轮数超上限 | 达到 ORCHESTRATOR_MAX_TOOL_ROUNDS 后强制要求 LLM 给出最终回答 |
| 前端旧版本 | 未识别的 SSE step 事件按现状忽略，不影响 chunk/specification 渲染 |

## 7. 验收标准

- [ ] 对话中说"查看空闲节点"，消息内出现步骤卡片（工具名/参数/结果/耗时），
      可折叠展开。
- [ ] 对话中说"派发任务到最合理的节点"，AI 调用 smart_dispatch 完成派发
      并给出理由，任务列表可见分配结果。
- [ ] 对话中描述开发需求，三件套文档照常流式生成（specification_delta
      体验与升级前一致）。
- [ ] 「技能」页展示 5 个 MVP 技能；禁用某技能后对话中 AI 不再调用它。
- [ ] AI_DISPATCH_ENABLED=false（默认）时调度行为与现状完全一致（回归）。
- [ ] 开启 AI 调度后：候选唯一的任务不产生 LLM 调用（token 消耗为 0）；
      多候选任务走批量决策；dispatch_decisions 表有完整审计记录。
- [ ] 模拟 LLM 输出非法节点 ID → 任务回退规则分配且不重试，审计标记
      fallback=true。
- [ ] 无 LLM Key 时系统正常启动、正常调度（纯规则），对话技能给出明确降级提示。
- [ ] 全部新增端点/事件按 logging-standards 输出结构化日志；新增错误码
      已回填 api-standards §3。

## 8. 后续迭代（本期不做）

- **子 Agent（tilacode）任务执行步骤回流对话**：Agent 端 todo/执行事件
  映射为对话步骤卡片，需要 Agent 端改造与会话关联机制，本期仅在中枢
  预留 SSE 事件扩展空间。
- 用户自定义技能（动态注册）、技能调用统计报表、纯 AI 全权调度模式。

## 9. 关联文档

- [ADR-033](../adr/033-ai-skills-and-smart-dispatch.md)
- [032-concurrent-task-scheduling-spec.md](./032-concurrent-task-scheduling-spec.md)（预筛逻辑来源）
- [010-task-creation-spec-chat-design.md](./010-task-creation-spec-chat-design.md)（对话页与三件套链路）
- [030-llm-models-management-spec.md](./030-llm-models-management-spec.md)（模型配置复用）
- [api-standards.md](../standards/api-standards.md) / [database-standards.md](../standards/database-standards.md) / [logging-standards.md](../standards/logging-standards.md)
