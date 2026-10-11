# ADR-047: 中枢日志系统 v1（运行日志分层 + 执行链路事件表 + 子 Agent 日志按需拉取）

## 状态

Accepted

## 日期

2026-09-24

## 问题背景

用户核心诉求：**报错可循、执行链路可溯源**。当前缺口：

1. **运行日志只有滚动文件**：structlog 文件按天切割（local 保留 7 天），无内存查询能力；
   排障时只能 grep 文本文件。ERROR/WARN 与海量 INFO 混在同一文件，关键错误易被淹没；
   文件被滚动删除后（7 天）报错原因彻底丢失。
2. **桌面打包模式日志目录错误**：`_resolve_log_dir` 在非 local 环境解析到
   `/var/log/opc/central-hub`（Linux 路径），Windows 桌面版（ADR-021）下不可写/不可见。
3. **工具/执行链路无持久化**：技能调用（skill_call）、LLM 调用、上下文压缩、
   steering、任务派发决策等只存在于滚动文本日志，无法按 request_id/session_id
   结构化拉取完整链路；无法回答「这轮对话到底调了哪些技能、哪一步失败」。
4. **子 Agent 日志全量在节点本地**：中枢只落任务生命周期状态变更（且仅传统
   logger 文本），节点执行报错需要 SSH 上节点翻文件；节点离线后无从追溯。
5. **AI 无法自助查日志**：中枢技能体系（ADR-033）中没有日志查询技能，
   对话遇到报错时模型无法「看一眼刚才发生了什么」来自助定位。

## 决策

与用户确认（2026-09-24），分五部分：

### A. 运行日志三层（保留现有文件，增加内存缓冲与错误归档）

1. **文件日志维持现状**：按天切割，local 保留 7 天、staging/prod 30 天
   （logging-standards §9），不改动切割参数——用户明确「做限制了担心信息被覆盖」，
   因此不做大小切割、不缩短保留期。
2. **内存环形缓冲**：新增 `RingBufferHandler`（`collections.deque(maxlen≈5000)`）
   挂到 root logger，查询 API 直读内存，**最近 5000 条永不落盘丢失**
   （重启清空，可接受——文件日志仍在）。查询 API 默认数据源。
3. **ERROR/WARN 归档**：新增分级 Handler 将 WARNING 及以上单独写 `error.log`
   （按天切割，保留 30 天），报错追溯窗口从 7 天延长到 30 天。
4. **桌面模式日志目录**：`_resolve_log_dir` 增加 frozen/桌面判定，桌面模式
   落 `data/logs`（与 resources/artifacts 等目录同策略，launcher 注入绝对路径）。

### B. 工具/执行链路日志：新表 tool_call_logs（90 天）

新增 `tool_call_logs` 表（迁移 `20260924_tool_call_logs`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| request_id | varchar(64) | 请求链路 ID（structlog contextvars 同源） |
| session_id | varchar(64) | 对话会话 ID |
| task_id | varchar(64) | 关联任务 ID |
| kind | varchar(32) | 事件类型（见下） |
| name | varchar(128) | 主体名（技能名/模型名/事件名） |
| status | varchar(16) | success/failed/started |
| args_summary | text | 参数摘要（截断 2KB，防膨胀） |
| result_summary | text | 结果摘要（截断 4KB） |
| error | text | 失败原因 |
| duration_ms | int | 耗时 |
| created_at | timestamptz | 时间 |

kind 枚举：`llm_call`（LLM 流式调用）/ `skill_call`（技能调用含 doom 跳过与超时）/
`skill_result`（技能结果）/ `compaction`（上下文压缩）/ `steering`（插话提升）/
`dispatch`（任务派发决策）/ `agent_task_event`（子 Agent 任务生命周期）。

溯源 = 按 `request_id` 或 `session_id` 拉全链（时间升序），配合 kind 过滤。
采集写入统一走 `tool_call_log_service.record()`（快照字段 + 截断 + 失败仅告警不抛，
日志采集永不影响业务主流程）。

### C. 子 Agent 日志：中枢落事件 + 按需拉取

