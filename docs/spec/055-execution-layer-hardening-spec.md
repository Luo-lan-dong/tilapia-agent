# 编程执行层缺陷全量修复与进度可视化 Spec（P0–P2）

> 关联：[ADR-055](../adr/055-execution-layer-hardening.md) · [Harness](./055-execution-layer-hardening-harness.md) · [Changes](./055-execution-layer-hardening-changes.md)
> 上游：[ADR-012](../adr/012-tilacode-execution-layer-integration.md)（执行层集成）· [ADR-028](../adr/028-task-workspace-binding-and-artifact-sync.md)（产物）· [ADR-032](../adr/032-concurrent-task-scheduling.md)（并发）

## 1. 功能描述

修复 `opc-agent-programming` 执行层 13 项缺陷（P0 5 项 / P1 4 项 / P2 4 项，其中 P2-10
按用户决策不做），覆盖三层：**节点执行层**（Python 桥接层）、**中枢**（落库与 WS 校验）、
**前端**（任务摘要展示）。核心目标四个：

1. **任务不丢**：任何异常路径都必须给中枢一个终态回执；卡死任务必须被识别并释放槽位；
2. **节点不掉线**：扫描/打包等重 IO 不得阻塞事件循环（心跳优先级最高）；
3. **进度看得见**：任务摘要面板必须实时显示「Agent 当前在做什么」，不再只显示
   「暂无待办明细」；
4. **安全边界**：工作目录不可越界写入，API Key 不落任务工作区，`.env*` 不进产物。

## 2. 用户故事

- **US-1（任务不悬挂）**：作为用户，任何失败（含桥接层未捕获异常、工作目录非法、
  卡死无进度）我都能在中枢看到 `agent.task_failed`，而不是任务永远停在「执行中」。
- **US-2（节点不假死）**：作为运维，超大工作目录的扫描/打包不能导致节点被判定掉线。
- **US-3（卡死自愈）**：作为运维，卡死的任务在阈值内被判失败并释放并发槽位，
  后续任务可以继续派发到该节点。
- **US-4（摘要可见）**：作为用户，我在「任务摘要」面板能看到 Agent **当前动作**
  （阶段 + 具体操作，如「正在编辑 src/main.py」），即使 Agent 没有产出待办列表。
- **US-5（长任务不假死）**：作为用户，Agent 长时间思考或执行命令期间，面板仍有心跳式的
  「仍在执行」提示，不会看起来像卡住。
- **US-6（凭据不泄露）**：作为安全负责人，执行模型的 API Key 不出现在任务工作目录、
  产物清单或回传的 zip 中。
- **US-7（不回归）**：作为用户，旧中枢/旧节点混跑时现有行为不变；任务链续接、
  产物拉取、现场迁移、规则文档加载等既有能力不受影响。

## 3. 核心流程

### 3.1 任务执行与看门狗（P0-2 / P0-3 / P2-13）

```
hub.task_dispatch
   ↓ 校验 + 下载资产（既有）
TaskBridge.execute_task                       ← 任何异常兜底上报 failed
   ↓ 取 project_key 锁（D4 串行化）
_prepare_session（工作目录边界校验 + 原子写配置 + 用户级凭据）
   ↓ 起三个后台任务：
   ├── _file_tree_reporter   （经 to_thread 扫描，不阻塞事件循环）
   ├── _task_event_pump      （实例级事件：todo.updated / 权限兜底）
   └── _task_watchdog        ← 新增：无进度超时检测
   ↓
SSEConsumer.run（记录 last_activity_at，事件驱动）
   ↓ 事件正常 → 重置看门狗；进度上报（stage/message/tool/detail）
   ↓
完成判定：step.ended → 1s 间隔轮询 /api/session/active（上限 5s）→ idle 即完成
   ↓
结果分发：plan→plans/*.md；build→追加总结 prompt（/context 取文本）
   ↓ agent.task_completed（或 failed / task_no_progress_timeout）
```

看门狗判定（D1）：

```
每 10s 检查一次：
  if now - consumer.last_activity_at > task_no_progress_timeout_seconds:
      logger.warning("task_timeout", timeout_seconds=阈值)      # logging-standards §5.1
      POST /api/session/{id}/interrupt                          # 尽力中断
      consumer.fail("task_no_progress_timeout:<秒>")
  if task_execution_timeout_seconds > 0 and now - started_at > 该值:
      同上，reason="task_execution_timeout:<秒>"
```

### 3.2 完成判定（P1-6，D5）

