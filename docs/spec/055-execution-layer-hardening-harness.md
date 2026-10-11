# 编程执行层缺陷全量修复与进度可视化 Harness（P0–P2）

> 关联：[ADR-055](../adr/055-execution-layer-hardening.md) · [Spec](./055-execution-layer-hardening-spec.md) · [Changes](./055-execution-layer-hardening-changes.md)
> 涉及三套测试工程：节点 `opc-agent-programming/tests`（pytest）、中枢 `opc-central-dispatch-hub/tests`（pytest）、前端 `opc-web-console/src/__tests__`（vitest + RTL）。

## 1. 测试金字塔

```
   E2E（2）        真实节点跑任务：任务摘要面板显示「当前动作」/ 产物拉取不阻塞心跳
    集成（6）      桥接↔中枢事件链路 / 看门狗中断 / 完成判定 / 压缩与重试事件 /
                   并发同项目目录锁 / 凭据移出工作区
   单元（30+）     扫描器 deadline / 路径边界 / 配置原子写 / 存储损坏 /
                   事件翻译新 schema / 阶段映射与 message 脱敏 / 看门狗判定 /
                   子进程自愈 / 中枢字段落库 / 前端三态渲染
```

## 2. 单元测试清单

### 2.1 节点：文件扫描与打包（tests/test_file_scanner_rules.py、tests/test_workspace.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-01 | `rglob` → `scandir` 后 deadline 真能中断 | 构造超大树（mock `scandir` 计数），到点后立即返回且 `truncated=True`，**遍历条目数 < 总数**（原实现会先物化全部） |
| UT-02 | 敏感文件排除 | `.env`、`.env.local`、`.mcp.json`、`.npmrc`、`id_rsa` 不在清单；`.env.example` 在清单 |
| UT-03 | 打包上限 | 累计超 500MB / 单文件超限 → 抛明确错误（不 OOM） |
| UT-04 | 扫描不阻塞事件循环 | 以 `asyncio.to_thread` 包裹后可并发执行心跳协程（断言心跳 tick 计数 > 0） |

### 2.2 节点：工作目录与配置（tests/test_workspace.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-05 | `output_dir` 绝对路径越界 | `/etc`、`C:\Windows` → `WorkspacePathError` |
| UT-06 | `output_dir` 相对路径越界 | `../outside` → `WorkspacePathError`；`sub/dir` → 允许 |
| UT-07 | 兜底路径净化 | `task_id` 含 `..`/分隔符 → 被净化或拒绝 |
| UT-08 | 配置原子写 | 写入过程中断不产生半截 JSON；并发两次 upsert 结果一致（无内容丢失） |
| UT-09 | 模板不污染 | 连续两次 `materialize_rule_docs` 后 `TILACODE_CONFIG_TEMPLATE["instructions"]` 长度不变（`deepcopy`） |
| UT-10 | 凭据不落工作区 | `_resolve_session_model` 后工作目录 `tilacode.jsonc` 不含 `apiKey`；用户级配置目录含对应 provider 且命名空间为 `<provider>_<task短ID>` |

### 2.3 节点：会话登记簿（tests/test_session_store.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-11 | `update_status` 单次加载 | 只调用 `_load` 一次（spy 计数）；并发更新不丢其他记录 |
| UT-12 | 文件损坏不清空 | 预置损坏 JSON → 抛 `SessionStoreError`，原文件被备份（`.bak`），**不**写出空表 |

