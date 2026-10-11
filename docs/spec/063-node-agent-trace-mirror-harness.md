# 节点 Agent 会话轨迹镜像 Harness（测试清单与门禁）

> 配套 [ADR-063](../adr/063-node-agent-trace-mirror.md) / [Spec](./063-node-agent-trace-mirror-spec.md) / [Changes](./063-node-agent-trace-mirror-changes.md)。
> M1–M4 已实施并回归（2026-10-02），以下为**实际覆盖回填**；E2E 手测项待真实环境执行（Runbook §1–§4）。

## 1. 测试金字塔

```
        ┌──────────────────────────────┐
        │  E2E（Runbook 手测 4 组）      │  真实节点 + 真实任务
        └──────────────┬───────────────┘
        ┌──────────────┴───────────────┐
        │ 集成：节点桥接 2 · 中枢 API 4 │  真实 PG + httpx / pytest
        │       node-chat 6 + steer 6  │
        └──────────────┬───────────────┘
        ┌──────────────┴───────────────┐
        │ 单元：轨迹服务 6 · 采集器/翻译器│  纯函数 / mock
        │ （分段·脱敏·背压·重试）· 前端 8 │
        └──────────────────────────────┘
```

## 2. 单元 / 集成清单（已实施，全绿）

### 中枢（opc-central-dispatch-hub）

- 轨迹服务 6（`tests/services/test_task_agent_trace_service.py`）：`ON CONFLICT (task_id,seq) DO NOTHING` 幂等、8KB 字面截断、读取侧坏行兜底 `{"raw": ...}`、5000 行上限、`after_seq` 分页、`node_id` 过滤；
- 轨迹 API 4（`tests/api`）：POST 批量 `accepted/ignored` 计数、GET `data.rows`/`data.has_more`、`limit` 夹取 [1,1000] 不报 422、坏行不 500；
- node-chat 6 + steer 6（`tests/services/test_task_node_chat.py`、`test_task_steer.py`）：执行中 409 并提示走 steer / 空闲建链上后续任务（同 root/同工作目录/沿用模型·规则·环境、不重跑规划 Agent）+ 定向派发 / 空文本·节点不存在·404；
- 回归命令：`python -m ruff check app tests` + `python -m pytest tests/services tests/api -q`（node-chat/steer 合计 **12 passed**）。

### 节点（opc-agent-programming）

- `AgentTraceCollector`（`app/executor/agent_trace.py`）：seq 统一分配、32 行/1s 攒批、失败整批回队重试、1024 行背压丢老 + status 告知、递归脱敏（Bearer/`sk-`/凭据赋值/敏感键）、UTF-8 字节级头尾截断；
- `EventTranslator.translate_trace`：reasoning 增量 1200 字符分段 + ended 收尾去重、text ended 全文、tool 经 callID 关联工具名 + 结果摘要 + 退出码；
- `sse_consumer.on_trace_rows` 旁路转发（异常只告警）；`task_bridge` 采集器生命周期（execute_task 创建 / finally 排水、心跳周期兜底 flush）；
- 回归命令：`python -m ruff check app tests` + `python -m pytest tests -q`（**248 passed**，含 `tests/test_agent_trace.py`、`tests/test_task_bridge.py`）。

### 前端（opc-web-console）

- `NodeTraceBlock` 5（`src/__tests__/components/NodeTraceBlock.test.tsx`）：默认折叠 / 展开渲染只读 / 展开回调 / 跟随暂停 + 「有新输出」pill / 加载更早；
- `TaskNewChatPanelMention` 3（`src/__tests__/components/TaskNewChatPanelMention.test.tsx`）：@ 按钮、chip、发送分流（running→steer / 空闲→node-chat）；
- 门禁：`npx tsc -b` 零错误 + `npx vitest run`（上述两组用例）。

## 3. 契约要点（防回归）

- `POST /api/v1/tasks/{id}/agent-trace` 返回统一包 `ApiResponse`：`data.accepted` / `data.ignored`；
- `GET` 读 `data.rows` / `data.has_more`，`created_at` 为 ISO 字符串；`limit` 夹取 [1,1000]；
- `payload` 字面截断后可能非合法 JSON，读取侧兜底 `{"raw": ...}`，不得 500；
- `node-chat` 对 running 任务必须 409（前端已分流，直调亦拒绝）。

## 4. E2E（Runbook 可复制执行：[adr063-acceptance-runbook.md](../ops/adr063-acceptance-runbook.md)）

| # | 场景 | 验收 | 状态 |
|---|---|---|---|
| E2E-1 | 轨迹落库与回看 | 真实任务 >30s 后查库三类行；重复 POST 同 seq 返回 ignored | ⬜ 待真实环境 |
| E2E-2 | 折叠块手测 | 默认折叠/展开渲染/实时追加/上滚暂停/加载更早/断线回看 | ⬜ 待真实环境 |
| E2E-3 | `@节点` 双语义 | 执行中 steer（状态保持 running + `task_bridge_steer_sent` 日志）；空闲 node-chat（新任务 parent=原任务、同工作目录） | ⬜ 待真实环境 |
| E2E-4 | 脱敏红线抽查 | `payload like '%sk-%'` 命中行不含明文（已替换 `***`） | ⬜ 待真实环境 |

## 5. CI 门禁

- 中枢：`python -m ruff check app tests` + `python -m pytest tests/services tests/api -q`
- 节点：`python -m ruff check app tests` + `python -m pytest tests -q`
- 前端：`npx tsc -b` + `npx vitest run src/__tests__/components/NodeTraceBlock.test.tsx src/__tests__/components/TaskNewChatPanelMention.test.tsx`
- 红线：①轨迹上报失败不影响任务执行与完成判定；②折叠块只读；③不落凭据/环境变量原文；④不引入 PTY/TUI 终端镜像。
