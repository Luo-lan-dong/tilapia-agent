# 实施范围：待办先行与派发确认门禁（ADR-067）

| 模块 | 状态 | 涉及文件（预估） | 验收 |
|---|---|---|---|
| M1 迁移 | ✅ 完成（2026-10-07） | Alembic `20261007_todo_first_dispatch_hold`（todos 加 session_id FK + tasks 加 dispatch_hold）、`app/models/task_orchestrator_todo.py`、`app/models/task.py` | alembic upgrade head |
| M2 待办先行 | ✅ 完成（2026-10-07） | `app/skills/orchestrator_todo.py`（无任务挂会话）、`app/services/orchestrator_todo_service.py`（session 维度写入）、`app/skills/task_creation.py`（迁移待办）、`app/agents/orchestrator_prompts.py`（准则 3 待办真先行） | UT-H1/H2/H4 |
| M3 派发门禁 | ✅ 完成（2026-10-07） | `app/skills/task_creation.py`（置 hold）、`app/services/scheduler.py`（skip）、`app/services/task_service.py`（dispatch_task 清 hold） | IT-H1~H4 |
| M4 收尾 | ✅ 完成（2026-10-07） | 测试 `tests/services/test_orchestrator_wake.py` 增补 + `tests/skills/test_orchestrator_todo_skill.py` 增补 + E2E | 见进度记录 |

## 1. 红线

- 调度器 skip 不得影响 API 直建任务（hold=False）的自动派发；
- 待办迁移失败不阻塞创建任务；
- todo_write 挂任务的既有路径零回归。

## 2. 变更清单