### 2.4 节点：桥接与看门狗（tests/test_task_bridge.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-13 | `prepare` 抛 `ValueError` → 上报失败 | 中枢收到 `agent.task_failed`，reason 含异常类型（原实现无回执） |
| UT-14 | 未预期异常兜底 | `execute_task` 内抛 `TypeError` → 仍上报 failed；`CancelledError` 不误报 |
| UT-15 | 看门狗触发 | `last_activity_at` 停滞超阈值 → 调 `interrupt` 且 `fail(reason="task_no_progress_timeout:*")` |
| UT-16 | 看门狗不误杀 | 持续有事件（含 `tool.progress`/`retried`/`compaction.delta`）时不触发 |
| UT-17 | 总时长上限（可选） | 配 `task_execution_timeout_seconds>0` 且超时 → failed；配 0 → 不触发 |
| UT-18 | 进度心跳补发 | 静默 > `AGENT_PROGRESS_HEARTBEAT_SECONDS` → 补发 `agent.task_progress` 且 `message` 标明「仍在执行」 |
| UT-19 | payload 字段完整 | 进度事件含 `stage`（枚举内）与 ≤120 字符 `message`；`detail` 含 tokens/cost/files |
| UT-20 | 并发同 project_key | 两个任务同时进入 → 配置写与会话创建被串行化（事件顺序断言，无交错） |
| UT-21 | handoff 包防护 | 含 `/abs`、`..\x`、`C:\x`、反斜杠成员被拒；条目数/单文件超限被拒；不抛 `UnboundLocalError` |
| UT-22 | `finally` 中 `session_id` 未定义 | `_prepare_session` 早失败 → `finally` 正常执行（不抛 `UnboundLocalError`） |

### 2.5 节点：SSE 消费与完成判定（tests/test_task_bridge.py 或新 test_sse_consumer.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-23 | `step.ended` 后 idle 判定 | `is_session_active` 返回 False → 立即 completed；**SSE 订阅次数 == 1**（不拆流重连） |
| UT-24 | 仍活跃不误判 | `is_session_active` 在前 3 次 True、第 4 次 False → completed；期间无新订阅 |
| UT-25 | active 查询异常 | 抛 `TilacodeAPIError` → 保守视为活跃，交由看门狗兜底（不立即 completed） |
| UT-26 | 真实断流重连 | 流异常结束 → 按 `after=durable.seq` 重连，且不重放旧事件 |

### 2.6 节点：事件翻译（tests/test_event_translator.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-27 | 工具名取真实字段 | `tool.called{tool:"bash", input:{command:"npm test"}}` → `tool_name=="bash"`（原实现为 `unknown`） |
| UT-28 | 阶段映射 | `edit/write/apply_patch`→`editing`；`bash`→`running_command`；`grep/glob`→`searching`；`step.started(build)`→`coding`、`(plan)`→`planning` |
| UT-29 | message 生成与脱敏 | 文件类工具 message 含相对路径、`bash` 含 ≤60 字符命令摘要；长度 ≤120；不含绝对路径前缀 |
| UT-30 | `step.ended` 附加信息 | `detail.tokens/cost/files` 与事件一致 |
| UT-31 | 压缩事件 | `compaction.started/ended` → `stage=compacting` / 恢复 + message |
| UT-32 | 重试事件 | `retried{attempt:2}` → message 含「重试」与次数 |
| UT-33 | 未知事件与权限事件 | 返回 None（不产生进度） |

### 2.7 节点：子进程自愈（tests/test_tilacode_manager.py，新增）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-34 | stderr 被消费 | 子进程持续写 stderr 不阻塞（写出超过管道容量后仍能继续） |
| UT-35 | monitor 抗异常 | `on_unexpected_exit` 抛异常 / `CancelledError` 不终止 monitor 循环 |
| UT-36 | 重启退避与上限 | 连续崩溃 → 退避序列 1s/2s/4s…封顶 60s；达上限 → 进入 unavailable |
| UT-37 | 端口/密码动态 | 重启后 `resolved_port`/`server_password` 变化能被客户端取到（非构造期快照） |
| UT-38 | 健康检查落地 | 连续 3 次 `/api/health` 失败 → 触发重启；成功则重置计数 |
| UT-39 | bridge_ready 复位 | 重启失败 → `bridge_ready` 清除，派发被拒绝且心跳 status=unavailable |

