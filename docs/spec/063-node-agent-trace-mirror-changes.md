# 实施范围：节点 Agent 会话轨迹镜像（ADR-063）

> 本文是 ADR-063 的**实施范围与逐模块登记表**。规则：**先在本表登记实施范围，再动代码**；
> 每完成一个模块，回填「状态 / 验收结果 / 涉及文件」，并同步 `docs/INDEX.md` 更新记录。

## 0. 本次范围（M1–M5）

| 模块 | 状态 | 涉及文件（预估） | 验收命令 / 观察点 |
|---|---|---|---|
| M1 通道与契约 | ✅ 完成（2026-10-01） | 中枢：`alembic/versions/20261001*_agent_trace.py`、`app/models/task_agent_trace.py`、`app/services/task_agent_trace_service.py`、`app/api/v1/endpoints/tasks.py`（ingress/查询）、**任务 SSE 事件类型 `node_trace`**；节点：`app/executor/tilacode_client.py`（上报方法）、`app/executor/task_bridge.py`（仅 status 行打通） | 迁移 head 生效；派发一个任务后 `select count(*) from task_agent_trace;` > 0；`GET /v1/tasks/{id}/agent-trace` 按 seq 取回；重复 POST 不重复落库 |
| M2 节点采集 | ✅ 完成（2026-10-02） | 节点：`app/executor/event_translator.py`（`translate_trace` 分类）、新 `app/executor/agent_trace.py`（`AgentTraceCollector`：seq/批量/重试/背压/截断/脱敏）、`app/executor/sse_consumer.py`（`on_trace_rows` 转发）、`app/executor/task_bridge.py`（采集器生命周期 + seq 统一 + 心跳兜底 flush）、新 `tests/test_agent_trace.py`、`tests/test_task_bridge.py`（M2 集成用例） | 节点 `pytest tests -q` **248 passed**（含脱敏/背压/重试/分段用例）+ `ruff` All checks passed；上报失败不外抛用例通过 |
| M3 前端折叠块 | ✅ 完成（2026-10-02） | 前端：新 `src/components/NodeTraceBlock.tsx` + `NodeTraceBlock.css`、`src/hooks/useTaskAgentTrace.ts`、`src/components/TaskNewChatPanel.tsx`（`nodeTraceGroups` 插槽 + `NodeTraceGroup` 类型）、`src/pages/TaskNew.tsx`（hook 接入 + node_id 分组 + 展开才轮询）、`src/api/tasks.ts`（`fetchTaskAgentTrace`）、`src/types/task.ts`（`AgentTraceRow`/`AgentTraceListResponse`）、新 `src/__tests__/components/NodeTraceBlock.test.tsx` | `npx vitest run NodeTraceBlock` **5 passed**（默认折叠/展开渲染只读/展开回调/跟随暂停+新输出/加载更早）；`npx tsc -b` 零错误；全量 vitest 中 4 个失败文件（TaskNewInputBar/TaskDetailDrawer/useTaskPlanActions/TaskNew 旧断言）均为本次未触碰的既有过期用例 |
| M4 `@节点` | ✅ 完成（2026-10-02） | 中枢：`app/services/task_service.py`（新 `start_node_chat`：执行中拒绝/空闲建链上后续任务并定向派发）、`app/api/v1/endpoints/tasks.py`（`POST /{task_id}/node-chat`）、`app/schemas/task.py`（`TaskNodeChatRequest`）、新 `tests/services/test_task_node_chat.py`；前端：`src/api/tasks.ts`（`nodeChatTask`）、`src/components/TaskNewChatPanel.tsx`（@ 按钮 + chip + `mentionNode` 消息来源标注）、`src/pages/TaskNew.tsx`（候选来自任务已派发节点；发送分流：running→`steerTask` / 空闲→`nodeChatTask`）、`src/hooks/useTaskBuildingChat.ts`（`appendUserMessage`）、`src/types/taskBuilding.ts`（`ChatMessage.mentionNode`）、`src/pages/TaskNew.css`、新 `src/__tests__/components/TaskNewChatPanelMention.test.tsx` | 中枢 `pytest test_task_node_chat + test_task_steer` **12 passed**（执行中拒绝并提示走 steer / 空闲建链同根同目录定向派发 / 空文本·节点不存在·404）；前端 mention 用例 **3 passed** + `tsc -b` 零错误；E2E「@ 后节点侧收到」待真实环境手测（M5 Runbook） |
| M5 收尾 | ✅ 完成（2026-10-02） | `docs/ops/adr063-acceptance-runbook.md`（新验收操作单：轨迹落库/折叠块手测/@节点双语义/脱敏红线/回归命令）、`docs/adr/063-node-agent-trace-mirror.md`（状态补实施进度）、`docs/INDEX.md`（ADR 状态 + Ops 表 + 更新记录）、本表 | 三端回归：节点 248 passed + ruff 全过；中枢 node-chat/steer 12 passed + ruff 全过；前端 NodeTraceBlock 5 + Mention 3 passed + tsc 零错误；Runbook 可复制执行 |

