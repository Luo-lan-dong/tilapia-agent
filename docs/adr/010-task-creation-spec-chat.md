# ADR-010：任务创建 Spec Chat 多轮对话构建

## 状态

Accepted

## 日期

2026-08-07

## 问题背景

OPC 中央调度中枢的「新建任务」功能在 ADR-007 仪表盘 MVP 阶段只提供一个自然语言输入框：用户输入一句话后，系统直接调用 Planning Agent 生成任务并落库。该单轮输入模式在 MVP 验证中暴露出三类问题：

1. **意图理解偏差**：用户一句话难以表达完整上下文（技术栈、优先级、验收标准），Planning Agent 输出的任务目标与用户期望偏离，往往需要多轮沟通修正。
2. **缺少可审核的中间产物**：用户在任务创建前看不到 AI 生成的计划，无法在落库前纠正偏差，错误任务进入队列后只能人工干预。
3. **派发链路不闭环**：MVP 阶段任务创建后无法手动指定子系统执行，也没有标准化的任务规范推送通道，子系统接入缺乏统一契约。

随着 ADR-008 Linux 编程 Agent MVP 推进，需要一种让用户与 AI 共同把需求澄清到足够准确、并在创建前生成可审核规范文档的任务构建方式，参考 Trae `/spec` 模式引入多轮对话 + 三件套文档预览。

MVP 验证通过后又暴露出新的体验问题：AI 回复非流式，用户等待时间长且无法感知生成进度；对话页面未显示当前模型，用户不确定调用了哪个 LLM；缺少深度思考开关，简单需求也按默认推理强度执行，浪费 token；对话状态随页面刷新丢失，无法回溯和复用历史会话。因此需要在本 ADR 基础上扩展流式输出、模型显示、推理模式切换和历史会话持久化能力。

## 决策

1. **引入 Spec Chat 多轮对话流程**：新增 `/tasks/new` 页面，左侧聊天区与 Spec Agent 多轮对话，右侧实时显示 `spec.md` / `tasks.md` / `checklist.md` 三件套草案，AI 判断信息足够后启用「创建任务」按钮。
2. **新增 SpecBuilderAgent**：基于 DeepSeek（复用 ADR-009 的 LLM Provider 配置），通过结构化 JSON 输出三件套，使用 `response_format={"type": "json_object"}` 强制 JSON 模式，并对模型偶发的字段平铺、Markdown 代码块包裹、字段缺失等异常做兼容与校验。
3. **任务创建时复用 tasks 表扩展字段**：`tasks` 表新增 `specification`(JSONB)、`source`(String，默认 `manual`)、`execution_target`(String，nullable) 三个字段，创建任务时一次性提交最终三件套。同时新增 `task_building_sessions` 表用于持久化历史会话（字段见「数据影响」），创建任务时可选择从会话创建，前端 `/tasks/new` 提供历史会话列表和继续会话入口。
4. **创建任务接口兼容扩展**：`POST /api/v1/tasks` 当请求包含 `specification` 时直接保存（`source="spec_chat"`），不再调用 Planning Agent；不包含时保持原有 Planning Agent 行为，向后兼容。支持通过 `session_id` 从已有会话创建任务。
5. **MVP 派发闭环采用中枢 mock**：新增 `POST /api/v1/tasks/{id}/dispatch` 手动派发接口，由中枢后端通过 httpx 调用 `POST /api/v1/subsystems/{id}/accept-task` mock 接口模拟子系统接收，返回固定 `{"status":"accepted"}`。待各 Agent 子系统开发完毕后，accept-task 接口迁移至各 Agent 节点暴露真实端点，中枢改为正向转发。
6. **LLM 配置走 ADR-009 数据库配置**：`SpecBuilderAgent` 通过 `LLMProviderService.get_active_provider` 从数据库读取 LLM 配置创建 `DeepSeekClient`，不依赖环境变量，支持运行时热更新。
7. **SSE 流式输出**：MVP 阶段 `POST /api/v1/tasks/build/chat/stream` 采用 SSE（Server-Sent Events）推送 AI 回复流，事件类型包括 `chunk`（内容增量）、`specification`（三件套更新）、`status`（ready 状态变更）、`error`（生成错误）。后续如需双向实时通信再迁移 WebSocket。
8. **显示当前对话模型**：在 SSE 首个事件或独立接口中返回当前使用的模型标识（如 `deepseek-chat` / `deepseek-reasoner`），前端在 `/tasks/new` 页面顶部展示模型名称和推理模式状态。
9. **思考模式 / 快速模式切换**：在 `TaskBuildChatRequest` 中新增 `mode` 字段（`fast` / `thinking`），`thinking` 模式通过 DeepSeek 的 `reasoning_effort` 或 `thinking` 参数开启深度思考，`fast` 模式关闭深度思考并使用更短的 prompt 以减少 token；单模型即可实现开关，无需切换模型。
10. **前端测试框架选型**：前端测试体系建设遵循 [ADR-011](./011-frontend-testing-framework.md)，采用 Vitest + React Testing Library + Playwright + MSW。
11. **后端优先开发**：后端（数据库迁移、SSE 接口、历史会话 API、思考模式）完成后并通过 pytest，再进入前端开发（测试框架配置、SSE 对接、历史会话 UI、模式切换）。

