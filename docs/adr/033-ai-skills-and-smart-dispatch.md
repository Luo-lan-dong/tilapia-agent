# ADR-033: 中枢 AI 技能体系与智能调度（Orchestrator 统一对话 + Skills + AI 派发决策）

## 状态

Accepted

## 日期

2026-09-14

## 问题背景

当前中枢的 AI 能力与调度能力存在两个断层：

1. **AI 能力单一**：中枢唯一的 AI 能力是 SpecBuilderAgent 规范文档生成
   （ADR-010），对话页面只会"写文档"。用户期望中枢成长为通用 AI Agent：
   能查节点、派任务、答状态，且每一步执行可追溯。
2. **调度纯规则**：任务分配由 `scheduler._find_best_available_node` 纯 SQL
   规则决策（ADR-032：在线 + 资源水位预筛 + 并发槽位 + 负载升序）。规则能
   保证"负载最低"，但无法做综合权衡（任务特征匹配、任务链工作目录亲和
   （ADR-028）、相关任务避扎堆），也无法给出人类可读的分配理由。

用户明确诉求（2026-09-14 讨论确认）：

- 中枢可调用**工具查看当前在线节点**，由 AI 自主选择"负载最低、最合理"
  的节点分配任务（工具需要新开发）。
- 核心担忧不是 LLM 故障（LLM 挂了后续步骤本来就无法执行），而是
  **输出不可控（幻觉）白白浪费 token**。
- 侧边栏需要「技能」管理页：查看每个技能的用途、参数、返回结构、启用状态。
- 对话中要像 Trae 一样**逐步展示技能执行过程**（调用什么工具、参数、结果），
  每一步有迹可循。

本决策同时修订此前的硬约束「任务规划用 DeepSeek、任务调度用规则引擎」：
调度仍保留规则引擎作为**安全边界与兜底**，AI 在规则预筛出的合格候选集内
做增强决策。

## 决策

与用户确认（2026-09-14）：

1. **技能框架（工具型定位）**：技能 = 中枢 AI 在对话中可自主调用的工具。
   新增 `app/skills/` 模块：`Skill` 抽象基类（name / description /
   parameters JSON Schema / returns 说明 / execute），代码注册 + 数据库
   `hub_skills` 表存启用开关。MVP 不支持用户自定义技能、不做调用统计。
2. **统一对话链路**：新增 `OrchestratorAgent`（function calling 循环）作为
   「新建对话」页的统一 AI 入口；现有文档生成能力封装为
   `generate_spec_docs` 技能（内部复用 SpecBuilderAgent，
   `specification_delta` 流式体验不变）。否决"双轨渐进 + 意图路由"方案。
3. **MVP 技能清单（5 个）**：
   - `find_idle_nodes` 查找空闲节点（复用 ADR-032 预筛逻辑泛化）
   - `smart_dispatch` 智能派发任务（内部走 AI 决策链路）
   - `get_node_metrics` 节点资源详情
   - `get_task_status` 任务状态/队列概况
   - `generate_spec_docs` 生成三件套规范文档（包装 SpecBuilderAgent）
4. **混合调度模式（方案 B 优化版，幻觉无害化）**：
   - SQL 规则预筛合格候选集（ADR-032 逻辑原样保留为安全边界）；
   - 候选唯一 → 直接规则分配，**跳过 LLM（零 token 快速路径）**；
   - 候选 >1 且开关开启 → 本轮 pending 任务**批量合并为一次** LLM 调用，
     AI 只能在候选 ID 枚举白名单内选择并给出理由；
   - 输出非法（幻觉 ID）/ 超时 / 异常 → **直接回退负载升序排序，不重试**
     （重试 = 双倍 token）；
   - 上下文瘦身：仅传候选节点关键字段（名称/CPU/内存/在跑数/负载分/类型）。
5. **AI 决策默认关闭**：`AI_DISPATCH_ENABLED` 默认 `false`，系统设置页可
   开启；无使用中 LLM 配置时自动降级为纯规则调度（保持"缺 Key 不影响系统
   启动与调度"的既有约束）。
6. **决策审计**：新增 `dispatch_decisions` 表，记录每次决策的任务、候选集
   快照、AI 原始输出、校验结果、是否回退、最终选择与理由、耗时——调度侧
   的"每一步有迹可循"。
7. **执行链路可视化**：SSE 新增 `step_started` / `step_finished` 事件
   （技能名、参数摘要、结果摘要、耗时、成功/失败），前端在对话消息内渲染
   可折叠步骤卡片。**子 Agent（tilacode）任务执行步骤回流对话本期不做**，
   列入后续迭代（见 Spec §8）。
8. **技能管理页**：侧边栏新增「技能」菜单 → `/skills` 页面：技能列表
   （名称/一句话描述/参数说明/返回值说明/启用开关）+ 详情（完整 JSON
   Schema 与返回结构）。技能启用状态影响 Orchestrator 可调用工具集。

明确不做（本期）：

- 用户自定义技能（动态注册 / 上传工具组合）
- 技能调用统计报表
- 子 Agent 执行步骤显示到对话（后续迭代，先写入文档）
- 自动调度 AI 决策的"纯 AI 全权模式"（AI 只在规则预筛后的候选集内决策）

## 备选方案

### 调度智能化深度

| 方案 | 结论 |
|------|------|
| A. 纯 AI 决策：每轮调度把全量数据交给 LLM 直接分配 | 否决。LLM 故障瘫痪调度；每轮必有 LLM 延迟与 token 成本；输出不可控风险最大 |
| B. 规则预筛 + AI 白名单决策 + 规则兜底（本决策） | 采纳。安全边界留在代码，快速路径零 token，幻觉无害化，可审计 |
| C. AI 仅作对话技能，自动调度保持纯规则 | 否决。"自动分配智能化"落空；但对话技能与 B 共用实现，作为 B 的补充场景保留 |