## 1. 数据与接口契约（M1 定稿后不得随意变更）

### 1.1 表 `task_agent_trace`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | bigint PK | |
| `task_id` | **UUID** FK tasks.id（`tasks.id` 是 `UUID(as_uuid=True)`，ON DELETE CASCADE） | 级联删除 |
| `node_id` | str \| null | 执行节点 |
| `session_id` | str \| null | 节点侧会话 id |
| `seq` | int | **任务内单调**，幂等键 |
| `kind` | str | `status` / `reasoning` / `text` / `tool` / `todo` / `usage` |
| `payload` | text | 视图行内容。**字面**存「前 6144 B + `{"truncated":true}` + 后 1536 B」，总长 ≤ 8192；对象被截断时该文本可能不是合法 JSON，**读取侧兜底**为 `{"raw": ...}`（读接口不会因坏行 500） |
| `created_at` | timestamptz | |

唯一约束 `(task_id, seq)`；索引 `(task_id, seq)`；单任务行数上限 5000（超出归档策略见 ADR D3 备注）。

### 1.2 接口

- `POST /api/v1/tasks/{task_id}/agent-trace`：`{"node_id": ..., "session_id": ..., "rows": [{"seq": n, "kind": ..., "payload": {...}}]}`；重复 `(task_id, seq)` 忽略；返回**统一包一层 `ApiResponse`**：`{"code":0,"message":"ok","data":{"accepted":n,"ignored":m}}`（`accepted/ignored` 在 `data` 下，不是顶层）。
- `GET /api/v1/tasks/{task_id}/agent-trace?node_id=&after_seq=&limit=`：同样包 `ApiResponse`，**前端读 `data.rows` / `data.has_more`**；每行含 `seq/kind/payload/created_at`（`created_at` 为 **ISO 字符串**）+ 冗余的 `id/task_id/node_id/session_id`（schema 容忍）；`limit` 默认 200，端点内夹取到 **[1,1000]（越界不报 422）**；坏行 `payload` 由读取侧兜底为 `{"raw": ...}`。
- 任务 SSE：新增事件 `{"type": "node_trace", "data": {"node_id": ..., "seq": n, "kind": ..., "payload": {...}}}`。
- `POST /v1/tasks/{task_id}/node-chat`：`{"node_id": ..., "text": ...}`（空闲节点开新一轮）。

## 2. 回归与红线检查（每模块完成都要跑）

- 节点：`python -m ruff check app tests` + `python -m pytest tests -q`
- 中枢：`python -m ruff check app tests` + 相关集合 `python -m pytest tests/services tests/api -q`
- 前端：`npx vitest run`（相关用例）+ `npx tsc -b`（与既有基线对比）
- 红线：① 轨迹上报失败不影响任务执行与完成判定；② 折叠块**只读**；③ 不落凭据/环境变量原文；④ 不引入 PTY/TUI 终端镜像。

## 3. 进度记录

