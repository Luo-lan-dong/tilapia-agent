# 中枢日志系统 v1 Harness（ADR-047）

## 1. 测试金字塔

- **单元测试（pytest，中枢）**：环形缓冲 Handler、tool_call_log_service（record 截断/失败兜底/查询过滤/清理）、
  logs 端点参数校验、query_logs 技能（参数解析/摘要生成/命中计数）。
- **单元测试（pytest，Agent）**：日志文件过滤（level/tail_lines）、分块回传编码。
- **集成测试**：orchestrator 埋点（技能失败后 tool_call_logs 有 skill_call failed 行）、
  task_event_handler 埋点（completed 后有 agent_task_event 行）。
- **E2E（手动/脚本）**：日志页两视图 + 节点日志拉取 + AI 自助查错。

## 2. 单元测试清单（中枢，tests/）

| 用例 | 文件 | 断言要点 |
|------|------|---------|
| UT-1 环形缓冲容量 | tests/core/test_logging_ring_buffer.py | 超 maxlen 丢最旧；级别/关键字过滤正确；快照倒序 |
| UT-2 分级 handler | 同上 | WARNING+ 进 error handler；INFO 不进 |
| UT-3 record 快照与截断 | tests/services/test_tool_call_log_service.py | args>2KB 截断、result>4KB 截断；request_id/session_id 快照 |
| UT-4 record 失败兜底 | 同上 | DB 异常时仅 warning 日志，不抛异常 |
| UT-5 查询过滤 | 同上 | 时间区间/kind/status 过滤；limit 生效；时间升序（trace） |
| UT-6 清理任务 | 同上 | 90 天前删除，90 天内保留 |
| UT-7 logs 端点 | tests/api/test_logs.py | 默认最近 1 小时；level 多值；空结果 200 |
| UT-8 trace 端点 | 同上 | 无 request_id 且无 session_id → 422/400 |
| UT-9 agent-fetch 等待与超时 | 同上 | chunk 拼装正确；Future 超时 504；节点不在线报错 |
| UT-10 query_logs 技能 | tests/skills/test_query_logs.py | minutes_back/level/keyword 参数；返回摘要行+计数；_result 事件协议 |

## 3. 集成测试清单

| 用例 | 断言要点 |
|------|---------|
| IT-1 技能失败落库 | _run_tool_call 失败路径后 tool_call_logs 有 skill_call(status=failed) 与 skill_result |
| IT-2 LLM 调用落库 | chat_stream LLM 轮后 tool_call_logs 有 llm_call 行（含 duration_ms） |
| IT-3 任务事件落库 | _handle_task_failed 后有 agent_task_event 行（status=failed，error 字段=reason） |

## 4. Agent 端测试清单（opc-agent-programming，tests/）

| 用例 | 断言要点 |
|------|---------|
| AT-1 日志过滤读取 | level 过滤准确；tail_lines 截取最后 N 行；时间格式行解析容错 |
| AT-2 分块回传 | base64 编码/分块 seq 从 0 起/done 标记最后一块 |
| AT-3 错误回传 | 文件缺失时发 agent.log_error 且不发 chunk |

## 5. E2E 测试场景

1. **E2E-1 报错可循**：前端制造一次技能失败 → 日志页选 ERROR → 看到该错误 →
   点开展示 request_id → 切执行链路视图按 request_id 查询 → 瀑布含失败技能节点。
2. **E2E-2 节点日志拉取**：日志页选在线节点 → 拉取最近 200 行 → 文本展示；
   离线节点 → 45s 内返回明确错误提示。
3. **E2E-3 AI 自助查错**：对话触发一次技能失败后问「刚才的技能为什么失败」→
   模型调用 query_logs → 回答引用了具体错误摘要。

## 6. CI 门禁

- 中枢：`ruff format && ruff check` + 新增/相关 pytest 全过 + 全量回归通过；
- Agent：新增测试全过；
- 前端：`npm run typecheck` 通过。

## 7. 回归注意

- 并发跑多个 pytest 会互相锁库（远程 PG），测试务必串行执行；
- 日志 Handler 测试需在 configure_logging 后 reset root handlers，防用例间泄漏；
- query_logs 技能读的是进程内环形缓冲，测试需先注入已知条目。
