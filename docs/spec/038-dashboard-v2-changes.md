# 集群仪表盘 v2 变更清单（Changes）

> 关联决策：[ADR-038](../adr/038-dashboard-v2-cluster-metrics-alerts-deliverables.md)
> 按 `.trae/rules/decision-documents.md` 要求，边开发边实时登记文件/函数级明细。

## 一、决策文档

| 文档 | 路径 |
|------|------|
| ADR | docs/adr/038-dashboard-v2-cluster-metrics-alerts-deliverables.md |
| Spec | docs/spec/038-dashboard-v2-spec.md |
| Harness | docs/spec/038-dashboard-v2-harness.md |
| Changes | docs/spec/038-dashboard-v2-changes.md（本文件） |

## 二、变更清单

### 后端（opc-central-dispatch-hub）

| # | 文件 | 类型 | 函数/类级明细 | 作用 |
|---|------|------|--------------|------|
| 1 | alembic/versions/20260917_dashboard_v2.py | 增 | upgrade/downgrade | tasks.priority 列 + status CHECK 重建含 review（DROP chk_tasks_status/status 双名兜底）+ llm_usage_records 表 |
| 2 | app/models/task.py | 修 | TaskStatus 增加 REVIEW；Task 增加 priority 列 | 状态机与优先级字段 |
| 3 | app/models/llm_usage_record.py | 增 | LlmUsageRecord 模型 | token 用量记录表 |
| 4 | app/models/__init__.py | 修 | 导出 LlmUsageRecord | 模型注册 |
| 5 | app/schemas/task.py | 修 | TaskCreateRequest.priority（Literal P0/P1/P2）；TaskResponse.priority | 创建可选传、响应返回 |
| 6 | app/schemas/dashboard.py | 增 | ClusterMetricsResponse/AlertItem/AlertListResponse/DeliverableItem/DeliverableListResponse | 三端点 Schema |
| 7 | app/services/task_event_handler.py | 修 | _handle_task_completed：plan+plan_content → review；task_completed 日志加 status | review 流转 |
| 8 | app/services/plan_review_service.py | 修 | _get_completed_plan_task 放行 review（兼容存量 completed）；导入 TaskStatus | 审批兼容 |
| 9 | app/services/task_service.py | 修 | list_tasks 增加 keyword（title ILIKE）；两处 Task 构造加 priority | 搜索与优先级 |
| 10 | app/api/v1/endpoints/tasks.py | 修 | list_tasks 增加 keyword Query 参数并透传 | 搜索入口 |
| 11 | app/clients/deepseek.py | 修 | 构造器增加 scene；新增 _record_usage（fire-and-forget 落库）；chat_completion_raw/messages 捕获 usage；_parse_sse_lines 捕获末帧 usage（choices 空数组兼容）；_build_payload stream_options.include_usage | token 记录埋点 |
| 12 | app/agents/spec_builder.py / planning.py / orchestrator.py、app/services/dispatch_decision_service.py | 修 | DeepSeekClient 构造传 scene=spec_chat/planning/orchestrator/ai_dispatch | 用量场景标注 |
| 13 | app/services/dashboard_service.py | 增 | DashboardService：get_metrics/_cluster_load_percent/get_alerts/_collect_alerts/get_deliverables/_file_type_of/_start_of_today_utc | 三端点聚合业务逻辑 |
| 14 | app/api/v1/endpoints/dashboard.py | 增 | GET /dashboard/metrics|alerts|deliverables | API 层 |
| 15 | app/api/v1/api.py | 修 | 注册 dashboard router（prefix=/dashboard） | 路由挂载 |
| 16 | tests/api/v1/test_dashboard.py | 增 | metrics 聚合/无快照/load 为 null、告警三态、交付物排序/类型映射/空态、三端点 HTTP 200（8 项） | UT-8/9/10 |
| 17 | tests/services/test_task_event_handler.py | 增 | plan+方案→review；plan 无方案→completed（2 项） | UT-1/2/3 |
| 18 | tests/api/v1/test_task_dispatch.py | 增 | approve/reject 对 review 状态成功（2 项）；_make_plan_task 进度适配 | UT-4/5 |
| 19 | tests/clients/test_deepseek.py | 增 | usage 落库/无 usage 跳过/落库失败不影响主调用/流式末帧捕获（4 项）+ usage_db_recorder fixture | UT-11/12 |