| 日期 | 模块 | 内容 | 验收结果 |
|---|---|---|---|
| 2026-10-01 | M1 通道与契约 | **完成**。中枢：迁移 `20261001_agent_trace`（单头，接 `20260928i_experience_env`）+ 模型 `TaskAgentTrace` + 服务 `TaskAgentTraceService`（`append_rows`/`list_rows`/`count_rows`，`ON CONFLICT (task_id,seq) DO NOTHING` 幂等、8KB 字面截断、5000 行上限、`after_seq` 分页 + `node_id` 过滤）+ 端点 `POST/GET /api/v1/tasks/{id}/agent-trace` + schema + 测试（服务 6 + API 4）。节点：`hub_client.report_agent_trace`（失败只 warning）+ `task_bridge` 启动/结束两条 `status` 行（seq 按任务从 1 递增，finally 清理）+ 2 用例。**SSE `node_trace` 按 ADR-063 侦察结论降级为可选**（改为前端按 `after_seq` 增量拉取） | 中枢 `alembic current = 20261001_agent_trace (head)`；`pytest services+api` **10 passed**；`ruff` All checks passed；节点 `pytest -q` **229 passed** + `ruff` 全过 |
| 2026-10-01 | — | 立项：ADR-063 + 本实施范围（用户已确认方案②/只读/落库 + `@节点` 两种语义 + 多节点群聊式） | 仅文档，未写代码 |
| 2026-10-02 | M2 节点采集 | **完成**。节点新增 `app/executor/agent_trace.py`（`AgentTraceCollector`：seq 统一分配、32 行/1s 攒批、失败整批回队重试、1024 行背压丢老 + 补 status 告知行、递归脱敏（Bearer/sk-/凭据赋值/敏感键）、UTF-8 字节级头尾截断）；`event_translator.translate_trace`（reasoning 增量 1200 字符分段 + ended 收尾去重、text ended 全文、tool 生命周期经 callID 关联工具名 + 结果摘要 + 退出码）；`sse_consumer` 新增 `on_trace_rows` 旁路转发（异常只告警）；`task_bridge` 采集器生命周期（execute_task 创建/finally close 排水、心跳周期兜底 flush、`_report_trace_status` 委托采集器）。顺带修复既有 F821：`task_bridge` 缺失 `verify_tamper` import | 节点 `pytest tests -q` **248 passed** + `ruff` 全过（含分段/脱敏/背压/重试/桥接集成用例） |
| 2026-10-02 | M3 前端折叠块 | **完成**。前端新增 `NodeTraceBlock`（默认折叠；块头 = 节点徽标·节点名·任务名·最新动作·进度·行数；展开按 seq 渲染 status/reasoning/text/tool 行；只读无输入；跟随最新 + 上滚暂停 + 「有新输出」pill + 「加载更早」客户端窗口）与 `useTaskAgentTrace`（回看翻页 + 展开时 1s 轮询、收起即停、失败自愈）；`TaskNewChatPanel` 新增 `nodeTraceGroups` 插槽（群聊式并列）；`TaskNew` 按 `node_id` 分组 + 展开才轮询；`api/tasks.fetchTaskAgentTrace` + 类型定义 | `vitest NodeTraceBlock` **5 passed**；`tsc -b` 零错误（全量 vitest 中 4 个失败文件为既有过期用例，与本次无关） |
| 2026-10-02 | M4 `@节点` | **完成**。中枢 `TaskService.start_node_chat`（执行中 assigned/running → 拒绝并提示走 steer；空闲 → 在原任务链上建后续任务（同 root/同工作目录/沿用模型·规则·环境，不重跑规划 Agent）+ `dispatch_task(node_id=…)` 定向派发）+ 端点 `POST /v1/tasks/{id}/node-chat`（409/404 映射）+ `TaskNodeChatRequest`；前端 @ 按钮（候选 = 任务 assigned/target 节点）+ 输入区 chip + 消息 `@节点名` 来源标注 + 发送分流（running→`steerTask` / 空闲→`nodeChatTask`）+ `appendUserMessage` 本地回显 | 中枢 **12 passed**（node-chat 6 + steer 6）+ `ruff` 全过；前端 mention 用例 **3 passed** + `tsc` 零错误；E2E 待真实环境手测 |
| 2026-10-05 | 文档补全 | **四件套补齐**（决策文档带号体系改造，见 `docs/changelog/2026-10.md`）：补建 Spec [063-node-agent-trace-mirror-spec.md](./063-node-agent-trace-mirror-spec.md)（功能描述/用户故事/核心流程/降级链路/验收标准）与 Harness [063-node-agent-trace-mirror-harness.md](./063-node-agent-trace-mirror-harness.md)（M1–M4 实测覆盖回填 + E2E 待办清单）；本文件更名加序号前缀；ADR/INDEX 状态统一为「M1–M5 已完成，遗留真实环境 E2E 手测」 | `python scripts/check_docs.py` 零错误 |
