# 前端 UI 盘点与页面结构：任务域（继承 ADR-052）

> 主关联 ADR：[ADR-052 前端亮色主题与颜色收敛](../adr/052-light-theme.md)（按 `.trae/rules/decision-documents.md` 序号归属边界规则：无自身 ADR 的 UI 迭代/附属文档继承主关联 ADR 序号）
> 规范依据：[frontend-component-standards.md §3.2 设计 token](../standards/frontend-component-standards.md)｜循环依据：`.trae/rules/e2e-dev-loop.md`
> **本批次只出文档，不改任何代码**；每节标注【依据】= 已核实来源，【待审核】= 我的推断需你确认。

## 1. 本批次范围【依据】

| 项 | 内容 |
|---|---|
| 路由 | `/tasks` → `TaskQueue`（70 行）、`/tasks/new` → `TaskNew`（**760 行**，全站最大页面）；**全站无 `tasks/:id`** |
| 页面文件 | `src/pages/TaskQueue.tsx`、`src/pages/TaskNew.tsx` |
| 域内组件（22） | `TaskTable`、`TaskDetailDrawer`、`TaskDetailInfo`、`TaskDetailArtifacts`、`TaskDetailModals`、`TaskSessionDrawer`、`TaskNewChatPanel`、`TaskNewRightPanel`、`TaskNewInputBar`、`TaskNewCommandPalette`、`TaskNewDocPanel`、`TaskNewToolDefs`、`TaskToolEmpty`、`TaskInsightPanel`、`TaskSummary`、`TaskSummaryPanels`、`TaskSummaryTodoSections`、`TaskExecutorModelSelect`、`PlanReviewPanel`、`AIDispatchCard`、`NodeTraceBlock`、`DispatchEligibilityHint` |
| 域内 hooks（16） | `useTaskList`、`useTaskBuildingChat`(**604 行**)、`useTaskBuildingSessions`、`useTaskSessionActions`、`useTaskPlanTracking`、`useTaskPlanActions`、`useTaskAgentTrace`(175)、`usePlanReviewFlow`、`useNodeChatReplyPoll`、`useParentTaskContext`、`usePolling`、`useSessionParamRestore`、`useDeliverableList`、`useResizablePanel`、`useMediaQuery`、`useDesktopShell` |
| 域内 api（4） | `api/tasks.ts`(288)、`api/taskBuilding.ts`(166)、`api/taskBuildingSessions.ts`(42)、`api/attachments.ts`(50) |
| 相关决策 | ADR-012 任务链、ADR-028 工作目录绑定与产物同步、ADR-029 执行模型与凭据下发、ADR-032/033 并发与智能调度、ADR-053 中枢 PTY 终端、ADR-055 执行层加固与进度语义、ADR-059 调度准入与产物韧性、ADR-061 M4 验收闸与 `verify_blocked`、M5a steer、M5c 只读洞察、M5d 编排器待办、ADR-063 轨迹镜像折叠块、ADR-064 节点续聊 |

## 2. 路径清单（每条含 入口 / 目标 / 成功 / 失败 / 返回 / 中断 / 重试）

### P1 查看与筛选任务列表【依据：`TaskQueue` + `TaskTable` + `useTaskList`】

| 维度 | 内容 |
|---|---|
| 入口 | 左侧导航「任务」；`Dashboard` 的 `DashboardTaskTable`（【待审核】点击落点：跳 `/tasks` 还是直接开详情抽屉） |
| 目标 | 找到目标任务并进入其详情 |
| 成功 | 列表渲染 + 状态/节点/进度可见；点击行 → **打开详情抽屉**（`TaskDetailDrawer`，`【待审核】` 因无 `tasks/:id` 路由） |
| 失败 | 列表接口失败 → 【待审核】表现为空列表还是错误提示（需你确认期望：应显式错误态 + 重试按钮） |
| 返回 | 关闭抽屉回到列表，**保留筛选/分页/滚动位置**（【待审核】现状是否保留） |
| 中断 | 无（只读） |
| 重试 | 【待审核】是否需要「重新加载」按钮 |