| 文件 | 类型 | 函数/位置明细 | 作用 |
|---|---|---|---|
| `opc-central-dispatch-hub/app/core/config.py` | 增（2026-10-08 第五轮） | Settings 尾部 | 新增 `orchestrator_wake_max_tool_rounds`（默认 120）与 `orchestrator_wake_timeout_seconds`（默认 1800）——唤醒轮独立预算，部署类长流程不再被全局 40 轮/600s 掐断 |
| `opc-central-dispatch-hub/app/agents/orchestrator.py` | 修（2026-10-08 第五轮） | `chat_stream` 签名 + 轮次上限解析 | 加 `max_rounds_override: int \| None` 参数，唤醒轮传入独立上限，普通对话仍走全局默认 |
| `opc-central-dispatch-hub/app/services/orchestrator_service.py` | 修（2026-10-08 第五轮） | `chat_stream` 签名 + 透传 | **关键修复**：包装层 `OrchestratorService.chat_stream` 同步加 `max_rounds_override` 并透传给 `OrchestratorAgent`——此前漏改，唤醒轮调用时报 `TypeError: got an unexpected keyword argument 'max_rounds_override'`，导致唤醒轮 100% 崩溃（wake_round_failed） |
| `opc-web-console/src/hooks/useTaskPlanTracking.ts` | 修（2026-10-08 第五轮） | `poll` 终态分支 + 新增 `pollTodosAfterTerminal` | 任务终态后不再停止轮询，切「只拉 orchestratorTodos」模式（5s 循环）——修复唤醒轮推进待办时右侧面板冻结 |
| `tests/conftest.py` | 修（2026-10-07 第二轮） | `_GUARDED_TABLES` 顺序 | `TaskBuildingSession` 移到 `TaskOrchestratorTodo` 之前——todos 挂会话后 FK 清理序调整 |
| `alembic/versions/20261007_todo_dispatch_hold.py` | 增 | upgrade/downgrade | todos 加 session_id FK + owner CHECK + 索引；tasks 加 dispatch_hold 列（server_default false） |
| `app/models/task_orchestrator_todo.py` | 修 | 列定义区 | task_id 改 nullable=True；加 session_id 列（FK task_building_sessions）；加 `chk_task_orchestrator_todos_owner` CHECK + `idx_task_orchestrator_todos_session_seq` 索引 |
| `app/models/task.py` | 修 | Task 列定义区（~199-205 行） | 加 `dispatch_hold` Boolean 列（NOT NULL，server_default false） |
| `app/services/orchestrator_todo_service.py` | 修 | `_validate_owner`/`_owner_where`/`list_todos`/`add_todos`/`update_todo`/`remove_todo` | owner 双维度改造：task_id/session_id 互斥挂载；签名改为 task_id 位置参数 + session_id kw-only |
| `app/services/orchestrator_todo_service.py` | 增 | `migrate_session_todos(session, session_id, task_id)` | 会话待办迁移到新任务（seq 拼接续号），创建任务成功后调用 |
| `app/skills/orchestrator_todo.py` | 修 | `_resolve_owner`/description | 无任务时挂当前会话（待办先行）；无会话且无任务才报错；描述改为「收到执行型需求后第一件事 add 全计划」 |
| `app/skills/task_creation.py` | 修 | request 构造区（~195-196 行）+ create 成功后 | 传 `dispatch_hold=True`；调用 `migrate_session_todos` 迁移会话待办（try/except 旁路不阻塞创建） |
| `app/services/scheduler.py` | 修 | `dispatch_pending_tasks` where（~551-552 行） | 加 `Task.dispatch_hold.is_(False)`——调度器跳过待确认任务 |
| `app/services/task_service.py` | 修 | `dispatch_task`（~1308-1310 行） | 派发即确认：清除 dispatch_hold 并落 execution_target |
| `app/services/task_service.py` | 修 | `_create_task_from_planning` Task 构造（~441 行） | 透传 `dispatch_hold=request.dispatch_hold` |
| `app/services/task_service.py` | 修 | `_create_task_from_spec` Task 构造（~348 行） | 透传 `dispatch_hold=request.dispatch_hold`（修集成测试发现的 spec 路径漏传） |
| `app/schemas/task.py` | 修 | `TaskCreateRequest`（~77-80 行）/`TaskResponse`（~187-190 行） | 两模型加 `dispatch_hold` 字段 |
| `app/schemas/task.py` | 修 | `OrchestratorTodoListResponse` | task_id 改可选 + 加 session_id 字段（双维度响应） |
| `app/api/v1/endpoints/task_building_session.py` | 增 | `list_session_orchestrator_todos`（GET /tasks/build/sessions/{id}/orchestrator-todos） | 会话维度只读待办端点：任务创建前前端展示挂会话的待办 |
| `opc-web-console/src/types/task.ts` | 修 | `OrchestratorTodoListResponse` | task_id 改可选 + session_id 可选（对齐后端） |
| `opc-web-console/src/api/tasks.ts` | 增 | `fetchSessionOrchestratorTodos` | 会话维度待办取数 |
| `opc-web-console/src/hooks/useSessionOrchestratorTodos.ts` | 增 | hook 全文 | 会话阶段 5s 轮询挂会话的中枢待办（失败保上次结果） |
| `opc-web-console/src/pages/TaskNew.tsx` | 修 | import 区 / 130-138 行 / 面板传参（~747 行） | 会话阶段（无 ?task=）用会话维度待办，任务阶段用任务维度 |
| `opc-web-console/src/hooks/useTaskBuildingChat.ts` | 修 | step 事件处理（~306-328 行） | `?task=` 对齐信号扩展：create_task 成功的 result_data.task_id 也记录（ADR-067 后待办挂会话，create_task 成为任务创建后的唯一对齐信号） |
| `opc-web-console/src/components/TaskNewRightPanel.tsx` | 修 | summary 分支（~211-234 行） | 无任务但有待办时渲染 OrchestratorTodoSection（此前被「暂无进行中的任务」空态遮挡）；容器复用 task-summary 样式修贴边 |
| `app/services/orchestrator_todo_service.py` | 增 | `reset_todos` / `reset_in_progress` / `reset_in_progress_for_chat_session` | reset=清空重建整份清单（修两批叠加）；中断/取消后 in_progress 重置 pending（按会话绑任务解析 owner） |
| `app/skills/orchestrator_todo.py` | 修 | description/parameters/execute/`_reset` | 新增 action=reset（计划细化/变更时整份重建，禁止 add 叠加） |
| `app/agents/orchestrator_prompts.py` | 修 | 准则 3 | 补充「计划细化/变更必须 reset 重建，小幅调整才用 add/update/remove」 |
| `app/agents/orchestrator.py` | 修 | chat_stream 异常链（~1195-1215 行） | 捕获 CancelledError（用户点停止）→ 重置该会话 in_progress 待办 → re-raise |
| `app/services/task_service.py` | 修 | `cancel_task`（~888-899 行） | 任务取消时重置该任务 in_progress 中枢待办（旁路） |
| `tests/conftest.py` | 修 | `_GUARDED_TABLES` 顺序 | ADR-067 后 todos 可挂会话（session_id FK）：清理序须先 todos 后 sessions，forward 序 sessions 移到 todos 之前（修用户真实会话待办挡守卫清理） |
| `tests/skills/test_orchestrator_todo_skill.py` | 增 | test_reset_replaces_entire_list / test_reset_without_items_fails / test_reset_in_progress_for_chat_session | reset 替换/缺 items 报错/中断重置（挂会话+绑任务两场景） |
| `opc-web-console/src/index.css` | 修 | `::-webkit-scrollbar`（~82-95 行） | 滚动条 8px → 6px（thumb 圆角 4→3，用户反馈偏粗） |
| `app/services/task_event_handler.py` | 修 | `_handle_task_completed`/`_handle_task_failed` | 任务终态清 `progress_stage/progress_message/progress_tool`（修「完成后动作卡仍显示编写中」） |
| `app/agents/orchestrator.py` | 增 | `_load_todos_block` + `_build_messages(todos_block)` 参数 | 每轮 system 注入中枢待办实时清单（修「中枢不知道上轮列了什么」——跨轮/唤醒轮凭记忆重列的根因）；4 处调用点透传 |
| `app/agents/orchestrator_prompts.py` | 修 | 准则 12（新增） | 派发后停等唤醒：跟踪项置 start 即结束本轮，任务终态自动唤醒后 complete 并推进（不轮询） |
| `opc-agent-programming/tilacode/packages/opencode/src/tool/todowrite.txt` | 修 | Rules 段 | 强化实时更新要求（CRITICAL：状态变化立即调用，外部看板依赖；修子 Agent 待办批量最后才上报） |
| `opc-agent-programming/pyproject.toml` | 修 | version | 0.6.6 → 0.6.7（节点重新构建发布） |
| `opc-web-console/src/components/TaskSummary.tsx` | 修 | progressPercent 计算 + CurrentAction 渲染条件 | 进度条优先按子 Agent 待办完成率（无待办回退 task.progress，completed 强制 100）；动作卡仅 running/assigned 显示 |
| `opc-web-console/src/components/TaskSummary.tsx` | 修 | progressPercent 公式（实测修订） | 完成率会因子 Agent 中途重列清单归零（实测 90→0→75→13 大起大落）——改为 `max(task.progress, 完成率)` 单调不回退 |
| `app/services/orchestrator_wake_service.py` | 修 | `build_wake_message(agent_todos_incomplete)` / `_load_wake_context` 返回 | 唤醒消息注入子 Agent 待办未完成明细——任务被节点提前报完成时（实测 6/8），明确警告编排 AI 核实而非轻信验收通过 |
| `app/services/orchestrator_wake_service.py` | 修 | `build_wake_message` 文案 | 修「已已完成」拼接 bug（state 已含「已」字） |
| `opc-agent-programming/app/executor/task_bridge.py` | 修 | `_build_prompt` | 待办执行纪律从 prompt 末尾**前移到最前**并强化为「第一个动作」（实测子 Agent 90% 才首次调 todo，末尾纪律对 deepseek 约束力不足）；版本 0.6.7 → 0.6.8 构建，两节点已部署 |
| `app/services/orchestrator_wake_service.py` | 修 | `build_wake_message` 指令 2)/3) | 唤醒后把**可自主推进的待办逐项继续完成**（start→执行→complete），仅用户决策类事项才停下等待（2026-10-07 用户裁决） |
| `opc-web-console/src/hooks/useWakeMessagesSync.ts` | 增 | hook 全文 | 唤醒轮消息实时同步：任务终态后 3s 轮询会话库，把本地缺失的唤醒回合消息（system_wake 触发 + AI 回复含步骤卡）按序追加到对话区（role+content 判重） |
| `opc-web-console/src/types/taskBuilding.ts` | 修 | `ChatMessage.source` | 加可选 source 字段（识别后端落库的 system_wake 消息） |
| `opc-web-console/src/pages/TaskNew.tsx` | 修 | import + 140-160 行 | 集成 useWakeMessagesSync：终态 + 非发送中启用，session 取 trackedTask.session_id ?? currentSessionId |
| `app/services/wake_stream_service.py` | 增 | 服务全文 | 唤醒轮事件流广播注册表（steering_service 同款模式）：open/publish/close/subscribe，close 置 None 哨兵唤醒订阅者，无活跃轮订阅立即 done |
| `app/services/orchestrator_wake_service.py` | 修 | `_execute_wake_round` 消费循环 | 打开广播队列，非内部事件（非 `_` 前缀）逐个 publish，finally close |
| `app/api/v1/endpoints/task_building_session.py` | 增 | GET /sessions/{id}/wake-stream（SSE） | 订阅唤醒轮事件流实时转发；无活跃轮立即 done 不悬挂 |
| `opc-web-console/src/api/taskBuilding.ts` | 增 | `subscribeWakeStream` | GET SSE 订阅（fetch + ReadableStream 解析，done 即收尾） |
| `opc-web-console/src/hooks/useTaskBuildingChat.ts` | 修 | `applyChatStreamEvent`（抽出共享）+ 暴露 | thinking/chunk/step 渲染逻辑抽为共享函数，正常对话与唤醒流同一套渲染行为 |
| `opc-web-console/src/hooks/useWakeStream.ts` | 增 | hook 全文 | 订阅唤醒事件流 → onEvent 喂给 applyChatStreamEvent；done/断开后 4s 重订阅捕捉下一次唤醒 |
| `opc-web-console/src/pages/TaskNew.tsx` | 修 | 162-173 行 | 集成 useWakeStream（与 useWakeMessagesSync 并存：流负责实时渲染，轮询兜底补触发消息与漏收历史） |
| `app/agents/orchestrator_prompts.py` | 修 | 准则 3 | 「待办真先行（ADR-067）：第一件事 todo_write(add) 列全计划，无需先建任务」 |
| `tests/skills/test_orchestrator_todo_skill.py` | 修 | `test_without_bound_task_fails_and_writes_nothing` → 拆两用例 | 改 `test_without_task_attaches_to_session`（挂会话成功）+ `test_without_session_and_task_fails_and_writes_nothing` |
| `tests/skills/test_create_task_skill.py` | 增 | `test_create_task_sets_dispatch_hold_and_migrates_session_todos` | 集成断言：hold=True + 会话待办迁移 + 无残留 |

