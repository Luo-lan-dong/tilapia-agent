# 中枢日志系统 v1 Spec（ADR-047）

## 1. 功能描述

为中枢（opc-central-dispatch-hub）补齐三层日志能力，实现「报错可循、执行链路可溯源」：

1. 运行日志分层：文件（现状）+ 内存环形缓冲（查询直读）+ ERROR/WARN 归档（error.log 30 天）；
2. 执行链路事件表 tool_call_logs：技能调用 / LLM 调用 / 压缩 / steering / 派发 / 子 Agent 任务事件；
3. 子 Agent 日志按需拉取：中枢下发 `hub.log_fetch`，Agent 读本地文件分块回传；
4. 查询 API + 前端日志页（两视图）；
5. AI 技能 query_logs：对话中自助查日志。

## 2. 用户故事

- **US-1 排障查报错**：作为运维者，我在日志页选时间区间 + ERROR 级别 + 关键字，
  看到最近报错的结构化字段（含 request_id），并可展开详情。
- **US-2 链路溯源**：我拿到一个 request_id / 会话 ID，切到「执行链路」视图，
  看到该请求的全部事件瀑布（LLM 调用 → 技能调用 → 技能结果 → 压缩 → 派发），
  每条含状态与耗时，一眼定位失败环节。
- **US-3 节点日志追溯**：某任务失败了，我在日志页选择该任务所在节点发起
  「拉取节点日志」，中枢回传该任务时间段的节点本地日志（ERROR 过滤 + tail）。
- **US-4 AI 自助查错**：对话中模型调用失败，用户问「刚才为什么报错」，
  模型自主调用 query_logs 技能（minutes_back=30, level=error）拿到摘要并解释原因。
- **US-5 长期归档**：一个月前的报错，error.log（30 天）与 tool_call_logs（90 天）仍可查。

## 3. 核心流程

### 3.1 运行日志写入与查询

```
logger.info/error(...)
   │
   ├──> ConsoleHandler（控制台，现状）
   ├──> TimedRotatingFileHandler app.log（按天，7/30 天，现状）
   ├──> [新] 分级 Handler：level >= WARNING → error.log（按天，30 天）
   └──> [新] RingBufferHandler：deque(maxlen=5000)（内存，重启清空）

GET /api/v1/logs?start&end&level&keyword&limit
   └──> RingBufferHandler 快照 → 过滤（时间/级别/关键字）→ 倒序返回
```

### 3.2 执行链路采集

```
orchestrator.chat_stream
  ├── LLM 流式调用开始/结束 ──────────> record(kind=llm_call, model, status, duration)
  ├── 预压缩/overflow 压缩 ──────────> record(kind=compaction, ...)
  ├── steering drain 提升 ────────────> record(kind=steering, count)
  └── _run_tool_call ─────────────────> record(kind=skill_call, name, args_summary)
                                       record(kind=skill_result, status, result_summary, duration)

task_event_handler（agent.accepted/started/progress/completed/failed）
  └─────────────────────────────────> record(kind=agent_task_event, name=事件名,
                                            task_id, status, result_summary=reason 等)

task_service.dispatch_task / 调度器派发
  └─────────────────────────────────> record(kind=dispatch, name=agent 名称, task_id)
```

### 3.3 子 Agent 日志按需拉取

```
前端/AI ── POST /api/v1/logs/agent-fetch {client_id, task_id?, tail_lines?, level?}
   │
   ├── 生成 request_id，注册等待 Future
   ├── WS 下发 hub.log_fetch {request_id, level, tail_lines}
   │
   │        Agent: 读本地 logs/app*.log → level 过滤 + tail 截取（≤2000 行）
   │        ├── agent.log_chunk {request_id, seq, total, data_b64, done}  （仿 ADR-028）
   │        └── agent.log_error {request_id, reason}
   │
   ├── Future 完成 → 拼接解码 → 200 {content, lines}
   └── 45s 超时 → 504 {错误}
```

### 3.4 降级链路

- tool_call_logs 写入失败 → 仅 logger.warning，业务零感知；
- 节点离线 / log_fetch 超时 → agent-fetch 返回错误信息，前端提示；
- Agent 为旧版本（无 log_fetch 处理）→ 同上超时路径；
- 环形缓冲为空（刚重启）→ 返回空列表，前端引导切 source=file。

## 4. 验收标准

1. 重启中枢后 `GET /api/v1/logs` 返回内存缓冲中最近的日志（含重启前的请求若未重启）；
2. 制造一次技能失败（如调用未知技能），`GET /api/v1/logs/trace?session_id=...`
   能看到 skill_call（failed）记录；
3. ERROR 日志同时出现在 app.log 与 error.log，error.log 保留策略 30 天；
4. 在线节点上发起 agent-fetch，45s 内返回该节点日志文本；离线节点返回明确错误；
5. 对话中模型可自主调用 query_logs 技能并给出报错摘要；
6. tool_call_logs 中 created_at 超过 90 天的行被清理任务删除；
7. 桌面（frozen）模式日志目录为 data/logs，不再写 /var/log。

## 5. 边界与不做

- 不做日志大小切割（用户明确担心覆盖，保留按天切割）；
- 不做节点日志全量实时上报；
- 不做前端 WebSocket 实时推送（轮询查询已满足）；
- 不做日志检索的全文索引（keyword 为子串匹配，v1 内存 + 表内 ILIKE）。