## 备选方案

### 方案 A：继续走单轮输入 + Planning Agent 自动拆解（已否决）

- **优点**：实现简单，复用现有 `/tasks` 接口和 Planning Agent，无需新增页面和 Agent。
- **缺点**：用户意图偏差无法在创建前纠正；缺少可审核的中间产物；任务目标不准确导致后续派发与执行全部偏离。
- **否决原因**：MVP 验证已证明单轮输入模式无法满足准确性要求，多轮沟通成本高且无法沉淀规范文档。

### 方案 B：多轮对话 + 三件套文档 + 手动派发 mock（已采纳）

- **优点**：用户与 AI 共同澄清需求，创建前可审核三件套；任务规范以结构化文档形式落库与派发，为后续子系统接入提供统一契约；mock 派发闭环让 MVP 阶段可端到端验证。
- **缺点**：需要新增 SpecBuilderAgent、4 个 API 接口、数据库迁移和前端新页面，开发量比方案 A 大。
- **采纳原因**：解决 MVP 暴露的核心痛点，三件套文档为后续 Agent 子系统执行提供标准输入，mock 派发为真实子系统接入预留契约。

### 方案 C：引入独立的任务构建会话表 + WebSocket 实时推送三件套

- **优点**：会话状态持久化，支持断点续谈；WebSocket 实时推送三件套更新体验更好。
- **缺点**：MVP 阶段过度设计，会话表增加复杂度，WebSocket 推送与现有 REST 轮询架构不一致。
- **否决原因**：MVP 阶段对话通常 3-5 轮即收敛，前端 React state 已足够；三件套在每轮对话后整体返回，无需增量推送。后续如需断点续谈再引入会话表。

## 影响分析

1. **架构影响**：新增 `app/agents/spec_builder.py`（SpecBuilderAgent）、`app/services/spec_builder_service.py`（服务层包装）、`app/api/v1/endpoints/task_building.py`（对话路由）、`app/api/v1/endpoints/task_dispatch.py`（派发 + mock 接收路由）、`app/schemas/task_building.py`（Schema）。扩展 SSE 流式接口 `POST /api/v1/tasks/build/chat/stream`、历史会话 CRUD 接口（`GET/POST/PATCH/DELETE /api/v1/task-building-sessions`）。
2. **数据影响**：
   - `tasks` 表新增 `specification`(JSONB)、`source`(String(32)，默认 `manual`)、`execution_target`(String(32)，nullable) 三个字段，通过 Alembic 迁移 `fe0198a28013` 落地。
   - 新增 `task_building_sessions` 表（Alembic 迁移待创建），字段：`id`（UUID/PK）、`title`（String(255)）、`messages`（JSONB，对话历史）、`specification`（JSONB，当前三件套）、`mode`（String(16)，`fast` / `thinking`）、`model`（String(64)，当前模型标识）、`created_at`、`updated_at`；无用户体系，按全局会话管理。
3. **接口影响**：
   - 保留 `POST /api/v1/tasks/build/chat`（同步对话入口，向后兼容）
   - 新增 `POST /api/v1/tasks/build/chat/stream`（SSE 流式对话入口）
   - 扩展 `POST /api/v1/tasks`（支持 `specification` 和 `session_id`，兼容原有行为）
   - 新增 `GET /api/v1/task-building-sessions`（历史会话列表）
   - 新增 `POST /api/v1/task-building-sessions`（创建会话）
   - 新增 `GET /api/v1/task-building-sessions/{id}`（读取会话）
   - 新增 `PATCH /api/v1/task-building-sessions/{id}`（更新会话标题/内容）
   - 新增 `DELETE /api/v1/task-building-sessions/{id}`（删除会话）
   - 新增 `POST /api/v1/tasks/{id}/dispatch`（手动派发）
   - 新增 `POST /api/v1/subsystems/{id}/accept-task`（MVP mock 接收）
   全部遵循 `api-standards.md` 统一响应格式 `ApiResponse[T]`，SSE 流式接口返回 `text/event-stream`。
