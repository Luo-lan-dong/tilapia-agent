# ADR-055: 编程执行层缺陷全量修复与进度可视化（P0–P2）

## 状态

Accepted

## 日期

2026-09-27

## 问题背景

对 `opc-agent-programming` 执行层（Python 桥接层 + vendored `tilacode` 执行层）做了一次
完整代码走查，共定位 13 项缺陷，按危害分为 P0/P1/P2。所有结论均有文件行级依据，
见各条目「代码依据」列。

### P0（正确性 / 稳定性，会导致任务丢失或节点失去服务能力）

| 编号 | 缺陷 | 代码依据 |
|------|------|---------|
| P0-1 | 工作目录扫描与产物打包是**同步阻塞 IO 且跑在事件循环上**：`sorted(root.rglob("*"))` 在 deadline 检查之前就把整棵树物化排序（10 秒「硬超时」形同虚设）；`_pack_workspace` 同步 zip 进内存（最坏 ≈2×500MB）。周期上报（30s）或产物拉取时事件循环被占住 → **心跳发不出 → 中枢判定掉线 → 任务被改派** | `app/executor/file_scanner.py:67`、`app/executor/task_bridge.py:525-541` |
| P0-2 | **无任务级超时**：`task_execution_timeout_seconds`（默认 300）全仓无任何引用（死配置）；SSEConsumer 只在 `step.ended` 之后计时，模型不结束 step 时永不触发。任务卡死 → `_execution_tasks` 槽位不释放 → 节点此后**拒绝所有任务**（`agent_max_concurrent_tasks=2`），直到进程重启 | `app/config.py:84-88`（无引用）、`app/client/hub_client.py:699`、`app/executor/sse_consumer.py:180-186` |
| P0-3 | **异常可能不产生 failed 回执**：`execute_task` 只捕 `(TilacodeAPIError, TaskBridgeError, SessionStoreError, OSError)`，`prepare` 抛出的 `ValueError`、`TypeError`、`KeyError` 会穿过去；`hub_client._run_task` 只有 `finally` 没有 `except` → **中枢永久等待一个不会到来的终态** | `app/executor/task_bridge.py:191`、`app/client/hub_client.py:898-906`、`app/executor/workspace.py:153-154` |
| P0-4 | **`output_dir` 路径越界 + API Key 明文落工作区**：`prepare` 唯一校验是「不等盘根」，注释自认「绝对路径本就在盘内」；实际可写 `/etc`、`~/.ssh`，相对 `../..` 亦可逃逸。同时 `executor_model.api_key` 明文写入工作区 `.tilacode/tilacode.jsonc` | `app/executor/workspace.py:146-156`、`app/executor/workspace.py:216-221`、`app/executor/task_bridge.py:384-390` |
| P0-5 | **`change_summary` 恒为空（协议不符）**：`_extract_last_assistant_text` 期望 `/api/session/{id}/history` 返回「消息数组（`role`/`parts`）」，该端点实际返回的是 **durable 事件分页** `{data: SessionEvent.Durable[], hasMore}`（`packages/protocol/src/groups/session.ts:307-325`），取不到文本即返回 `None` → ADR-012 的「本轮修改说明」回传事实上失效 | `app/executor/task_bridge.py:629-658` |

### P1（效率与可观测性）