### 对话链路

| 方案 | 结论 |
|------|------|
| 统一链路：OrchestratorAgent 承接全部对话，文档生成技能化 | 采纳。一个入口一套步骤展示，技能调用与文档生成体验一致 |
| 双轨渐进：文档对话保持原链路，技能对话走新链路，入口意图路由 | 否决。意图路由本身引入不确定性，步骤展示两套体验 |

### 其他

| 决策点 | 结论 | 否决项 |
|--------|------|--------|
| AI 调度开关默认值 | 默认关，设置页可开 | 默认开（风险前置） |
| 技能注册方式 | 代码注册 + DB 启用开关 | 全 DB 动态注册（过度设计，Schema 无法保证类型安全） |
| LLM 输出失败重试 | 不重试，直接回退规则 | 重试一次（token 双倍浪费，收益低） |

## 影响

### 对中枢的影响

- 新增 `app/skills/`：`base.py`（Skill 基类与 SkillResult）、
  `registry.py`（注册表 + DB 开关读取）、`node_inspection.py`、
  `task_dispatch.py`、`spec_generation.py`。
- 新增 `app/agents/orchestrator.py`（+ `orchestrator_prompts.py`）：
  function calling 循环，最大工具轮数可配（`ORCHESTRATOR_MAX_TOOL_ROUNDS`，
  默认 8，防死循环）。
- `app/clients/deepseek.py` 扩展 function calling（tools 参数、tool_calls
  响应解析、流式下的工具调用事件透传）。
- `scheduler.py` 改造：`_find_best_available_node` 泛化为
  `_list_available_nodes`（返回全部合格候选）；`dispatch_pending_tasks`
  增加批量 AI 决策钩子（开关关闭/无 Key/候选唯一时行为与现状完全一致）。
- 新增决策审计 service 与 `dispatch_decisions` 表读写。
- 对话端点（task_building SSE）切换到 OrchestratorAgent，SSE 事件扩展。

### 对数据库的影响

- 新表 `hub_skills`：`skill_key`（唯一）、`name`、`description`、
  `enabled`、时间戳；Alembic 迁移 + 5 个 MVP 技能种子数据。
- 新表 `dispatch_decisions`：`task_id`、`batch_id`（同轮批量决策标识）、
  `candidates`（JSONB 候选快照）、`ai_raw_output`、`ai_choice_node_id`、
  `validated`、`fallback`、`final_node_id`、`reason`、`duration_ms`、
  `created_at`。
- `system_settings` 复用现有机制存 `ai_dispatch_enabled`（热更新）。

### 对协议的影响

- SSE 新增事件：`step_started`、`step_finished`（api-standards §4.3 登记）。
- 新增 REST 端点：`GET /api/v1/skills`、`GET /api/v1/skills/{skill_key}`、
  `PATCH /api/v1/skills/{skill_key}`（启用/禁用）、
  `GET/PUT /api/v1/settings/ai-dispatch`。
- 新增业务错误码分段 `204xxx`（技能域）：技能不存在等（实施时按
  api-standards §3 回填具体码表）。

### 对前端的影响

- 侧边栏新增「技能」菜单与 `/skills` 页面（列表 + 详情抽屉 + 启用开关）。
- `TaskNew` 对话消息内新增可折叠步骤卡片组件（对标 Trae 执行轨迹 UI）。
- 系统设置页新增「AI 智能调度」开关（含说明与降级提示）。

### 对 Agent 节点端的影响

- **零改动**。智能化全部在中枢侧完成。

### 兼容性

- **AI 开关关闭（默认）**：调度行为与现状完全一致（回归保障）。
- **无 LLM Key**：AI 决策自动降级规则调度；对话技能不可用时文档生成链路
  保持可用（generate_spec_docs 复用现有配置读取逻辑）。
- **LLM 幻觉**：白名单校验失败即回退，最坏代价 = 一次 LLM 调用的 token +
  8s 超时预算，绝不会派错节点。
- **旧会话**：task_building_sessions 历史数据结构不变，Orchestrator 直接
  读取兼容。

## 关联文档

- [ADR-032 节点并发任务调度](./032-concurrent-task-scheduling.md)（预筛逻辑保留为安全边界，本决策在其上叠加 AI 决策层）
- [ADR-030 多模型管理](./030-llm-models-management.md)（Orchestrator/AI 调度复用"使用中模型"配置）
- [ADR-028 任务工作目录绑定](./028-task-workspace-binding-and-artifact-sync.md)（任务链亲和的依据）
- [ADR-010 任务创建 Spec Chat](./010-task-creation-spec-chat.md)（文档生成能力来源，本决策将其技能化）
- [033-ai-skills-and-smart-dispatch-spec.md](../spec/033-ai-skills-and-smart-dispatch-spec.md)
- [033-ai-skills-and-smart-dispatch-harness.md](../spec/033-ai-skills-and-smart-dispatch-harness.md)
- [033-ai-skills-and-smart-dispatch-changes.md](../spec/033-ai-skills-and-smart-dispatch-changes.md)

## 修订记录

- **2026-09-16（ADR-036）**：`create_task` 技能创建任务时由服务层自动挂接同会话最近任务为父任务（显式 `?parent=` 优先），`change_brief` 自动填充；提示词新增三件套按需生成策略（简单增量修改不调用 `generate_spec_docs`）；新增 `/spec` 精确命令确定性强制执行文档生成。详见 [ADR-036](./036-session-task-chain-and-spec-command.md)。