### 2.8 中枢（tests/services/test_task_event_handler.py、tests/api/v1/test_task_lifecycle.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-40 | 新字段落库 | payload 带 `stage`/`message` → `task.progress_stage`/`progress_message` 写入 |
| UT-41 | 非法值告警不拦截 | `stage=123`、`message` 超长 → 截断/忽略 + WARNING，进度仍更新 |
| UT-42 | 向后兼容 | 旧 payload（无新键）→ 原有行为不变，新列保持 NULL |
| UT-43 | 日志 stage 真实 | `task_progress_updated` 的 `stage` 等于上报值（原为硬编码 `execution`） |
| UT-44 | Schema 校验 | `AgentTaskProgressPayload` 对缺 `progress` / 越界 progress 拒绝 |
| UT-45 | 迁移可升级可回滚 | `alembic upgrade head` / `downgrade -1` 均成功；两列存在性断言 |
| UT-46 | TaskResponse 暴露 | `GET /tasks/{id}` 返回 `progress_stage`/`progress_message` |

### 2.9 前端（src/__tests__/components/TaskSummary.test.tsx，新增）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-47 | 有 todos + 有当前动作 | 两者都渲染；阶段中文标签正确 |
| UT-48 | 无 todos + 有当前动作 | 显示「当前动作」行，**不再**只显示「暂无待办明细」 |
| UT-49 | 无 todos 无当前动作 | 显示降级空态文案 |
| UT-50 | 未知 stage 容错 | 直接显示原始字符串或「执行中」，不崩溃 |

## 3. 集成测试

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-1 | SSE → 进度 → 中枢事件全链路 | mock tilacode SSE 序列（admitted/step.started/tool.called/tool.progress/step.ended/compaction.*/retried）→ 断言中枢收到的 payload 序列含正确 `stage`/`message` 且进度单调不回退 |
| IT-2 | 看门狗端到端 | 事件流在 `tool.called` 后停住 → 阈值内上报 `agent.task_failed(task_no_progress_timeout)`，槽位释放 |
| IT-3 | 完成判定端到端 | `step.ended` + `is_session_active=False` → 5s 内 `agent.task_completed`；断言 SSE 订阅仅一次 |
| IT-4 | `change_summary` 取数 | mock `/context` 返回消息数组 → `change_summary` 非空；mock `/context` 失败但 `/history` 可用 → 仍能抽取文本 |
| IT-5 | 并发同项目目录 | 同 `project_key` 两任务并发 → 配置与会话无交错；`.tilacode/tilacode.jsonc` 内容完整（非半截） |
| IT-6 | 凭据移出工作区 | 完整跑一次带 `executor_model.api_key` 的 `_prepare_session` → 工作区无 Key、用户级目录有 provider、产物清单不含该文件 |

## 4. E2E

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-1 | 中枢派发一个小型 build 任务到真实节点 | 前端「任务摘要」面板运行期间显示「当前动作」（阶段 + 描述）；完成后 `change_summary` 非空；产物可拉取 |
| E2E-2 | 大工作目录任务（含 `node_modules`） | 执行期间节点心跳不间断（中枢未判定离线）；任务正常完成 |

## 5. CI 门禁

- 节点：`python -m pytest tests/` 全绿；`ruff check app tests` + `ruff format --check` 全过；
- 中枢：`pytest tests/` 全绿（含新迁移的 upgrade/downgrade 用例）；
- 前端：`npm run typecheck`（tsc）+ `vitest run` + `npm run build` 全过；
- **守卫用例（必须存在，防回归）**：
  - UT-01（扫描器 deadline 真中断）——防「10s 超时形同虚设」回归；
  - UT-13（未捕获异常仍回执）——防「任务永久悬挂」回归；
  - UT-15/UT-16（看门狗触发与不误杀）——防卡死占槽与误杀长任务；
  - UT-23（完成判定不拆流）——防 30s 静默撕流回归；
  - UT-27（工具名真实）——防 `unknown` 回归；
  - UT-48（无 todo 也显示当前动作）——防 P1-9 回归；
  - UT-10（凭据不落工作区）——防凭据回潮。
- **迁移门禁**：本决策**包含**一枚迁移（`tasks.progress_stage`/`progress_message`），
  必须同时提供 `upgrade` 与 `downgrade`（backend-component-standards §5）。