| 编号 | 缺陷 | 代码依据 |
|------|------|---------|
| P1-6 | **完成判定用错原语**：静默窗口以 `asyncio.wait_for(anext(...))` 超时实现，会**撕掉整条 SSE 连接**再按 `after=seq` 重连；30 秒窗口还串行加在每个任务的关键路径上。（`POST /api/session/{id}/wait` 在 tilacode 侧未实现，返回 `OperationUnavailableError`，不能依赖） | `app/executor/sse_consumer.py:176-192`、`tilacode/packages/core/src/session.ts:421-424` |
| P1-7 | **任务链上下文单调膨胀**：续接复用同一 Session（设计如此），但每轮重复写入三件套/AGENTS.md/规则文档并在 prompt 中重复提示；`delivery:"steer"` 会把新一轮输入插进当前 drain 并把 step 计数重置为 1（`runner/llm.ts:187-196`）；长链必然触发压缩（用户侧表现为长时间无进度，而压缩事件未被翻译上报） | `app/executor/task_bridge.py:256-309`、`app/executor/tilacode_client.py:276` |
| P1-8 | **事件翻译器与真实 schema 有偏差，白丢大量信息**：①工具名恒为 `unknown` —— 真实 `session.next.tool.called` 的字段是 `tool: string` + `input: {}`（`schema/src/session-event.ts:312-325`），而 `_tool_name` 找的是 `data.name` / `data.tool.title`；②未处理 `session.next.compaction.*`、`session.next.retried`、`tool.progress`；③`step.ended` 里现成的 `tokens{input,output,reasoning,cache{read,write}}`、`cost`、`files[]` 全被丢弃 | `app/executor/event_translator.py:197-205`、`tilacode/packages/schema/src/session-event.ts:162-183` |
| P1-9 | **上报信息量过于贫瘠 → 任务摘要面板永远「暂无待办明细」**：线上 payload 只有 `{task_id, progress, todos}`，`EventTranslator` 精心构造的 `message`/`tool_name`/`stage` 在最后一步被丢弃；中枢侧 `TaskEventPayload` 也只有 `task_id`/`progress`。模型若不调用 `todowrite`（V2 core 虽有该工具，但无任何机制保证调用），面板就没有任何「进行到哪一步」的信息 | `app/client/hub_client.py:1030-1040`、`app/executor/task_bridge.py:420-424`、`opc-central-dispatch-hub/app/schemas/ws_messages.py:52-61`、`opc-web-console/src/components/TaskSummary.tsx:100-119` |

### P2（工程化与能力）

| 编号 | 缺陷 | 代码依据 |
|------|------|---------|
| P2-10 | 节点侧无 MCP 能力 | **本次不做**（用户决策 2026-09-27：节点侧暂不需要）；子代理能力单列待确认 |
| P2-11 | **子进程自愈不完整**：①`stderr=asyncio.subprocess.PIPE` 但**从不读取**，写满会阻塞子进程；②`monitor` 只捕 `(OSError, RuntimeError)`，`CancelledError` 击穿后自愈能力永久丢失；③重启无退避、无次数上限；④客户端 `base_url` 是构造期快照，端口漂移后不刷新（`resolved_port` 仅在构造时取一次）；⑤`bridge_ready` 置位后不复位，重启期间中枢仍认为可派发；⑥`tilacode_health_check_interval` 是死配置，无周期性健康检查 | `app/executor/tilacode_manager.py:91-94,206-208`、`app/main.py:52-55,154`、`app/config.py:124-128` |
| P2-12 | **并发与数据完整性**：①同一 `project_key` 并发任务共享目录与 Session，`AGENTS.md`/`tilacode.jsonc` 读-改-写无锁（`ensure_tilacode_config` 为 check-then-write）；②`TILACODE_CONFIG_TEMPLATE` 被 `_upsert_instructions` 浅拷贝后改写 → 污染同进程全局常量；③`session_store.update_status` 双重 `_load`，两次之间损坏即把单条记录写入空表**覆盖全量**；④`.env*` 等凭据文件不在产物排除清单，会被打包回传；⑤`_pack_workspace` 读全部文件进内存无上限保护；⑥`_extract_handoff_bundle` 未拦 Windows 盘符/反斜杠成员，且在 `with` 块外引用 `namelist()` | `app/executor/workspace.py:169-184,340-364`、`app/executor/session_store.py:137-150`、`app/executor/file_scanner.py:14-29`、`app/executor/task_bridge.py:311-335` |
| P2-13 | **长任务前端「假死」**：`>30s` 的单个 step（长思考、长 bash、正在压缩）期间没有任何事件上报，前端停在旧进度 | `app/executor/sse_consumer.py:180`、`opc-web-console/src/hooks/useTaskPlanTracking.ts:6` |