### 前端（opc-web-console）

| # | 文件 | 类型 | 函数/组件级明细 | 作用 |
|---|------|------|----------------|------|
| 20 | src/types/task.ts | 修 | TaskStatus + 'review'；TaskPriority 类型；Task.priority；TaskListParams.keyword；TaskListResponse 改 pagination 结构 | 类型扩展 |
| 21 | src/types/dashboard.ts | 增 | ClusterMetrics/AlertItem/AlertLevel/DeliverableItem/DeliverableFileType 等 | 三接口类型 |
| 22 | src/types/common.ts | 增 | PaginationMeta（对齐后端 PaginationInfo） | 分页元信息 |
| 23 | src/types/agentNode.ts | 修 | AgentNodeListResponse 改 pagination 结构 | 修复存量 total=0 缺陷 |
| 24 | src/api/dashboard.ts | 增 | fetchClusterMetrics/fetchAlerts/fetchDeliverables | API 封装 |
| 25 | src/hooks/useTaskList.ts、useAgentNodeList.ts | 修 | keyword 参数透传；total 改读 pagination.total | 搜索 + 修复存量 total=0 |
| 26 | src/hooks/useClusterMetrics.ts、useAlertList.ts、useDeliverableList.ts | 增 | 三个轮询 hooks（30s/30s/60s） | 数据获取 |
| 27 | src/theme/tokens.ts | 修 | COLORS 增 alertDangerBg/alertWarningBg/alertInfoBg | 告警三色 |
| 28 | src/components/StatCard.tsx | 修 | StatCardProps 增加 danger（数值红色） | 异常事件红字 |
| 29 | src/components/dashboard/DashboardTaskTable.tsx | 增 | 精简六列表格（ID/名称/优先级/状态/节点/进度） | 任务队列 Tab |
| 30 | src/components/dashboard/AlertList.tsx | 增 | 三态告警列表（点击任务类告警打开详情） | 告警 Tab |
| 31 | src/components/dashboard/DeliverableList.tsx | 增 | 交付物列表（下载走 /artifacts/download、查看开详情抽屉） | 交付物 Tab |
| 32 | src/components/dashboard/MetricsCards.tsx | 增 | 8 统计卡两排（含 token 人性化格式） | 统计区 |
| 33 | src/components/NodeCluster.tsx | 修 | 类型图标映射 AGENT_TYPE_ICON_MAP + loadBarColor 负载分色 | 节点卡片升级 |
| 34 | src/pages/Dashboard.tsx | 重构 | 页头刷新/设置/新建任务；8 卡片；Tabs 三 Tab + 角标；搜索 300ms 防抖 + 状态筛选 Dropdown + 分页；TaskDetailDrawer 查看联动 | 页面重构 |
| 35 | src/components/TaskTable.tsx、PlanReviewPanel.tsx、TaskDetailDrawer.tsx、hooks/useTaskPlanTracking.ts | 修 | review Tag 映射；canReview 放行 review；isTerminal 含 review | review 状态适配 |

### 存量缺陷顺带修复（本次联调发现）

| # | 缺陷 | 修复 |
|---|------|------|
| F1 | 前端 TaskListResponse/AgentNodeListResponse 类型写成顶层 total，与后端 pagination 结构不符，导致仪表盘分页「共 0 条」、统计卡节点总数恒 0（msw mock 与真实结构不一致掩盖） | 类型对齐 + 两个 hooks 改读 pagination.total（#23/#25） |
| F2 | tests/api/v1/test_task_lifecycle.py `_delete_node` 清理时未先删节点任务的 dispatch_decisions（ADR-033 后调度会记录决策审计行），DELETE tasks 触发外键违约 | 删除任务前先按 assigned_node_id 子查询清理 DispatchDecision |
| F3 | app/api/v1/endpoints/tasks.py keyword 用 `Query()` 作默认值，端点被直接调用（测试）时拿到 truthy Query 对象导致 ILIKE 匹配空、列表恒空 | 改 `Annotated[str \| None, Query(...)] = None`，默认值回归真 None |
| F4 | tests/services/test_stale_task_detector.py 契约测试构造 ORM Task 未 flush，priority 为 None，TaskResponse 新增必填 priority 后校验失败 | fake_task 显式补 priority="P2" |

