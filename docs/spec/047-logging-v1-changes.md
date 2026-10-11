# 中枢日志系统 v1 变更清单（ADR-047）

> 关联决策：[ADR-047](../adr/047-logging-v1.md) · [Spec](./047-logging-v1-spec.md) · [Harness](./047-logging-v1-harness.md)
>
> 按规则边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| # | 文件 | 变更类型 | 函数/类级明细 | 作用说明 | 状态 |
|---|------|---------|--------------|---------|------|
| 1 | docs/adr/047-logging-v1.md | 新增 | — | 决策文档 | 已完成 |
| 2 | docs/spec/047-logging-v1-spec.md | 新增 | — | 自然语言 Spec | 已完成 |
| 3 | docs/spec/047-logging-v1-harness.md | 新增 | — | 测试清单 | 已完成 |
| 4 | docs/spec/047-logging-v1-changes.md | 新增 | — | 本清单 | 已完成 |
| 5 | docs/INDEX.md | 修改 | 1.1/1.2/1.3/六 四处登记 | 决策文档索引 | 已完成 |
| 6 | opc-central-dispatch-hub/alembic/versions/20260924_tool_call_logs.py | 新增 | upgrade/downgrade | tool_call_logs 表 + 4 索引（request_id/session_id/task_id/kind+created_at），迁移已应用 | 已完成 |
| 7 | opc-central-dispatch-hub/app/models/tool_call_log.py | 新增 | ToolCallLog / ToolCallLogKind(StrEnum)；created_at 应用侧 UTC default（防同事务 now() 相同致排序不稳） | 执行链路事件表模型 | 已完成 |
| 8 | opc-central-dispatch-hub/app/models/__init__.py | 修改 | 导出 ToolCallLog/ToolCallLogKind | 模型注册 | 已完成 |
| 9 | opc-central-dispatch-hub/app/core/config.py | 修改 | +log_ring_buffer_size(5000)/tool_call_log_retention_days(90)/agent_log_fetch_timeout_seconds(45)/agent_log_fetch_max_lines(2000) | 日志系统配置 | 已完成 |
| 10 | opc-central-dispatch-hub/.env.example | 修改 | +日志系统 4 配置项模板 | 配置模板同步 | 已完成 |
| 11 | opc-central-dispatch-hub/app/core/logging.py | 修改 | +RingBufferHandler（UTC aware 快照，白名单字段+metadata）；+read_ring_buffer/ring_buffer_size；+_create_error_file_handler（WARN+ 归档 30 天）；_resolve_log_dir desktop 兜底 ./logs；configure_logging 增 ring_buffer_size 参数 | 内存缓冲+错误归档 | 已完成 |
| 12 | opc-central-dispatch-hub/app/main.py | 修改 | _setup_logging 传入 ring_buffer_size | 接线配置 | 已完成 |
| 13 | opc-central-dispatch-hub/app/services/tool_call_log_service.py | 新增 | record（独立会话+截断+失败兜底，extra 避开 LogRecord 保留字）/query_events/fetch_trace/purge_expired/serialize_event/register_log_fetch/handle_log_chunk（分块不全防御）/handle_log_error/cancel_log_fetch | 采集+查询+清理+节点日志等待 | 已完成 |
| 14 | opc-central-dispatch-hub/app/agents/orchestrator.py | 修改 | chat_stream：llm_call 成功/失败埋点、compaction 两处（pre_compact/overflow_compact）、_log_steering 四处 drain 埋点、doom 跳过 skill_call 埋点；_run_tool_call：skill_call(started)+skill_result 埋点 | 执行链路采集 | 已完成 |
| 15 | opc-central-dispatch-hub/app/services/task_event_handler.py | 修改 | accepted/started/completed/failed 四处 agent_task_event 埋点；+_handle_log_chunk/_handle_log_error + 事件表注册；progress 不落库（防表膨胀，文件日志已有） | 节点任务事件采集+日志回传处理 | 已完成 |
| 16 | opc-central-dispatch-hub/app/services/scheduler.py | 修改 | dispatch_pending_tasks 自动派发 dispatch 埋点；+_purge_tool_call_logs_if_due（每日一次随调度循环） | 派发采集+90 天清理 | 已完成 |
| 17 | opc-central-dispatch-hub/app/services/task_service.py | 修改 | dispatch_task 手动派发 dispatch 埋点 | 手动派发采集 | 已完成 |
| 18 | opc-central-dispatch-hub/app/api/v1/endpoints/logs.py | 新增 | GET /logs（memory/file 两数据源）/GET /logs/trace（422 校验）/POST /logs/agent-fetch（409 离线/504 超时/502 回传失败）/GET /logs/ring-buffer/stats；统一 ApiResponse 包装 | 查询端点 | 已完成 |
| 19 | opc-central-dispatch-hub/app/api/v1/api.py | 修改 | include_router(logs, prefix=/logs) | 路由注册 | 已完成 |
| 20 | opc-central-dispatch-hub/app/schemas/ws_messages.py | 修改 | +HUB_LOG_FETCH/AGENT_LOG_CHUNK/AGENT_LOG_ERROR | WS 协议扩展 | 已完成 |
| 21 | opc-central-dispatch-hub/app/skills/query_logs.py | 新增 | QueryLogsSkill（minutes_back/level/keyword/limit，默认 error+warning，摘要+计数防爆上下文） | AI 自助查日志技能 | 已完成 |
| 22 | opc-central-dispatch-hub/app/skills/registry.py | 修改 | SKILL_REGISTRY +QueryLogsSkill | 技能注册（hub_skills 表无行时兜底全启用，同 history_recall 机制，无需种子迁移） | 已完成 |
| 23 | opc-central-dispatch-hub/tests/conftest.py | 修改 | +_stub_tool_call_log_record autouse（record→内存 spy，_real_record 保留供恢复） | 测试防污染 | 已完成 |
| 24 | opc-central-dispatch-hub/tests/core/test_logging_ring_buffer.py | 新增 | UT-1/UT-2 共 6 项 | 环形缓冲+分级归档测试 | 已完成 |
| 25 | opc-central-dispatch-hub/tests/services/test_tool_call_log_service.py | 新增 | UT-3~6/UT-9 共 11 项（共享会话工厂 savepoint 模式） | 服务层测试 | 已完成 |
| 26 | opc-central-dispatch-hub/tests/api/v1/test_logs.py | 新增 | UT-7/8/9 共 7 项（不触库） | 端点测试 | 已完成 |
| 27 | opc-central-dispatch-hub/tests/skills/test_query_logs.py | 新增 | UT-10 共 6 项 | 技能测试 | 已完成 |
| 28 | opc-agent-programming/app/utils/log_fetcher.py | 新增 | read_recent_log_lines（tail+级别 JSON 行过滤+滚动文件补齐+行数上限） | 节点日志读取 | 已完成 |
| 29 | opc-agent-programming/app/client/hub_client.py | 修改 | +_LOG_FETCH/LOG_CHUNK/LOG_ERROR 常量与分块大小；_handle_message 分发；+_handle_log_fetch（读取→base64 分块回传，空文件/异常回传 log_error）；+send_log_chunk/report_log_error | 下行指令接收与回传 | 已完成 |
| 30 | opc-web-console/src/types/logs.ts | 新增 | LogEntry/LogsResponse/TraceEvent/TraceResponse/AgentLogFetchResponse | 类型 | 已完成 |
| 31 | opc-web-console/src/api/logs.ts | 新增 | fetchLogs/fetchTrace/fetchAgentLogs（60s 超时） | API 封装 | 已完成 |
| 32 | opc-web-console/src/pages/Logs.tsx | 新增 | 三 Tab：原始日志流（内存/文件+级别+关键字+时间区间+展开 metadata）/执行链路（request_id/session_id 溯源，表格+Timeline 瀑布）/节点日志拉取（选节点+级别+行数）；UTC→本地时间显示 | 日志页 | 已完成 |
| 33 | opc-web-console/src/App.tsx | 修改 | /logs 路由（LazyLogs） | 接线 | 已完成 |
| 34 | opc-web-console/src/components/AppLayout.tsx | 修改 | 菜单项「日志」(FileTextOutlined)+getSelectedKey/getPageTitle /logs 分支 | 菜单接线 | 已完成 |