1. **中枢落生命周期事件**：`task_event_handler` 的任务生命周期处理处
   （accepted/started/progress/completed/failed）写入 `tool_call_logs`
   （kind=agent_task_event，含 executor_model_actual 等关键信息），
   中枢侧始终有一条可查的任务时间线。
2. **节点全量日志按需拉取**：新增下行指令 `hub.log_fetch`
   （payload: request_id/client_id/task_id/level/start/end/tail_lines），
   Agent 端读本地 TimedRotating 日志文件（按 level 过滤 + tail_lines 截取，
   上限 2000 行）分块回传 `agent.log_chunk` / `agent.log_error`
   （完全仿 ADR-028 file_fetch 的 base64 分块模式）；
   中枢侧 `POST /api/v1/logs/agent-fetch` 同步等待回传（Future 模式，超时 45s），
   日志文本直接随响应返回，中枢**不落盘不缓存**。
   明确不做全量实时上报（网络/存储成本高、节点离线即丢、v1 无场景）。

### D. 查询 API 与前端日志页

- `GET /api/v1/logs`：params `start/end/level[]/kind/keyword/source/limit`
  （默认最近 1 小时；source=memory 内存缓冲（默认）或 file 滚动文件尾部读取）；
- `GET /api/v1/logs/trace?request_id= 或 session_id=`：拉 `tool_call_logs` 链路
  （时间升序，kind 过滤可选）；
- 前端侧边栏新增「日志」页（`/logs`）：时间区间 + 等级多选 + kind + 关键字筛选；
  两视图——**原始日志流**（内存缓冲条目，可展开看结构化字段）与
  **执行链路**（按 request_id/session_id 选，瀑布式时间线）。

### E. AI 技能 query_logs

新增技能 `query_logs`（registry 注册）：参数 `minutes_back`（默认 30）/
`level`（默认 error+warning）/ `keyword` / `limit`（默认 50）；
数据源=内存环形缓冲；返回摘要行 + 命中计数（防爆上下文），
模型据此可回答「刚才为什么报错」。

### 保留策略汇总

| 载体 | 保留 | 说明 |
|------|------|------|
| app.log（INFO 全量） | 7 天（local）/30 天（staging/prod） | 现状不动 |
| error.log（WARN+） | 30 天 | 新增分级 Handler |
| 内存环形缓冲 | 最近 5000 条 | 重启清空 |
| tool_call_logs | 90 天 | 调度器新增每日清理协程 |

## 备选方案

| 方案 | 结论 |
|------|------|
| 子 Agent 日志全量实时上报中枢（WS 流） | 否决：网络与存储成本高、节点离线即丢、99% 日志无人看；v1 按需拉取 |
| 链路事件复用 task_building_sessions.messages JSONB | 否决：会话数据与运维日志混存，90 天清理会破坏会话历史 |
| ELK/Loki 外部日志栈 | 否决：桌面一体化部署（ADR-021）场景过重，引入外部依赖 |
| tool_call_logs 存 JSONB 单列 | 否决：level/kind/时间需 SQL 级过滤与索引，拍平字段查询效率高 |
| 环形缓冲落盘持久化 | 否决：文件日志已承担持久化职责，缓冲只为查询便利，重启丢失可接受 |
| 日志页实时 WebSocket 推送 | 否决（v1）：轮询/按需查询已满足排障场景，推送留待后续 |

## 影响

### 中枢后端

- `app/core/logging.py`：RingBufferHandler + WARN+ 分级 error.log Handler +
  桌面日志目录判定 + 全局读取函数；
- `app/models/tool_call_log.py` + `alembic/versions/20260924_tool_call_logs.py`
  （索引：request_id/session_id/created_at/kind）；
- `app/services/tool_call_log_service.py`（record + 查询 + 90 天清理）；
- `app/agents/orchestrator.py` 埋点（llm_call/compaction/steering/skill_call/skill_result）；
- `app/services/task_event_handler.py` 埋点（agent_task_event）；
- `app/api/v1/endpoints/logs.py`（GET /logs、GET /logs/trace、POST /logs/agent-fetch）；
- `app/schemas/ws_messages.py`：新增 `hub.log_fetch` / `agent.log_chunk` / `agent.log_error`；
- `app/skills/query_logs.py` + registry 注册；
- `app/core/config.py`：`log_ring_buffer_size`（5000）、`tool_call_log_retention_days`（90）、
  `agent_log_fetch_timeout_seconds`（45）、`agent_log_fetch_max_lines`（2000）。