4. **前端影响**：
   - 新增/扩展 `src/pages/TaskNew.tsx`：顶部显示当前模型和 `fast/thinking` 模式切换；聊天区接入 SSE 流式渲染；右侧文档区实时更新。
   - 新增 `src/pages/TaskSessions.tsx`（或抽屉/侧边面板）：历史会话列表、继续会话、删除会话。
   - `src/api/taskBuilding.ts` 增加 `taskBuildingChatStream`（EventSource 封装）和历史会话 API 封装。
   - 按 [ADR-011](./011-frontend-testing-framework.md) 引入 Vitest + RTL + Playwright + MSW 测试框架。
5. **测试影响**：
   - 后端：新增 SSE 接口测试（使用 `TestClient` 读取 event stream）、历史会话 CRUD 测试、思考模式参数测试。
   - 前端：新增 `TaskNew` 组件测试（模式切换、SSE 消息渲染、创建按钮状态）、历史会话组件测试、API 层 MSW 测试、Playwright E2E 覆盖对话 → 创建 → 派发完整流。
6. **关联模块影响**：依赖 ADR-009 的 `LLMProviderService` 读取 LLM 配置；`TaskService.dispatch_task` 新增 httpx 调用中枢 mock 接口的逻辑，后续需替换为真实子系统转发；依赖 ADR-011 建立前端测试体系。
7. **安全影响**：三件套内容由 AI 生成，落库前用户可编辑；MVP 阶段不对 Markdown 内容做 XSS 过滤（前端使用 textarea 编辑，不渲染 HTML），后续接入 Markdown 预览组件时需评估 XSS 风险；历史会话全局可见（无用户体系），MVP 阶段不隔离，后续接入用户体系后需按 `user_id` 隔离。

## 实际实施情况

实施过程中相对初版 plan.md 的偏差：

1. **SpecBuilderAgent 构造函数签名调整**：plan.md 中 `__init__(self, client: DeepSeekClient | None = None)`，实际改为 `__init__(self, session: AsyncSession, deepseek_client: DeepSeekClient | None = None)`，通过 `LLMProviderService` 从数据库读取配置创建 client，与 ADR-009 的 LLM 配置热更新能力对齐。
2. **增加 JSON 解析重试机制**：初版 plan.md 仅做一次解析，实际增加了 `MAX_PARSE_RETRIES = 1` 重试，重试时追加 `RETRY_PROMPT` 引导模型严格按 JSON 格式输出，提升 LLM 输出稳定性。
3. **增加字段平铺兼容**：新增 `_normalize_flat_fields` 处理 DeepSeek 偶发将 `spec/tasks/checklist` 平铺在 JSON 顶层的情况，自动组装回 `specification` 嵌套对象。
4. **增加字段校验**：新增 `_validate_parsed` 校验 `ai_reply`、`specification`、`is_ready_to_create` 字段类型，缺字段或类型错误时抛出 `TaskSpecificationError`。
5. **task_dispatch.py 拆分两个 router**：`router`（前缀 `/tasks`）承载 dispatch 接口，`subsystem_router`（前缀 `/subsystems`）承载 accept-task mock 接口，便于后续子系统路由独立演进。
6. **数据库字段长度规范化**：`source` 和 `execution_target` 使用 `String(32)` 而非无长度 `String`，符合 `database-standards.md` 字段长度约束要求。

7. **后续补充的决策（2026-08-07）**：MVP 验证通过后，根据实际体验问题补充以下决策：
   - 新增 SSE 流式输出（`POST /api/v1/tasks/build/chat/stream`），MVP 阶段替代 WebSocket。
   - 在 SSE 首事件中返回当前模型标识，前端顶部展示模型和推理模式。
   - 新增 `mode` 字段（`fast` / `thinking`），通过 DeepSeek 的 `reasoning_effort`/`thinking` 参数控制深度思考，单模型切换。
   - 新增 `task_building_sessions` 表持久化历史会话，推翻原决策点 3 中「MVP 阶段不新增会话表」的断言。
   - 前端测试框架选型独立为 [ADR-011](./011-frontend-testing-framework.md)，采用 Vitest + RTL + Playwright + MSW。

## 关联文档

- [010-task-creation-spec-chat-design.md](../spec/010-task-creation-spec-chat-design.md)
- [010-task-creation-spec-chat-harness.md](../spec/010-task-creation-spec-chat-harness.md)
- [002-central-dispatch-hub-spec.md](../spec/002-central-dispatch-hub-spec.md)
- [003-task-node-state-spec.md](../spec/003-task-node-state-spec.md)
- [009-llm-provider-management.md](./009-llm-provider-management.md)
- [007-web-console-dashboard.md](./007-web-console-dashboard.md)
- [011-frontend-testing-framework.md](./011-frontend-testing-framework.md)
- [api-standards.md](../standards/api-standards.md)
- [database-standards.md](../standards/database-standards.md)
- [backend-component-standards.md](../standards/backend-component-standards.md)