## 实施记录

- 2026-09-24：文档四件套完成，开发启动。
- 2026-09-24：中枢后端全量实施（#6~#23）；新增测试 30 项（环形缓冲 6 + 服务层 11 + 端点 7 + 技能 6），分批运行全过；alembic 迁移 20260924_tool_call_logs 已应用；ruff 全过（存量 3 处非本次文件问题未动）。
- 2026-09-24：Agent 端 log_fetch（#28~#29）与前端日志页（#30~#34）完成，前端 typecheck 通过。
- 2026-09-24：**验证收尾（重启后端 + 实链路验证）**，全部通过：
  - 后端启动正常，新结构化日志格式生效（agent_registered/agent_heartbeat_received 等带 request_id/level_number）；`error.log` 仅收 WARN+（slow_request_detected/agent_offline_detected 实条验证），INFO 不入归档；
  - `GET /logs`：内存源倒序返回 + level/keyword 过滤命中 + file 源兜底解析 + ring-buffer/stats size 正常；`GET /logs/trace` 缺参 422、空表 200；tool_call_logs 表已建（迁移生效，暂无链路事件，录入路径由 11 项服务层单测覆盖）；
  - `POST /logs/agent-fetch`：脚本化 WS 客户端模拟新版 Agent 回传（注册 → WS 建连 → hub.log_fetch 下行 → agent.log_chunk 单块 base64 → 端点返回原文逐字节一致）；旧版节点（在线两节点均旧版）按设计 45s 超时返回 504 明确错误；
  - 浏览器验证 /logs 三 Tab：原始日志流（双源切换/级别/关键字/时间/分页/行展开 metadata JSON）、执行链路（request_id 溯源空态正常）、节点日志（选节点拉取，旧版节点超时提示文案与后端 504 对应）。