### P2 新建任务与派发（主线）【依据：`TaskNew` + `useTaskBuildingChat` + `api/taskBuilding` + ADR-032/033】

| 维度 | 内容 |
|---|---|
| 入口 | `/tasks` 的「新建任务」；`TaskNew` 直链 `/tasks/new` |
| 目标 | 把需求讲清楚 → 生成三件套规范 → 选执行模型/工作目录 → 派发到节点 |
| 成功 | 会话流出现「创建任务」→「生成三件套规范文档」→「智能派发任务」步骤；右栏「任务摘要」可见；任务进入 pending→running |
| 失败 | 规划失败 / 派发被拦（`DispatchEligibilityHint` 提示准入原因）/ 无可用节点 → 会话流给出可读原因（`title="对话失败"` 弹窗） |
| 返回 | 会话历史可回溯（`?session=` 参数恢复，`useSessionParamRestore`）；切页回来不丢 |
| 中断 | 「停止」按钮 → 中断当前会话（`useTaskBuildingChat.stop`） |
| 重试 | 重新发送 / 重新派发（【待审核】是否有显式「重试派发」入口） |

### P3 执行中干预（四路）【依据：ADR-061 M5a/M5c/M4 + ADR-063/064】

| 子路径 | 入口 | 目标/成功 | 失败 | 返回/中断/重试 |
|---|---|---|---|---|
| P3.1 插话 steer | `TaskInsightPanel` 的「@节点」输入框 / 底部输入栏 steer 态 | 不中断任务补充要求；返回 `delivered` | 空文本 422；节点已结束 | 不改任务状态；可重复发言 |
| P3.2 `@空闲节点` 续聊 | 同一入口（节点空闲时） | 节点对该会话续聊 → `agent.node_chat_reply` → 会话流显示「节点回复」徽标 | 300s 超时尽力中断；失败标记保留可见 | `reply_to` 轻量轮询（2s×320s，ADR-064 M3） |
| P3.3 验收阻断决策 | 任务摘要区「已阻断」入口 | 「接受现状 / 继续修复」二选一 → 任务状态流转 | 非 `verify_blocked` 返回 409 | 24h 无决策由调度器兜底置 failed |
| P3.4 预算告警 | 任务摘要/洞察区告警条 | 80% 软提醒、100% 硬停（ADR-061 M5b） | — | 只读展示 |

### P4 查看执行过程与产物【依据：ADR-063 折叠块 + `TaskDetailArtifacts` + `useDeliverableList`】

| 维度 | 内容 |
|---|---|
| 入口 | 会话流中的节点折叠块（`NodeTraceBlock`）/ 详情抽屉「产物」区 |
| 目标 | 实时看到节点在做什么、怎么想的；取回产物 |
| 成功 | 展开后按 `after_seq` 增量拉取轨迹（思考/正文/工具）；产物列表可下载 |
| 失败 | 轨迹接口失败 → 【待审核】是否显示可重试的错误态（建议：块内错误态 + 重试） |
| 返回/中断 | 收起即停轮询；再次展开续拉（自愈） |

### P5 失败处置（重试 / 改派 / 取消）【依据：ADR-059 + `TaskDetailModals` + `TaskService`】

| 维度 | 内容 |
|---|---|
| 入口 | 任务详情抽屉的操作区 |
| 目标 | requeue（重派）/ cancel（取消）/ 查看失败原因 |
| 成功 | 状态流转可见（等待队列 → running） |
| 失败 | 准入不通过时给出原因（`DispatchEligibilityHint`） |
| 中断 | 取消为终态（需二次确认） |

### P6 中枢终端【依据：ADR-053】

| 维度 | 内容 |
|---|---|
| 入口 | `TerminalDrawer`（按 ADR-053 在命令执行时**自动展开**） |
| 目标 | 旁观看中枢侧命令执行、必要时手动审批 |
| 成功 | 终端实时输出；审批弹窗可批准/拒绝 |
| 中断 | 关闭抽屉不影响执行（重开续看） |