### 优化追加（用户反馈远程库下统计卡全 0 / 接口超时后）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 36 | app/services/dashboard_service.py | 修 | metrics 往返 8→4 次：节点表全量一次取回同时推导计数/CPU 均值/离线告警行（替代 group-by + cluster_load + offline 3 查询）；失败/待确认/排队超时任务告警合并为 1 条 OR 查询后 Python 分桶截断（3→1）；模块 docstring 记录往返数约束 | 远程高延迟下 metrics 从 >10s（超 axios 超时→统计卡全 0）降至 ~1s |
| 37 | src/api/dashboard.ts | 修 | 仪表盘三接口单独 timeout 30s（默认 10s 在远程库抖动下不够） | 避免慢窗口下统计卡持续全 0 |
| 38 | src/components/NodeCluster.tsx | 修 | 负载文本 28px→38px + nowrap | 修复「34.6%」换行 |

### 功能追加：告警一键已读（用户需求）

语义：告警为后端实时推导（无持久化行），已读采用**水位线方案**——`system_settings.alerts_read_at` 记录已读时刻，`created_at` 早于等于水位线的告警不再返回；此后新产生（事件时间更新）的告警正常出现，角标（未读数）随之恢复。

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 39 | alembic/versions/20260917_alerts_read_at.py | 增 | system_settings 加 alerts_read_at 列 | 水位线持久化 |
| 40 | app/models/system_settings.py | 修 | SystemSettings.alerts_read_at | 模型字段 |
| 41 | app/services/dashboard_service.py | 修 | mark_all_alerts_read / _get_or_create_settings / _get_alerts_read_at；_collect_alerts 尾部按水位线过滤 | 已读业务 |
| 42 | app/schemas/dashboard.py + app/api/v1/endpoints/dashboard.py | 修 | AlertReadAllResponse；POST /dashboard/alerts/read-all | API |
| 43 | tests/api/v1/test_dashboard.py | 增 | 水位线隐藏+事件更新重现；HTTP read-all 返回水位线并清零（2 项） | 回归保障 |
| 44 | src/types/dashboard.ts、src/api/dashboard.ts、src/hooks/useAlertList.ts | 修/增 | AlertReadAllResponse；markAllAlertsRead；markingRead + markAllRead | 前端链路 |
| 45 | src/pages/Dashboard.tsx | 修 | 告警 Tab tabBarExtraContent 增加「一键已读」按钮（有未读时显示，loading 态） | 交互入口 |

复测（真实后端）：27 条未读 → read-all 0.87s 水位线落库 → 未读归 0；后端定向测试 10 passed；前端 tsc/eslint 通过。

复测（网络抖动窗口内）：metrics 0.9~1.7s、alerts 1.3s、deliverables 2.7s，全部低于前端 10s 默认与 30s 放宽超时。

### 联调验证记录

- 中枢测试：定向 53 项（dashboard/deepseek/event_handler/task_dispatch）全过；全量 328 passed + 上述 F2~F4 修复后定向复验 4/4 通过（另有 1 例 WS 进度断言为固定 0.2s 等待在远程 DB 高延迟下的偶发竞态，复跑即过，非本次引入）。
- 前端：tsc / eslint 通过；vitest 27 passed（3 个 TaskNew 存量失败为上会话布局改版未同步测试，与本次无关）。
- 浏览器复验：8 统计卡真实数据、告警角标 27 + danger/warning 三态、交付物真实列表、分页「共 63 条」（F1 修复生效）、节点卡片升级样式均确认。
- 环境备注：开发库位于远程 192.168.101.101:5432，当日网络抖动（建连 0.4~7.6s 波动）导致过一次后端连接池耗尽与搜索请求超 axios 10s 超时，均为环境问题非本次代码缺陷。