- 验证期修正：`ws_messages.py` AGENT_LOG_CHUNK 协议注释 `data_b64` → `data`（线上字段以 Agent 端 send_log_chunk 与中枢 _handle_log_chunk 实现为准，注释此前写错；无功能影响，payload 键名匹配无测试覆盖故单测未暴露）。
- 决策偏差说明：①agent_task_event 未记录 progress 事件（防表膨胀，任务表已有 progress 字段，文件日志保留全程）；②桌面模式日志目录由 launcher 既有 LOG_DIR 注入满足，logging.py 仅补 desktop 兜底分支。
- 2026-09-25：**节点日志 local 环境修复 + 执行链路默认概览**（用户反馈：节点日志报「local 环境无文件日志」、执行链路需手动输 ID 不自动展示、归属方不明确）：
  | 文件 | 变更 | 说明 |
  |------|------|------|
  | opc-agent-programming/app/utils/logger.py | 修改 | +resolve_log_dir(environment)（local/dev→./logs，staging/prod→/var/log/...）；setup_logging 全环境写文件（local 文件用 colors=False ConsoleRenderer，控制台仍带色） |
  | opc-agent-programming/app/utils/log_fetcher.py | 修改 | LOG_DIR 常量→default_log_dir()（按 APP_ENV 解析）；+_line_matches_level 兼容 local 可读文本行 ``[error    ]`` 标记过滤（error 含 critical），JSON 行大小写兼容 |
  | opc-agent-programming/app/client/hub_client.py | 修改 | _handle_log_fetch 显式传 log_dir=resolve_log_dir(config.app_env)；错误文案去「local 环境无文件日志」括注（local 现有文件日志） |
  | opc-central-dispatch-hub/app/api/v1/endpoints/logs.py | 修改 | GET /logs/trace 无链路 ID 时改调 query_events 返回最近事件（时间倒序，limit≤200 默认 50，mode=recent），废除缺参 422；TraceResponse +mode 字段 |
  | opc-central-dispatch-hub/tests/api/v1/test_logs.py | 修改 | UT-8 断言 422→200+mode=recent（test_trace_recent_without_identifier） |
  | opc-web-console/src/types/logs.ts、src/api/logs.ts | 修改 | TraceResponse +mode；fetchTrace +limit 参数 |
  | opc-web-console/src/pages/Logs.tsx | 修改 | 执行链路双态：概览态默认自动加载最近 50 事件（表格+可点击 request_id 溯源+空态文案）；详情态保留表格+瀑布+「返回最近事件」；Tab 内归属说明（中枢侧编排事件+节点任务生命周期，节点本地日志走「节点日志」Tab） |
  | opc-agent-programming/tests/test_log_fetcher.py | 修改 | +test_level_filter_matches_console_lines（local 文本行过滤/裸行跳过/critical 命中） |
  验证：Agent 32 项 + 中枢日志相关 17 项全过；前端 typecheck 过；后端重启后 trace 双模式实测（无参 mode=recent total=10、带 ID mode=trace）、浏览器概览自动展示与点击溯源瀑布全过。**Agent 端改动需再次更新节点整体包后生效。**