## 3. 往返流程图（ASCII）

```
                    ┌─────────────────────────── 左侧导航「任务」 ───────────────────────────┐
                    ▼                                                                      │
            ┌───────────────┐   点行   ┌────────────────────────┐   关闭      ┌──────────┐ │
            │  /tasks       │─────────▶│  详情抽屉              │────────────▶│ 回列表   │ │
            │  TaskQueue    │◀─────────│  TaskDetailDrawer      │◀────────────│(保留筛选)│ │
            └───────┬───────┘   ←返回   │  ├ 信息 TaskDetailInfo │   返回      └──────────┘ │
                    │                   │  ├ 产物 TaskDetailArtifacts                       │
       「新建任务」  │                   │  ├ 会话 TaskSessionDrawer                          │
                    ▼                   │  └ 操作/确认 TaskDetailModals                      │
            ┌───────────────┐           └───────────┬───────────────────────────────────────┘
            │ /tasks/new    │                       │  P5 重试/改派/取消
            │  TaskNew      │                       ▼
            │  ├ 左：会话流 │◀── P3.1 steer / P3.2 @空闲续聊 / P3.3 阻断决策 ──┐
            │  ├ 右：规范/摘要/方案 ──► P3.4 预算告警（只读）                  │
            │  ├ 下：输入栏 │                                                  │
            │  └ 块：NodeTraceBlock（P4 实时轨迹，默认折叠）                   │
            └───────┬───────┘                                                  │
                    │ 派发成功                                                  │
                    ▼                                                           │
        ┌──────────────────────────┐  失败/超时   ┌──────────────────────┐      │
        │ 任务执行中（pending→running）│────────────▶│ 失败态（原因可见）    │──────┘
        └───────────┬──────────────┘              │ 可 requeue（P5）      │
                    │ 完成                        └──────────────────────┘
                    ▼
        ┌──────────────────────────┐  必过验收不过  ┌──────────────────────┐
        │ completed（产物可见）      │◀──────────────│ verify_blocked       │
        └──────────────────────────┘   继续修复     │ 决策入口（P3.3）      │
                                                  │ 24h 兜底 → failed     │
                                                  └──────────────────────┘

三条回边（全站统一要求）：① 返回 = 关闭抽屉/离开页面且保留上下文；② 中断 = 停止/取消，需明确终态与二次确认；③ 失败重试 = 每处失败都要有可读原因 + 可执行的重试入口
```

## 4. 页面清单表

| 页面 | 主对象 | 主要区块 | 入口来源 | 主操作 | 空态 | 加载态 | 异常态 | 形态 |
|---|---|---|---|---|---|---|---|---|
| `/tasks` `TaskQueue` | 任务集合 | 筛选栏 + `TaskTable`（状态/节点/进度/操作） | 左侧导航；Dashboard 任务表【待审核】 | 打开详情；新建任务 | 「暂无任务」+ 引导去新建【待审核】 | 表格骨架/Spin | 加载失败提示 + 重试【待审核】 | 独立页 |
| `/tasks/new` `TaskNew` | **一次任务创建会话** | 左：`TaskNewChatPanel`（会话流 + 步骤卡 + `PlanReviewPanel` + `AIDispatchCard`）｜右：`TaskNewRightPanel`（规范文档/任务摘要/方案 三区 + 工具面板收起/展开）｜下：`TaskNewInputBar`（含命令面板 `TaskNewCommandPalette`）｜内嵌：`NodeTraceBlock` 折叠块 | 任务列表「新建任务」；直链 | 发消息 → 生成规范 → 选模型/目录 → 派发 | 首屏引导语 + 输入框 | 步骤卡「执行中」 | 「对话失败」弹窗 + 可重发 | 独立页 |
| 详情层（**当前**） | 单个任务 | `TaskDetailDrawer`：信息/产物/会话/操作 多段 | 任务列表点行 | 查看、requeue、cancel、决策 | 各段空态 | 段内 Spin | 段内错误【待审核】 | **抽屉**（无 `tasks/:id` 路由） |