```
收到 step.ended：
  _idle_armed = True
  idle_expired = await poll_active_until_idle(session_id, interval=1s, limit=5s)
      ├── is_session_active() == False → 判定 completed
      └── 5s 内仍 active（新 step 已开始）→ _idle_armed = False 继续正常消费
  期间不取消 anext()、不断开 SSE；仅在真实断流时按 after=durable.seq 重连
```

### 3.3 进度生成与上报（P1-8 / P1-9，D2）

```
tilacode SSE 事件                EventTranslator              中枢 WS payload
─────────────────────────────────────────────────────────────────────────────
prompt.admitted                → stage=preparing            → 心跳 progress=10
step.started                   → stage=coding/planning      → progress≥20
tool.called(tool=edit,          → stage=editing              → message="正在编辑 a/b.py"
           input.filePath)        message/tool               → tool="edit"
tool.called(tool=bash, cmd)    → stage=running_command      → message="正在执行 npm test"
tool.called(tool=grep|glob)    → stage=searching            → message="正在搜索 TODO"
tool.progress                  → stage 不变 + detail        → 心跳（P2-13）
text.ended                     → message=文本摘要(≤120)
step.ended(tokens/cost/files)  → detail={files,tokens,cost}  → progress +2（封顶 90）
compaction.started             → stage=compacting           → message="正在压缩上下文"
compaction.ended               → stage 恢复 + 心跳
retried                        → message="模型请求重试第 N 次"
todo.updated（实例级）          → progress=max(事件, todo 比例) + todos
完全静默 >20s（看门狗未触发）    → 桥接层补发心跳 progress 不变 + message="仍在执行：<最近动作>"
```

stage → 中文标签（前端映射）：

```
preparing=准备中   planning=规划中   coding=编写中   editing=修改文件
running_command=执行命令   searching=检索代码   testing=验证中
compacting=压缩上下文   summarizing=生成说明   idle=空闲
```

### 3.4 安全边界（P0-4 / P2-12）

```
prepare(project_key, output_dir):
  candidate = (workspace_root / output_dir) if 相对 else Path(output_dir)
  resolved = candidate.resolve()
  if not resolved.is_relative_to(Path(workspace_root).resolve()):   ← 新增硬约束
      raise WorkspacePathError(...)                                  ← 纳入 execute_task 捕获
  兜底路径 workspace_root/<task_id> 同样净化（拒绝 .. 与分隔符）

凭据（D3）：
  ~/.config/tilacode/tilacode.jsonc  ← provider.<name>_<task短ID>（含 apiKey，原子写）
  工作目录/.tilacode/tilacode.jsonc  ← permission/default_agent/instructions（无凭据）

产物（P2-12④）：EXCLUDED_NAMES 增 .env / .env.*（保留 .env.example）/ .mcp.json / .npmrc / id_rsa*
```

## 4. 降级链路

| 异常场景 | 行为 |
|---------|------|
| 工作目录越界（`output_dir` 指向工作区外） | 抛 `WorkspacePathError` → `execute_task` 捕获 → 上报 `agent.task_failed`（不再无回执） |
| 桥接层未预期异常（`ValueError`/`TypeError`/`KeyError`） | `hub_client._run_task` 兜底 catch → 上报 `agent.task_failed`（reason 含异常类型） |
| 扫描/打包超时或超大 | 标记 `truncated` / 抛明确上限错误；均不阻塞事件循环（`to_thread`） |
| 无进度超过看门狗阈值 | 中断 + 上报 `failed(task_no_progress_timeout)`，并发槽位释放 |
| 无进度超过总时长上限（默认关闭） | 同上，reason=`task_execution_timeout` |
| `/api/session/active` 查询失败 | 保守视为「仍活跃」继续等待（沿用既有 `_check_active_safe` 语义），由看门狗兜底 |
| `/api/session/{id}/context` 取修改说明失败 | 回退 `/history` 事件分页抽取；仍失败 → `change_summary=None`（不阻断完成上报） |
| 压缩进行中（长时间无业务进度） | `compaction.started` 即重置看门狗并上报 `stage=compacting`，不会误判卡死 |
| 用户级配置目录不可写（权限问题） | 记 ERROR 日志 + 回退到「任务目录内配置」并在日志与完成事件中标注降级（不静默降级安全性） |
| tilacode 连续重启失败 | 进入 `unavailable`：`bridge_ready` 复位、心跳标 unavailable、拒绝新任务（ADR-012 降级链） |
| 中枢为旧版本（忽略新 payload 键） | 新增键被忽略，`progress`/`todos` 语义不变（向后兼容） |
| 前端为旧版本（不认识新字段） | 类型可选字段，渲染逻辑不变 |