## 决策

与用户确认（2026-09-27）：**全链路修复**（节点 + 中枢 + 前端）、**超时采用「无进度看门狗」语义**、
**文档先行**（本 ADR + Spec + Harness + Changes 四件套）。

### D1 · 超时语义：无进度看门狗（替代「任务总时长」）

- 新增 `TASK_NO_PROGRESS_TIMEOUT_SECONDS`（默认 **1800**，`ge=60`）：**任何** SSE 事件
  （含 `tool.called`/`tool.progress`/`reasoning.*`/`text.*`/`step.*`）或进度上报即重置计时；
  仅当「无任何活动」超过阈值才判定卡死。
- 卡死处理：记 `task_timeout` WARNING 日志（logging-standards §5.1 既有事件名）→
  调 `POST /api/session/{id}/interrupt` → 按 `failed(reason="task_no_progress_timeout:<秒>")` 上报。
- 新增可选 `TaskDispatchPayload.timeout_seconds`（中枢可覆盖），对齐 `agent-node-standards.md`
  §5.3 的 `timeout_seconds` 约定。
- `TASK_EXECUTION_TIMEOUT_SECONDS` 语义改为**任务总时长上限**，默认 **0 = 不限**：
  该配置项历史上从未生效，改为「限」会误杀真实开发任务（规范默认 300s 按「任务」计，
  而一次真实编程任务常远超 5 分钟），因此默认不启用、由运维显式开启。

### D2 · 进度上报协议扩展（向后兼容）

`agent.task_progress` payload 从 `{task_id, progress, todos}` 扩展为：

```jsonc
{
  "task_id": "uuid",
  "progress": 42,
  "stage": "editing",                         // 新增：封闭枚举
  "message": "正在编辑 src/main.py",           // 新增：≤120 字符，人类可读
  "tool": "edit",                             // 新增：当前工具名，可空
  "detail": {                                  // 新增：可观测附加信息，可空
    "files": ["src/main.py"],                  // step.ended 的 files[]
    "tokens": {"input": 12000, "output": 800, "reasoning": 0,
               "cache": {"read": 9000, "write": 0}},
    "cost": 0.0,
    "compaction": {"state": "running", "reason": "auto"}   // 压缩中/完成
  },
  "todos": [{"content": "...", "status": "in_progress", "priority": "high"}]
}
```

`stage` 为**封闭枚举**（前端可直接映射中文标签）：
`preparing | planning | coding | editing | running_command | searching | testing | compacting | summarizing | idle`。

**兼容性**：节点**无条件**携带 `stage`；旧版中枢因 Pydantic 默认 `extra=ignore` 自动忽略
新增键（`todos` 当年即以此方式扩展），新增键均不破坏既有解析。`progress`/`todos` 语义不变。

`message` 生成规则（脱敏优先）：工具名 + 主参数摘要（`edit`/`write`/`read` 取文件相对路径，
`bash` 取命令首词与 ≤60 字符摘要，`grep`/`glob` 取模式），**截断 120 字符**，
**不得**包含绝对路径以外的敏感内容（沿用 `_sanitize_fetch_path` 的白名单式处理）。

### D3 · 凭据落点：**留在任务工作目录配置**（2026-09-27 修订，原「移出工作区」方案已废弃）

已核实 tilacode 配置加载顺序（`tilacode/packages/core/src/config.ts:135-203`）：
`~/.config/tilacode/tilacode.jsonc`（全局）→ 上层 `.tilacode/` → **工作目录 `.tilacode/tilacode.jsonc`（就近优先）**，
但**加载时机**才是决定性约束：