## 3. 进度记录

| 日期 | 模块 | 内容 | 验收结果 |
|---|---|---|---|
| 2026-10-07 | — | 立项（用户两截图实测：待办后置 + 调度器击穿确认门禁） | 仅文档 |
| 2026-10-07 | M1-M4 | 代码实施完成；集成测试暴露 spec 路径漏传 dispatch_hold（`test_create_task_sets_dispatch_hold_and_migrates_session_todos` 失败），已修 `_create_task_from_spec` | ✅ 回归 9+23+2 passed、ruff 全绿 |
| 2026-10-07 | 真实验证 | ①确定性探针：插入 hold=True+窗口过期任务，2-3 个调度周期后仍 pending 无节点（tmp/probe_dispatch_hold.py）；②真实 LLM 会话探针（tmp/probe_todo_first_hold.py）：轮 1 第一个技能=todo_write 挂会话 7 项 → 轮 2 确认后 create_task hold=True → 会话待办迁移归属任务 → 25s 调度器不派发 | ✅ 三断言全过 |
| 2026-10-07 | 前端配套 | 待办先行补展示链路：新增会话维度待办端点 + `useSessionOrchestratorTodos` hook + TaskNew 会话阶段接面板（任务创建前也能看到中枢待办） | ✅ tsc 零错误、前端 13 passed、端点探针（空/两条/404）全过 |
| 2026-10-07 | 面板遮挡修复 | 用户实测反馈「还是没有看到」：根因①`TaskNewRightPanel` 空态遮住待办段；②`?task=` 对齐只认 todo_write（挂会话后不再带 task_id）。修两处 + 浏览器实测用户真实会话（a7ecf124，10 项待办）面板正常显示 | ✅ tsc 零错误、前端 18 passed、浏览器实测通过 |
| 2026-10-07 | 第二轮实测修复 | 用户实测：①两批待办叠加（确认需求后 AI 重列 add 未清旧的）→ skill 加 action=reset + 提示词强制；②终止后「进行中」残留 → 对话流 CancelledError 与 cancel_task 两个入口重置 in_progress；③面板贴边 → 复用 task-summary 容器；④滚动条 8px 偏粗 → 全局收窄 6px；顺带修 conftest 守卫表清理顺序（todos 挂会话后 FK 顺序失效） | ✅ 后端 12 passed、ruff 全绿、tsc 零错误、中枢已重启 |
| 2026-10-07 | 第三轮实测修复 | 用户实测：①子 Agent 待办滞后（快结束才显示）→ 节点 todowrite.txt 强化实时更新（0.6.7 构建，两节点已部署）+ 前端进度条按待办完成率；②中枢不知道上轮待办 → 每轮 system 注入清单实时状态；③卡「跟踪节点执行」→ 准则 12 停等唤醒（配合 ADR-066）；④完成仍显「编写中」→ 终态清 progress 字段 + 前端动作卡仅执行中显示 | ✅ 0.6.7 两节点部署 active、tsc/ruff 全绿、浏览器实测（动作卡消失+100%）、中枢已重启 |
| 2026-10-07 | 第四轮实测修复 | 用户 0.6.7 实测：①进度大起大落（90→0→75→13，子 Agent 中途重列清单致完成率归零，max 公式回归）→ 前端改 `max(task.progress, 完成率)` 单调不回退；②子 Agent 待办 6/8 任务却报完成、唤醒轮轻信 → 唤醒消息注入未完成明细并强制核实（`build_wake_message(agent_todos_incomplete)`）；③确认 0.6.7 已编译部署（用户问需否重编译——已做） | ✅ 轻量探针 PASS、ruff/tsc 全绿、中枢已重启；节点侧「提前收尾即报完成」硬校验留作后续 |
| 2026-10-07 | 机制答疑与追问修复 | 用户追问：①待办生成时机=子 Agent 首次调 todowrite（0.6.7 实测 90% 才首调）→ 节点 `_build_prompt` 纪律前移+强化为「第一个动作」，0.6.8 构建部署两节点；②中枢唤醒轮其实**已执行且落库**（[系统自动唤醒] user 消息 + 8 步 assistant 回复在会话库中），但后台轮不实时推前端流、需刷新会话可见 → 顺带修唤醒文案「已已完成」bug | ✅ 0.6.8 两节点部署 active、ruff 全绿、中枢已重启 |
| 2026-10-07 | 唤醒可视化（用户裁决） | 「唤醒后中枢继续工作应像正常对话一样实时显示在对话区」：新增 `useWakeMessagesSync`（终态后 3s 轮询会话库，增量追加唤醒回合消息到对话区，role+content 判重）+ 唤醒指令强化（可自主推进的待办逐项完成，仅用户决策类事项停下等待）；浏览器实测用户真实唤醒会话：触发消息 + 8 步回复正确同步渲染、无重复 | ✅ tsc 零错误、探针 PASS、浏览器实测通过、中枢已重启 |
| 2026-10-08 | 唤醒真流式（用户二次裁决） | 「要实时流式，和用户主动发消息一样」：后端新增 `wake_stream_service` 广播注册表 + SSE 订阅端点（无活跃轮立即 done），唤醒轮逐事件 publish；前端 `useWakeStream` 订阅 + `applyChatStreamEvent` 共享渲染（与正常对话同一套 thinking/chunk/step 渲染），轮询同步降为兜底 | ✅ tsc 零错误、前端 18 passed、探针（HTTP 无活跃轮 + 进程内广播往返）PASS、中枢已重启 |
| 2026-10-08 | 第五轮实测修复（用户离席自主跑） | 问题①「中枢无法拉取节点产物」真相：产物同步成功（export.zip 41KB 全部落盘 data/artifacts/），AI 已自主推进部署（宝塔上传/解压/建库/装依赖），真正卡点是**唤醒轮被 600s 超时+40 轮上限强制收尾掐断**（部署 13 步长流程做不完）→ `config.py` 新增 `orchestrator_wake_max_tool_rounds=120` + `orchestrator_wake_timeout_seconds=1800`，`orchestrator.py` chat_stream 加 `max_rounds_override`，`_execute_wake_round` 接入；问题②右侧面板终态冻结：`useTaskPlanTracking` 终态后停止轮询 → 改切 `pollTodosAfterTerminal` 只拉中枢待办（5s 循环），唤醒轮推进待办实时可见。**自主验证中暴露关键 Bug**：包装层 `OrchestratorService.chat_stream` 漏透传 `max_rounds_override` → 唤醒轮 100% 崩溃 `TypeError`（wake_round_failed）→ 补透传 | ✅ **完整 E2E 全绿**：待办先行（首技能=todo_write）✓、create_task 后 hold=True ✓、派发节点执行 completed ✓、唤醒轮推进待办 5/5 无 in_progress 残留 ✓、export.zip 落盘 ✓、**唤醒流实时广播 3102 事件（thinking=2256 / step 17+17 / chunk=726 / done=71）** ✓；tsc 零错误 |
| 2026-10-08 | 环境故障排查（自主） | 节点心跳冻结 + dispatch 卡死 8 分钟：`pg_stat_activity` 诊断出**数据库锁级联**——两个卡死的 pytest 进程持 `idle in transaction`（含 `DELETE FROM system_settings`）16 分钟，堵死 agent_nodes 心跳 UPDATE 与 tasks UPDATE → 杀 pytest 释放锁、重启中枢（清混杂旧进程）后全线恢复 | ✅ 两节点心跳恢复正常、dispatch 立即返回 assigned、E2E 通过 |