## 5. 数据结构

### 5.1 中枢 `tasks` 表（新增两列）

```python
progress_stage: Mapped[str | None] = mapped_column(
    String(32), nullable=True, comment="Agent 实时上报的执行阶段（ADR-055）：preparing/.../idle"
)
progress_message: Mapped[str | None] = mapped_column(
    String(500), nullable=True, comment="Agent 实时上报的当前动作描述（ADR-055），≤120 字符"
)
```

### 5.2 `agent.task_progress` payload（扩展）

见 ADR-055 D2。新增键：`stage`（必带）、`message`、`tool`、`detail`（均可空）。

### 5.3 节点配置项

| 配置项 | 默认 | 约束 | 说明 |
|-------|------|------|------|
| `TASK_NO_PROGRESS_TIMEOUT_SECONDS` | 1800 | `ge=60` | 无进度看门狗阈值（秒） |
| `TASK_EXECUTION_TIMEOUT_SECONDS` | 0 | `ge=0` | 任务总时长上限（0=不限）；语义由「从未生效」改为可选上限 |
| `TILACODE_CONFIG_DIR` | 空 | — | 用户级 tilacode 配置目录覆盖（缺省 `~/.config/tilacode`） |
| `AGENT_PROGRESS_HEARTBEAT_SECONDS` | 20 | `ge=5` | 静默期进度心跳间隔（P2-13） |

## 6. 验收标准

- [ ] **P0-1**：构造含 5 万文件的目录，扫描与打包期间心跳仍按 `heartbeat_interval_seconds` 正常发送（集成用例断言心跳间隔未被拉长）；
- [ ] **P0-2**：无进度超过阈值 → 上报 `failed(task_no_progress_timeout)`，且 `_execution_tasks` 槽位被释放（后续派发可被接受）；
- [ ] **P0-3**：让 `prepare` 抛 `ValueError` → 中枢收到 `agent.task_failed`（回归用例，原实现会悬挂）；
- [ ] **P0-4**：`output_dir=/etc` 与 `../../x` 均被拒绝并上报失败；执行自定义模型任务后，工作目录内不存在 `apiKey`；用户级配置目录存在对应 provider；
- [ ] **P0-5**：build 模式任务完成后 `change_summary` 非空（mock tilacode 返回 `/context` 消息数组）；
- [ ] **P1-6**：`step.ended` 后 5s 内完成判定，且该窗口内不产生 SSE 重连（断言 stream 订阅次数为 1）；
- [ ] **P1-7**：压缩事件驱动 `stage=compacting` 上报；任务链第二轮 prompt 不再重复内联全量文档；
- [ ] **P1-8**：`tool.called` 翻译出的 `tool_name` 等于真实工具名（不再是 `unknown`）；`step.ended` 的 tokens/cost/files 出现在 `detail`；
- [ ] **P1-9**：前端任务摘要面板在 Agent 运行期间显示「当前动作」（阶段标签 + message）；中枢 `tasks.progress_stage`/`progress_message` 有值；无 todo 时不再只显示「暂无待办明细」；
- [ ] **P2-11**：kill tilacode 后节点自动重启并以退避策略重试；连续失败进入 unavailable 且心跳同步；`bridge_ready` 复位；stderr 有输出时不再阻塞子进程；
- [ ] **P2-12**：`.env` 不出现在产物清单与 zip；同 project_key 并发任务不产生配置写竞态（锁用例）；`session_store` 文件损坏时不清空既有记录；
- [ ] **P2-13**：模拟 60s 无事件，节点补发心跳进度且前端面板仍显示「仍在执行」；
- [ ] **兼容性**：旧格式 payload（无 stage/message）仍被中枢接受；迁移 `upgrade`/`downgrade` 均可执行；
- [ ] **门禁**：节点/中枢 `pytest` 全绿、`ruff check/format` 全过、前端 `tsc`+`vitest`+`build` 全过。

## 7. 关联文档

- [ADR-055](../adr/055-execution-layer-hardening.md)
- [055-execution-layer-hardening-harness.md](./055-execution-layer-hardening-harness.md)
- [055-execution-layer-hardening-changes.md](./055-execution-layer-hardening-changes.md)
- [012-tilacode-execution-layer-integration-spec.md](./012-tilacode-execution-layer-integration-spec.md)