- 2026-09-25：**执行链路可读性优化 + AI 自助排障技能扩展**（用户反馈：主体列含义不明、状态需中文、事件缺会话/任务上下文、希望对话中直接查询中枢链路与节点日志）：
  | 文件 | 变更 | 说明 |
  |------|------|------|
  | opc-central-dispatch-hub/app/services/tool_call_log_service.py | 修改 | +NodeLogFetchError（code: offline/send_failed/timeout/agent_error）+request_node_logs（下发 hub.log_fetch→同步等待回传，REST 端点与技能共用） |
  | opc-central-dispatch-hub/app/api/v1/endpoints/logs.py | 修改 | fetch_agent_logs 改调 request_node_logs 并按 code 映射 409/502/504；清理冗余 import |
  | opc-central-dispatch-hub/app/skills/query_trace.py | 新增 | QueryTraceSkill：查 tool_call_logs 最近事件（minutes_back/kind/keyword/limit），摘要行含任务 ID+request_id，提示同 req 可溯源 |
  | opc-central-dispatch-hub/app/skills/fetch_node_logs.py | 新增 | FetchNodeLogsSkill：按 client_id 拉节点本地日志（tail_lines≤2000/level），超长截尾保留最新；失败按 code 返回排查提示 |
  | opc-central-dispatch-hub/app/skills/registry.py | 修改 | SKILL_REGISTRY +QueryTraceSkill/FetchNodeLogsSkill（共 10 技能） |
  | opc-central-dispatch-hub/tests/skills/test_query_trace.py | 新增 | UT-11 共 4 项（摘要行/空态引导/参数透传/非法参数回退） |
  | opc-central-dispatch-hub/tests/skills/test_fetch_node_logs.py | 新增 | UT-12 共 7 项（成功/截尾/四种失败提示/缺参） |
  | opc-central-dispatch-hub/tests/services/test_skill_registry.py | 修改 | 技能总数断言 8→10（含新技能 key 清单） |
  | opc-web-console/src/pages/Logs.tsx | 修改 | 状态中文化（成功/进行中/失败）；「主体」表头 Tooltip（技能名/模型名/节点名/生命周期阶段）；+「任务 / 会话」列（task_id/session_id 短 ID 可复制） |
  验证：日志相关测试 39 项全过；前端 typecheck 过；后端重启后浏览器实测表头/中文状态/会话列正常。