- **用户级配置只在 tilacode server 启动时快照一次**，运行期不热重载；
- **项目级（工作目录）配置随会话目录动态加载**，session 创建/重建实例时生效。

因此任务专属 provider（含 `apiKey`）**必须写入工作目录** `.tilacode/tilacode.jsonc`
的顶层 **单数** `provider` 键下。命名空间沿用 `<provider>-<task短ID>`（`hub-` 前缀），
session 创建时引用该具名 provider。

> ⚠️ 早期这里写作「复数 `providers` 会被严格 schema 校验拒绝/**或静默忽略**（取决于文件）」。
> 2026-09-28 实机实证推翻了后半句：**复数 `providers` 在全局与工作区两处都会被严格校验拒绝**，
> 不存在「静默忽略」这种情形。准确机制与完整键位契约见下方「tilacode 配置 schema 契约」。

**凭据防外泄改由产物侧保证**：`file_scanner` 整体排除 `.tilacode/`（不进产物清单与回传 zip）。

#### tilacode 配置 schema 契约（2026-09-28 实机实证）

> 以下结论由**实际编译 tilacode 二进制**（Windows x64，`--target windows-x64`）后运行
> `debug config` / `run --model` 得到，非源码推断。复现脚本见本 ADR 末尾。

**① 判据：文件里出现任一 V1 键 → 整个文件按 V1 解析**（`core/src/v1/config/migrate.ts:10-33`）

V1 专属键集合（命中任一即触发）：

```
logLevel server command reference snapshot plugin autoshare
disabled_providers enabled_providers small_model mode agent
provider permission tools attachment layout
```

**② 严格校验只作用于 V1 路径**（`opencode/src/config/parse.ts:74-79` 的 `topLevelExtraKeys`
→ 顶层未知键抛 `unrecognized_keys`，**整个文件作废**）。实测四组对照：

| 文件内容 | 结果 |
|---------|------|
| 全局 + 复数 `providers` + `_hub_managed_providers` | ❌ `Unrecognized keys: providers, _hub_managed_providers`（**与线上事故报错逐字一致**） |
| 全局 + 单数 `provider` | ✅ 正常加载 |
| 工作区 + 复数 `providers`（文件内**无任何 V1 键**） | ❌ `Unrecognized key: providers` |
| 工作区 + 复数 `providers` + `permission`（含 V1 键） | ❌ `Unrecognized key: providers` |

**结论：复数 `providers` 在「全局」与「工作区」两处都不能用**——我早先「复数在工作区被静默忽略」的注释是错的。
V2 的 catalog 插件确实只读 `file.info.providers`（`core/src/config/plugin/provider.ts:18,24,51`），
但该值**只能由 V1 `provider` 经 `ConfigMigrateV1.migrate` 迁移产生**（`migrate.ts:70`）。

**③ 因此工作区配置必须「全 V1 键、零 V2 键」**。当前代理层写法（单数 `provider` +
`permission` + `default_agent` + `instructions`）**全部是 V1 键，安全**。
**红线**：以后往工作区配置加 `providers`/`permissions`/`agents`/`plugins`/`commands`/
`references`/`compaction`/`snapshots` 等任一 V2 专有键 → 立刻触发严格校验、文件作废、任务不执行。
（`instructions`/`default_agent`/`model`/`shell`/`skills`/`references`/`command`/`plugin`/`mcp`/
`formatter`/`lsp`/`attachment`/`permission`/`tools`/`snapshot`/`watcher`/`compaction`/`experimental`
等 **V1 也有**，可安全使用。）

**④ V1 provider 定义的正确键位**（`core/src/v1/config/provider.ts:82-127`）：

```jsonc
{
  "provider": {
    "<namespace>": {
      "name": "deepseek",
      "npm": "@ai-sdk/openai-compatible",   // 可选：SDK 包名
      "api": "https://api.deepseek.com",    // 字符串 URL（不是对象！）
      "env": ["DEEPSEEK_API_KEY"],          // 可选：环境变量名
      "options": { "apiKey": "sk-…", "baseURL": "https://…" },  // ← 密钥在这里
      "models": { "deepseek-chat": { "name": "DeepSeek Chat" } }
    }
  }
}
```

- `api` 是**字符串**；V2 里才是 `{type, package, url}` 对象；
- `apiKey` 必须放 **`options.apiKey`**（或 `options.baseURL`）。放顶层会被 `Schema.Struct` 忽略；
- 顶层还有 `id`/`whitelist`/`blacklist` 可用。

**⑤ 端到端验证**：按上述键位写入工作区配置后，`run --model <ns>/deepseek-chat` 正确输出
`> build · deepseek-chat`，并在 `api` 指向的**不可路由地址**上超时（`The operation timed out.`）
——证明模型解析成功且请求确实发往自定义 provider，而非内置 `opencode` 云端。

**⑥ `~/.config/tilacode/.gitignore` 是正常行为**：`ensureGitignore`
（`opencode/src/config/config.ts:295-312`）会为每个配置目录写入
`node_modules/package.json/package-lock.json/bun.lock/.gitignore`，删掉会重建。
看到该文件不代表配置被污染。

#### 复现步骤（供后续回归）

```powershell
# 1. 构建 Windows 探针二进制（约 3 分钟；注意 BUILD.md：构建脚本首行是 `rm -rf dist`，
#    会清掉已有产物——注意先备份）
cd opc-agent-programming/tilacode/packages/opencode
$env:OPENCODE_CHANNEL = "dev"      # 跳过 `git branch --show-current`（沙箱/无 git 环境会失败）
bun run build --skip-embed-web-ui --target windows-x64   # baseline 变体编译器下载易失败，用非 baseline

# 2. 隔离探针（绝不碰真实 ~/.config/tilacode/）
$env:TILACODE_CONFIG_DIR = "<探针目录>\global-singular"
cd <探针目录>\ws-current           # 工作区含 .tilacode/tilacode.jsonc
& <产物>\bin\opencode.exe debug config     # 看配置是否被接受
& <产物>\bin\opencode.exe run --model "<ns>/deepseek-chat" "hi"
```

**两个陷阱（本次踩过，避免重犯）**：

1. `debug config` 输出的是**旧 V1 结构**（`provider`/`permission`/`mode`/`agent`），
   它**测不到 V2 catalog**。只用它判断「当前写法是否可用」会得到**假阴性**——
   必须用 `run --model` 看模型能否解析（成功时输出 `> build · <model>`）。
2. 探针 provider 的 `api` 要指向**不可路由地址**并设 `options.timeout`（毫秒），
   否则会长时间挂住（本次一度阻塞 3 分钟）。


> 原 D3 决策（写用户级配置 + TTL 清理）**已被推翻**，原因有二：
> 1. **功能上不可行**：只写用户级时运行中的 server 永远看不到该 provider
>    （`ModelUnavailableError`）；若把复数 `providers` 或记账键
>    `_hub_managed_providers` 写进用户级配置，新版 tilacode 的严格 schema 校验会
>    因「Unrecognized keys」**启动即退出**，节点表现为重启循环、任务永不执行；
> 2. 由此衍生出**必现回归**：`ensure_tilacode_config` 曾在每次任务开始时
>    「顺手剥离凭据」（`_strip_credentials`），而它先于 `_resolve_session_model`
>    执行——复用工作目录的第二轮任务 provider 被删除 → 模型解析失败 →
>    任务永远不执行。该剥离逻辑已删除，并新增自愈（剔除毒键、复数归并单数）
>    与回归用例锁定。


### D4 · 同项目目录串行化与配置写入原子化

- 新增进程内 `asyncio.Lock`（按 `project_key`）+ 跨进程文件锁 `data/locks/<project_key>.lock`
  （`filelock`/`fcntl` 语义，Windows 用 `msvcrt`），在锁内完成
  「配置写入 → 会话创建 → prompt 下发」。
- 所有 `.tilacode/*.jsonc` 写入改为「读-改-写 + 临时文件 + 原子替换」，并统一走
  `deepcopy` 模板，杜绝全局常量污染。

### D5 · 完成判定：以 `session/active` 短轮询为主，SSE 仅作事件源

- `step.ended` 后不再拆流：以 1s 间隔、上限 5s 轮询 `GET /api/session/active`
  判定 idle（`active` 是进程内即时状态，判定足够快且不损失事件）。
- SSE 仅在真正断流时按 `after=durable.seq` 重连（既有能力保留）。
- 仍**不依赖** `POST /api/session/{id}/wait`（未实现）。

### D6 · P1-9 全链路闭环（节点 → 中枢 → 前端）

- **节点**：`EventTranslator` 产出 `stage/message/tool/detail`，`hub_client` 透传（修掉「构造了却丢弃」）。
- **中枢**：`tasks` 表新增 `progress_stage`（String(32)）、`progress_message`（String(500)）两列
  （Alembic 迁移）；`ws_messages` 新增 `AgentTaskProgressPayload` Schema（遵守
  backend-component-standards §2.4「所有 WS 消息必须有 Schema」）；`_handle_task_progress`
  校验并落库（非法值仅告警不拦截）；`task_progress_updated` 日志填**真实** stage
  （现为硬编码 `"execution"`）。
- **前端**：`TaskSummary` 新增「当前动作」行（stage 中文标签 + message），无 todos 时
  不再只显示「暂无待办明细」；`TaskDetailInfo` 同步；`Task` 类型补齐字段。

## 备选方案

| 方案 | 结论 |
|------|------|
| **A（本决策）** 全链路修复 + 无进度看门狗 + 进度协议扩展 | **采纳**。P1-9 的本质是「信息在链路上被丢弃」，只修节点不显示、只修展示没有数据，必须三层同改；看门狗是唯一不误杀真实开发任务的超时语义 |
| B. 仅修节点侧（不动中枢/前端） | 否决（用户已明确要全链路）：P1-9 不闭环，用户看不到任何改善 |
| C. 超时按 `TASK_EXECUTION_TIMEOUT_SECONDS=300` 直接启用 | 否决：会中断绝大多数真实开发任务（规范默认值按「任务」计，与编程任务真实时长不匹配） |
| D. 完成判定依赖 `POST /api/session/{id}/wait` | 否决：tilacode 侧未实现（`core/src/session.ts:421-424` 返回 `OperationUnavailableError`），`012-tilacode-execution-layer-integration-spec.md:89` 早有同结论 |
| E. 凭据继续落任务工作区，仅在打包时排除 | 否决：工作区对执行 AI 与任何本机进程可见（AI 可 `read` 该文件并把它写进代码/日志），排除打包只是掩盖问题 |
| F. 用「总时长硬上限（大值）+ 看门狗」双保险 | 缓议：本决策保留 `TASK_EXECUTION_TIMEOUT_SECONDS` 作为可选上限（默认 0=不限），运维需要时自行开启 |
| G. 为并行探索改造 tilacode 源码移植 `task` 工具 | 否决（本次）：V2 core 明确未移植（`core/src/tool/builtins.ts:26-29` TODO）；大项目并行改由中枢拆多任务链实现，避免维护 fork 分叉 |
| H. P2-10 节点接 MCP | 否决（用户决策 2026-09-27）：节点侧暂不需要 |

## 影响

### Agent 节点（`opc-agent-programming`）

| 文件 | 计划变更 |
|------|---------|
| `app/executor/file_scanner.py` | 修：`os.scandir` 增量遍历 + 逐目录 deadline；排除清单补 `.env*`（保留 `.env.example`）/`.mcp.json`/`.npmrc`/`id_rsa*` |
| `app/executor/workspace.py` | 修：`prepare` 强制 `is_relative_to(workspace_root)`；配置写入原子化 + `deepcopy`；`upsert_provider_config` 改用户级路径 + 命名空间隔离 |
| `app/executor/task_bridge.py` | 修：`_pack_workspace`/扫描走 `asyncio.to_thread` + 上限保护；`change_summary` 改 `/context`；完成判定改短轮询；`finally` 中 `session_id` 未定义隐患；handoff 解压防护；新增看门狗；`D4` 锁 |
| `app/executor/sse_consumer.py` | 修：暴露 `last_activity_at`；完成判定不再拆流；压缩/重试事件处理 |
| `app/executor/event_translator.py` | 修：工具名取 `data.tool`；新增 `tool.progress`/`retried`/`compaction.*`；产出 `stage/message/tool/detail` |
| `app/executor/tilacode_manager.py` | 修：stderr 消费；monitor 全异常防护 + 退避 + 次数上限；动态端口/密码；健康检查落地 |
| `app/executor/session_store.py` | 修：`update_status` 单次加载；损坏不静默清空（备份 + 告警 + 抛错） |
| `app/client/hub_client.py` | 修：`_run_task` 兜底回执；进度 payload 透传新字段；心跳期补发进度（P2-13） |
| `app/config.py` | 增：`task_no_progress_timeout_seconds`；修：`task_execution_timeout_seconds` 语义与默认；`.env.example` 同步 |
| `app/models/schemas.py` | 增：`TaskDispatchPayload.timeout_seconds` |
| `tests/` | 增/修：见 Harness |

### 中枢（`opc-central-dispatch-hub`）

- `app/models/task.py`：增 `progress_stage`、`progress_message`；
- `alembic/versions/20260927_task_progress_detail.py`：**新增迁移**（含 `downgrade`）；
- `app/schemas/task.py`：`TaskResponse` 增两字段；
- `app/schemas/ws_messages.py`：增 `AgentTaskProgressPayload`；
- `app/services/task_event_handler.py`：`_handle_task_progress` 校验并落库新字段 + 真实 stage 日志。

### 前端（`opc-web-console`）

- `src/types/task.ts`：增 `progress_stage?`、`progress_message?`（+ `progress_detail?`）；
- `src/components/TaskSummary.tsx`：增「当前动作」行与空态改造；
- `src/components/TaskDetailInfo.tsx`：详情增「当前动作」；
- `src/pages/TaskNew.css`：增 `.task-summary__action` 样式（复用既有 token）；
- `src/__tests__/components/TaskSummary.test.tsx`：三态用例。

### 规范与决策文档

- `docs/standards/api-standards.md` §4.2：`agent.task_progress` payload 扩展 + 变更记录；
- `docs/standards/agent-node-standards.md` §5.3/§6：超时（看门狗语义）与进度字段（stage/message）对齐；
- `docs/INDEX.md`：1.1 / 1.2 / 1.3 / 六（文档更新记录）四处登记。

### 兼容性

- 旧版中枢 + 新节点：新增 payload 键被忽略，行为不变（`todos` 已有先例）；
- 新版中枢 + 旧节点：`progress_stage`/`progress_message` 为 NULL，前端按「无当前动作」降级展示；
- `TASK_EXECUTION_TIMEOUT_SECONDS` 默认 0（不限）与历史「从未生效」等价，行为不回退；
- 完成判定改动影响所有任务的终态时机，必须由 Harness 的集成用例覆盖。

### 明确不做（本次范围外）

- P2-10 节点侧 MCP 能力（用户决策）；
- tilacode 源码改造（含移植 `task`/LSP 工具）——大项目并行改由中枢拆任务链；
- 存量历史任务的 `progress_stage`/`progress_message` 回填（NULL 即「无当前动作」，前端已兼容）。

## 实际实施情况

**状态：已全量实施（2026-09-27）**，详情与文件/函数级明细见
[055-execution-layer-hardening-changes.md](../spec/055-execution-layer-hardening-changes.md)。

### 与决策的偏差

1. **P1-7（跨轮上下文）落地方式收窄**：原计划「压缩后向 `project_context.md` 落快照」
   未实施，改为**不重写内容一致的规则文档与 AGENTS.md**（`WorkspaceManager.rule_docs_materialized`）
   ——后者才是上下文 epoch 增量的直接来源，收益确定且可单测；`maxTokens` 下发因
   `create_session` 协议未暴露该字段而未做。
2. **完成判定实现细节**：原设为「静默到期后短轮询」，实施中改为**静默到期或流结束且处于
   静默态**两种情况都进入轮询——测试替身的流会立即结束，这一改动同时让生产环境在
   「step 结束即断流」时更快判完成（且不再为判定而撕流重连）。
3. **额外发现并修复 3 个缺陷**（不在原 13 项内，见 Changes 登记）：
   `TilacodeManager.monitor` 未清理死进程引用导致重启失败路径空转；
   轮次切换未结束上一轮 SSE 消费任务（僵尸订阅）；
   周期任务在 `asyncio.sleep` 被替换时会饿死事件循环（防御性加固）。

### 验证结果

| 门禁 | 结果 |
|------|------|
| 节点 `pytest tests/` | **157 passed**（新增 `test_tilacode_manager.py` 9 项 + 事件翻译/桥接/规则文档多组用例） |
| 中枢 `pytest tests/services/test_task_event_handler.py` | **29 passed**（新增 5 项 ADR-055 用例） |
| 前端 `vitest`（新增 TaskSummary 用例） | **5 passed** |
| 前端 `tsc -b` / `vite build` | 均通过 |
| `ruff check` | 节点改动文件仅剩 2 项**存量**问题（`tests/test_tilacode_client.py` E501）；中枢改动文件 All checks passed |
| 迁移 | `20260927_task_progress_detail` 链完整、已应用；实测 `information_schema.columns` 确认 `progress_stage` varchar(32) / `progress_message` varchar(500) 均 nullable |
| 前端全量回归 | 53 passed / 4 failed，其中 3 项经 `git stash` 基线复跑确认**同样失败**（聊天流相关，与本次改动文件无关） |

### 遗留

- 存量任务的 `progress_stage`/`progress_message` 为 NULL（前端已降级展示）；
- P2-10 节点侧 MCP 与「节点侧子代理能力」未做（后者建议由中枢拆任务链实现）；
- 前端 `TaskNew.test.tsx` 3 项既有失败未修（与本次范围无关，已记录在案）。

## 关联文档

- [055-execution-layer-hardening-spec.md](../spec/055-execution-layer-hardening-spec.md)
- [055-execution-layer-hardening-harness.md](../spec/055-execution-layer-hardening-harness.md)
- [055-execution-layer-hardening-changes.md](../spec/055-execution-layer-hardening-changes.md)
- [ADR-012](./012-tilacode-execution-layer-integration.md)（执行层集成与降级链）
- [ADR-028](./028-task-workspace-binding-and-artifact-sync.md)（产物清单与按需拉取）
- [ADR-029](./029-task-executor-model-dispatch.md) / [ADR-030](./030-llm-models-management.md)（执行模型与凭据下发）
- [ADR-032](./032-concurrent-task-scheduling.md)（并发与资源水位）
- [ADR-034](./034-task-handoff-context.md)（现场迁移与 `project_context.md`）
- [ADR-039](./039-multimodal-attachments-model-auto-select.md)（附件安全策略，本决策复用于 handoff 包）
- [ADR-047](./047-logging-v1.md)（日志事件命名）
- [ADR-048](./048-rule-docs-dispatch.md)（规则文档下发）