### Agent 端

- `app/client/hub_client.py`：`_handle_message` 分发 `hub.log_fetch`；
- 日志回传 handler：读本地日志文件 → 过滤 → base64 分块回传
  （复用 file_chunk 通道模式，独立事件名）。

### 前端

- 新页面 `LogPage`（路由 /logs + 侧边栏菜单「日志」）；
- `src/api/logs.ts` + `src/types/logs.ts`。

### 兼容性

- 全部为新增能力，无协议破坏性变更；`hub.log_fetch` 为新下行事件，
  旧版 Agent 收到后走 unhandled_event 忽略（已观察日志，无副作用）；
- tool_call_logs 写入失败仅告警，不影响业务。

## 关联文档

- [logging-standards.md](../standards/logging-standards.md)（级别/格式/脱敏/切割基线）
- [ADR-028](./028-task-workspace-binding-and-artifact-sync.md)（file_fetch 分块回传模式，log_fetch 仿此）
- [ADR-033](./033-ai-skills-and-smart-dispatch.md)（技能框架，query_logs 载体）
- [ADR-021](./021-hub-windows-desktop-deployment.md)（桌面模式 data 目录策略）
- [ADR-043](./043-execution-guard-v1.md)、[ADR-044](./044-llm-resilience-v1.md)、
  [ADR-045](./045-orchestrator-steering-v1.md)（埋点对应的执行保护/韧性/steering 事件源）

## 实际实施情况

已全量实施并通过验证（2026-09-24），与决策一致，无方案级偏差：

- **A 运行日志三层**：RingBufferHandler（deque 5000，UTC aware 快照）+ WARN+ 分级 error.log（TimedRotating 30 天）+ 桌面目录兜底分支落地；文件日志切割参数未动。
- **B tool_call_logs**：迁移 20260924_tool_call_logs 应用（4 索引）；record 独立会话 + 2KB/4KB 截断 + 失败仅告警；orchestrator（llm_call/compaction/steering/skill_call/skill_result/doom 跳过）、scheduler（dispatch + 每日清理）、task_service（手动 dispatch）、task_event_handler（agent_task_event，progress 不落库防膨胀）埋点齐全。
- **C 节点日志**：hub.log_fetch 下行 + Agent log_fetcher（tail + 级别过滤 + 滚动文件补齐，上限 2000 行）base64 分块回传；中枢 Future 同步等待 45s；实测旧版节点超时 504、新版协议回传逐字节还原。
- **D 查询与前端**：GET /logs（memory/file 双源）、GET /logs/trace（缺参 422）、POST /logs/agent-fetch（409/502/504 分级）、GET /logs/ring-buffer/stats；前端 /logs 三 Tab 全部落地并浏览器实测通过。
- **E query_logs 技能**：注册入 SKILL_REGISTRY（hub_skills 无行时兜底全启用）。

验证记录详见 [changes 实施记录](../spec/047-logging-v1-changes.md)。实施期发现 AGENT_LOG_CHUNK 协议注释与线上字段名不一致（data_b64 → data），已修正注释；无功能影响。

实施后补充（2026-09-25，用户反馈驱动）：
- **Agent 全环境写文件日志**：原设计 local 仅控制台输出，节点日志拉取在 local 环境恒失败；修正为 local/dev 落 ``./logs/agent.log``（无颜色可读文本），log_fetcher 同步兼容可读文本行的级别过滤（原先仅匹配 JSON level 字段）。
- **执行链路默认概览**：原设计需手动输入 request_id/session_id 溯源；修正为无参时返回最近事件列表（时间倒序，mode=recent，默认 50 条），前端进入 Tab 自动展示、点击事件进入单链瀑布（mode=trace）。归属明确：执行链路=中枢侧编排事件（LLM 调用/技能/压缩/插话/派发）+节点任务生命周期上报；节点本地详细日志走「节点日志」按需拉取。
