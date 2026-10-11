# 实施范围：节点续聊对话（ADR-064）

> 本文是 ADR-064 的**实施范围与逐模块登记表**。规则：**先在本表登记实施范围，再动代码**；
> 每完成一个模块，回填「状态 / 验收结果 / 涉及文件」，并同步 `docs/INDEX.md` 与 `docs/changelog/`。

## 0. 本次范围（M1–M4）

| 模块 | 状态 | 涉及文件（预估） | 验收命令 / 观察点 |
|---|---|---|---|
| M1 中枢 | ✅ 完成（2026-10-05） | `app/services/task_service.py`（`start_node_chat` 建任务路径删除 → 新 `node_chat()`：404/409/422 分支 + trace_task_id/last_seq/reply_to 计算 + **先落库再下发** + `hub.node_chat` 下发；`steer_task` 送达后补落用户消息）、`app/services/task_building_session_service.py`（新 `append_message()`：node-chat/steer/回复三路共用单消息写入，500 条上限、旁路原则失败不抛）、`app/services/task_event_handler.py`（`_handle_node_chat_reply/_handle_node_chat_error` 上行处理 + `_coerce_uuid`）、`app/schemas/ws_messages.py`（`HubNodeChatPayload`/`AgentNodeChatReplyPayload`/`AgentNodeChatErrorPayload`）、`app/api/v1/endpoints/tasks.py`（202 + `ApiResponse[dict]`）、`app/schemas/task.py`（text `min_length=1`）；测试：`tests/services/test_task_node_chat.py` 重写 10 用例 + `test_task_event_handler.py`/`test_task_steer.py`/`tests/api/v1/test_tasks.py` 增补 | `ruff check app tests` 全过；`pytest tests/services tests/api -q` **583 passed**（3 失败经 git stash 基线验证为既有，与本次无关：调度器关闭 flake / 无规范任务 retry 撞既有 specification 校验 / dashboard token 聚合环境性污染）；UT-H1~H5 全绿 |
| M2 节点 | ✅ 完成（2026-10-05） | `app/executor/tilacode_client.py`（`send_prompt` 新增 `delivery` 参数，默认 "steer" 保持现行为）、`app/executor/agent_trace.py`（`AgentTraceCollector` 最小扩展 `start_seq`，默认 0 保持旧行为）、`app/executor/task_bridge.py`（`node_chat()` 协程：busy 并发保护 / 会话 `get_context` 探测（不 create_session 防覆写 agent/model）/ 300s `wait_for` + 超时尽力 interrupt / 私有临时采集器（归属 trace_task_id、start_seq=last_seq、不进 `_trace_collectors`）/ 复用 `SSEConsumer` 完成判定与 `_task_event_pump` 权限兜底泵 / 回复全文+tokens 提取；红线：不进 `_current_tasks` 不改任务状态）、`app/client/hub_client.py`（`hub.node_chat` 分发 + `send_node_chat_reply/error` 上行 + close 清理续聊任务）；新 `tests/test_node_chat.py` 18 用例 | `ruff check app tests` 全过；`pytest tests -q` **270 passed, 0 failed**（248 既有零回归 + 18 新增） |
| M3 前端 | ✅ 完成（2026-10-05） | `src/types/taskBuilding.ts`（`mention_node`/`replyTo`/`reply_to`/`failed` 可选字段）、新 `src/utils/chatMessage.ts`（camel/snake 双形态归一化 `messageMentionNode/messageReplyTo`）、`src/api/tasks.ts`（`NodeChatResponse{reply_to}`）、新 `src/hooks/useNodeChatReplyPoll.ts`（2s 复用会话详情接口轮询 / 320s 上限 / reply_to 去重 / 失败静默重试）、`src/hooks/useTaskBuildingChat.ts`（`appendAssistantMessage` 仅对 assistant 按 reply_to 去重 / `markMessageReplyTo` / `markMessageFailed`）、`src/components/TaskNewChatPanel.tsx`（节点回复徽标复用 `node-trace__source-tag` + 失败标签）、`src/pages/TaskNew.tsx`（分流 + `pendingReplies` 轮询接入 + 失败保留）、`src/pages/TaskNew.css`（`.task-new__failed-tag` 语义变量）；测试：新 `useNodeChatReplyPoll.test.tsx` + `TaskNewNodeMention.test.tsx` 各 4 用例 + 扩展 2 用例 | `tsc -b` 零错误；vitest 相关 **24/24 passed**（既有 7 失败经 stash 基线确认无关） |
| M4 收尾 | ✅ 完成（2026-10-05，含一轮 E2E 循环修复 + 浏览器回归修复） | **测试修复**：`tests/services/test_task_verify_blocked.py` 重写过期用例（旧断言「retry→steer」为 ADR-061 前语义，改为对齐现行「retry→重新派发」：清阻断态 + 摘要拼 change_brief + dispatch 断言，6 passed）。**构建发布**：0.6.1（BOM 坑修复后）→ 0.6.2 → **0.6.3** 三轮构建部署两节点（版本 .NET 无 BOM 替换法）；三查全过（register+websocket_connected / DB 心跳刷新 / 中枢在线）。**E2E 循环修复一处真缺陷**：节点回复提取 `reply_extract_failed`——context 端点解析对续聊轮取不到文本 → 改为 `_run_node_chat_round` 内 `_capture_rows` 直接捕获 SSE 轨迹 text 行全文（所见即所得），context 解析降为回退；`opc-agent-programming/app/executor/task_bridge.py`（`_capture_rows` 同步回调 + dict 行结构访问 + 返回 `{"text","tokens"}` 扁平结构）。**E2E 探针全绿**（`tmp/e2e_probe_064.py`）：真实任务 completed（L1 判据）→ node-chat 202 + reply_to → 续聊轮轨迹 seq 接续（204 行单调无重）→ **节点真实回复落库**（`task_building_sessions.messages` 第 [9] 条 assistant 全文）→ 异常路径 404/409 用例覆盖。**浏览器实测回归修复两项**（首轮 4/5 不通过后修复复验 4/4 通过）：①后端 `ChatMessage` schema 补 `mention_node`/`reply_to` 可选字段（原 Pydantic 序列化丢弃致会话详情 API 不回传来源标注，`app/schemas/task_building.py`）；②前端恢复 `?session=` 直链挂载恢复（ADR-061 曾实现、重构丢失回归；新 `src/hooks/useSessionParamRestore.ts` + `TaskNew.tsx` 接入，会话有 task_id 自动写 `?task=`）。浏览器复验：直链恢复/@笔记本 标注/节点徽标/刷新持久化 **4/4 通过** | 三端回归全绿（中枢 583+6+18 passed / 节点 270 passed / 前端 tsc 零错误 + 24+ 用例）；真实环境 E2E 与浏览器实测均通过 |
| M5 修复批次 | ✅ 完成（2026-10-06，用户实测五问题之③④，0.6.4→0.6.6 三轮构建） | `opc-agent-programming/app/executor/task_bridge.py`：①`_build_prompt` 末尾补 **todo 纪律**（正式动手前用 todo 工具拆 3~8 项计划、开工标进行中、完成即标已完成、收尾确认无遗留——tilacode 工具名经 `tilacode/packages/opencode/src/tool/registry.ts:214` 核实为 `todo`）；②`_run_node_chat_round` 新增**无进展看门狗** `_NODE_CHAT_WATCHDOG_SECONDS=60.0`：轮内活动计数（轨迹行数+进度明细数），`asyncio.wait(consume_task, 60s)` 零增长 → interrupt 清场 + 重发一次 prompt → 再 60s 仍零增长 → cancel 消费 + interrupt + `TaskBridgeError("session_unresponsive")` 快速失败（修复 interrupt 后 tilacode runner 挂起：send_prompt 200 但整轮零事件，干等 300s 超时用户零回复）；事件一旦流动即退出看门狗交 SSEConsumer 既有完成判定，总 300s 超时保留。③**观测补丁**：`_task_event_pump` 断连时输出事件类型直方图 `instance_stream_reconnected` + todo.updated 到达打点 `instance_stream_todo_event`；`_handle_todo_updated` consumer 未匹配 warning `todo_updated_consumer_missed`（原静默 return）。④`event_translator.py` **待办兜底提取**：`tool.called` 且 tool=todowrite 时从 `input.todos` 直接提取完整清单（实测 tilacode 升级后 todo.updated 不再广播到节点实例流、todo_updated_received 恒 0——会话级流仅含 session.next.* 无 todo 事件），复用 `_update_todos`+既有上报链路。测试：`tests/test_node_chat.py` +3（看门狗重发恢复/快速失败/prompt 待办纪律）+ `tests/test_event_translator.py` +2（input.todos 提取/全完成 100） | 节点 ruff 全过 + 275 passed（1 失败为本机内存 92% 触发 resource_busy 环境性）；0.6.6 发布两节点后实测 `tasks.todos` 首次真实写入（4 项清单实时更新） |