**「页面是否围绕主对象展开」检查**：`TaskQueue` ✅ 只服务"任务集合"；`TaskNew` ✅ 只服务"本次创建会话"（但 760 行、右栏三区并存 → 建议按 §6 拆分）；详情层当前是抽屉 ⇒ **无法分享链接/刷新恢复**，与 `nodes/:id/metrics`（独立页）不一致。

## 5. 共用组件清单（任务域）

| 组件 | 现状 | 建议 |
|---|---|---|
| `Markdown` / `MarkdownView` | 已有，但**不支持表格**（`Markdown.tsx:17` 明示） | **需统一规格并补 GFM 表格**（任务/办公交付物大量用表格） |
| `NodeTraceBlock` | 已有（ADR-063） | 跨节点类型复用（办公节点 M5 也要用同一块） |
| `TaskSummary` / `TaskSummaryPanels` / `TaskSummaryTodoSections` | 已有（ADR-061 体检时拆分过） | 作为"任务摘要"标准组件，避免各页各写 |
| `TaskInsightPanel` | 已有（ADR-061 M5c） | 与「@节点」入口绑定；**需抽出 `SteerInput` 供会话流与面板共用** |
| `PlanReviewPanel` / `AIDispatchCard` | 已有 | 域内共享；样式需纳入 token 规范 |
| `TaskTable` / `DashboardTaskTable` | **两份表格实现** | **需抽出统一 `DataTable` 包装**（列定义/空态/错误态/分页） |
| `TaskExecutorModelSelect` | 已有 | 跨域复用（办公节点也要选模型） |
| `DispatchEligibilityHint` | 已有 | 准入原因展示标准件 |
| **缺失（需新建并统一规格）** | — | `PageHeader`（页头：主对象 + 状态 + 主操作）、`EmptyState`、`LoadingState`、`ErrorState`（含重试）、`FilterBar`、`ConfirmAction`（危险操作二次确认）、`StatusTag`（`NodeStatusTag` 可泛化到任务状态） |

## 6. 本批结论与建议（待你审核）

1. **详情层提为独立页**：新增 `tasks/:id`，抽屉保留给"轻量查看/局部操作"。收益：可分享、可刷新、返回语义与其他域一致（与 `nodes/:id/metrics` 对齐）。
2. **`TaskNew` 需拆区块**：760 行 + 右栏三区 + 会话流 + 折叠块 → 建议按"会话区 / 右栏区 / 输入区 / 轨迹块"四块拆组件，容器间距一律走 §3.2 语义 token。
3. **抽出 7 个基础件**（上表"缺失"行）：它们是"页面内部怎么排"的载体，先有它们，后面每个域才有统一骨架。
4. **`Markdown` 补 GFM 表格**：任务与办公交付物高频使用，当前明确不支持。

## 7. 待你确认的问题（本批阻塞项）

| # | 问题 | 我的建议 |
|---|---|---|
| Q1 | 详情层用**独立页**还是继续抽屉？ | 独立页（可分享/刷新/统一返回） |
| Q2 | Dashboard 任务表点击落点：`/tasks` 还是直接详情？ | 直接进详情（少一步），并保留回到列表的筛选 |
| Q3 | `TaskSessionDrawer` 与 `TaskDetailDrawer` 的职责边界？ | 会话历史独立成段（或独立页签），不与"任务信息"混在一层 |
| Q4 | 列表/轨迹/产物的**失败态**要不要统一"错误 + 重试"？ | 要（纳入 `ErrorState` 标准件，全站统一） |
| Q5 | `TaskNew` 右栏三区（规范文档/任务摘要/方案）是并列 Tab 还是可叠放？ | 保持并列但统一容器与空态；工具面板收起状态持久化 |