## 1. 契约（M1 定稿后不得随意变更）

- 下行 `hub.node_chat`：`{task_id, root_task_id, node_id, text, reply_to, trace_task_id, last_seq}`
- 上行 `agent.node_chat_reply`：`{reply_to, task_id, root_task_id, node_id, text, tokens?}`
- 上行 `agent.node_chat_error`：`{reply_to, task_id, node_id, reason}`
- `POST /v1/tasks/{task_id}/node-chat`：请求体不变 `{node_id, text}`；空闲 → 202 `{reply_to}`；running → 409；任务不存在 → 404；节点离线 → 422
- 消息落库：`task_building_sessions.messages` 追加 `{role: "user"|"assistant", content, mention_node, reply_to?, ts}`（与规划 Chat 消息同构，来源字段区分）

## 2. 回归与红线（每模块完成都要跑）

- 中枢：`python -m ruff check app tests` + `python -m pytest tests/services tests/api -q`
- 节点：`python -m ruff check app tests` + `python -m pytest tests -q`
- 前端：`npx tsc -b` + `npx vitest run`
- 红线：①续聊不影响任务状态机/看门狗/完成判定；②消息落库失败不阻塞对话主链路；③ADR-063 折叠块只读/脱敏红线不破。

## 3. 进度记录

| 日期 | 模块 | 内容 | 验收结果 |
|---|---|---|---|
| 2026-10-05 | — | 立项：ADR-064 四件套（用户三项裁决：完整版过程展示 / 下线建任务路径 / 详设认可）。侦察证据见 ADR-064「问题背景」 | 仅文档，未写代码 |
| 2026-10-05 | M1+M2+M3 | **三端并行实施完成**（三个子代理按 Changes §1 契约独立开发，主会话抽查三个跨端接缝均字段对齐：①下行 `hub.node_chat` payload 7 字段；②上行 `agent.node_chat_reply` 字段与 node_id 回填；③消息落库 snake_case 结构与前端 `messageMentionNode/messageReplyTo` 双形态读取）。M1 两处自主决策：error 落库为 `role:"assistant"` + `[节点续聊失败] {reason}`（复用徽标渲染）；tokens 仅日志不落库。M2 实现取舍：会话校验用 `get_context` 探测而非 create_session（防覆写 agent/model）；轮内挂权限兜底泵、超时尽力 interrupt。M3 自引入 bug 修复：reply_to 去重限定 assistant 角色。中枢全量 78 分钟中 3 个既有失败经 git stash 基线复现验证与本次无关（其中 #2「无规范任务 retry 撞 specification 校验」与 S1 同根源，系 dispatch 硬校验与 retry 路径的既有矛盾，建任务路径下线后建议后续单独评估该测试） | 三端各自验收全绿（中枢 ruff 全过 + 583 passed / 节点 270 passed / 前端 tsc 零错误 + 24/24），明细见 §0 表 |
| 2026-10-06 | M5 | 修复批次（用户实测五问题之③④，登记于本表）：`_build_prompt` 补 todo 纪律（修③子 Agent 待办恒空）+ `_run_node_chat_round` 无进展看门狗（修④ interrupt 后 runner 挂起零事件干等 300s）；同批的①②⑤（前端待办接线遗漏 / 确认后派发语义 / 轨迹空重拉）非 ADR-064 代码，登记 `docs/changelog/2026-10.md` | 节点 ruff 全过 + 273 passed（+3） |
