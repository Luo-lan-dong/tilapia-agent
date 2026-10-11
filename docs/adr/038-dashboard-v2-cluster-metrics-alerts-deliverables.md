# ADR-038: 集群仪表盘 v2（8 统计卡 + 三 Tab 左栏 + review 状态 + token 用量记录）

## 状态

Accepted

## 日期

2026-09-17

## 问题背景

设计稿 `设计稿/集群仪表盘`（v2）对仪表盘模块（ADR-007）提出大版本迭代：
统计卡 4→8、左栏改三 Tab（任务队列/异常告警/交付物聚合）、任务表格新增
优先级列与搜索/分页、节点卡片升级、页头新增刷新/设置按钮、全部图标
SVG 化（落地用 @ant-design/icons）。现有后端无法支撑：

1. 无集群统计聚合端点（KPI 由前端从列表自算，且仅 4 项）。
2. 无告警数据源；无跨任务交付物聚合端点（ADR-028 仅单任务产物）。
3. `tasks` 无 `priority` 字段；任务列表不支持 keyword 搜索。
4. 设计稿新增"待确认"状态，现状态机仅 5 态。
5. Token 消耗无任何数据来源（`DeepSeekClient` 丢弃响应 `usage`）。

## 决策

1. **新增仪表盘聚合端点三件**（`/api/v1/dashboard/*`，统一 ApiResponse）：
   - `GET /metrics`：一次返回 8 项 KPI（total/running/completed_tasks、
     online/total_nodes、cluster_load_percent=在线节点 `metrics_snapshot`
     CPU 均值、alert_count、token_consumed_today、queued_tasks=pending 数）。
   - `GET /alerts`：后端推导告警列表。**判定规则**（用户已确认）：
     danger=节点离线；warning=失败任务 + 待确认（review）任务；
     info=创建超 10 分钟仍 pending 的排队任务。超时（stale）任务已置
     failed 归入 warning，不单列。
   - `GET /deliverables`：聚合最近有产物缓存任务的文件清单
     （复用 artifact_service 缓存，只读不触发网络拉取），返回
     task_id/task_title/node_name/path/size/mtime/file_type。
2. **引入 `review` 任务状态**（用户确认）：语义 = plan 模式任务 Agent 上报
   completed 且带 plan_content 时，中枢置 `review`（等待人工审批）而非
   `completed`；approve-plan / reject-plan 放行 `review`（兼容存量
   completed 的 plan 任务）；批准后转 build 重新入队，驳回后以 plan
   重新入队，均回到既有流转。build 任务完成仍为 `completed`，状态机其余
   语义不变（用户确认：任务队列整体状态不改）。需迁移 status CHECK 约束。
3. **任务优先级**：`tasks` 新增 `priority` 列（`P0|P1|P2`，默认 `P2`，
   非空）；创建请求可选传入；列表响应返回。暂无自动定级，后续迭代接
   Planning Agent。
4. **ETA 不做**（用户确认）：无数据来源且不改任务状态语义，任务表格不放
   ETA 列（与设计稿的偏差记录于 Spec）。
5. **token 用量记录（仅中枢侧）**：新增 `llm_usage_records` 表（scene/model/
   prompt_tokens/completion_tokens/total_tokens/created_at）；
   `DeepSeekClient` 作为中枢全部 LLM 调用的唯一咽喉，成功响应后异步落库
   usage（流式请求带 `stream_options.include_usage` 取末帧 usage）。
   `token_consumed_today` = 当日记录合计。**Agent 节点侧执行用量无上报
   协议，本期不计入**，列入后续迭代（用户确认口径为"总用量含中枢"）。
6. **任务列表搜索**：`GET /tasks` 新增 `keyword` 参数（title ILIKE，
   与 status/agent_type 可叠加），前端 300ms 防抖走后端（设计说明 §7.7）。
7. **前端落地**遵循设计说明 §7：tokens.ts 新增告警三色、Tab/Input.Search/
   Tag/Pagination/@ant-design/icons（禁 emoji）、新增 useClusterMetrics/
   useAlertList/useDeliverableList、组件 ≤200 行。仪表盘任务表格用精简
   版组件（ID/名称/优先级/状态/节点/进度），共享 TaskTable 保持不变供
   任务队列页使用。

## 备选方案

| 方案 | 结论 |
|------|------|
| KPI 由前端并行拉列表自算 | 否决。8 项中 5 项无法从列表推出（负载均值/告警数/今日 token/排队数/全量完成数），且多次往返 |
| ETA 由 todos 进度线性外推 | 否决（用户确认"算了"）。估算不准且引入误导 |
| review 用前端映射（completed+plan 显示为待确认） | 否决（用户确认加真实状态）。告警聚合、角标计数、跨页筛选都需要服务端状态 |
| token 用量写 system_settings JSONB 计数器 | 否决。无法按日聚合与回溯，独立表成本极低 |
| 交付物聚合遍历 data/artifacts/ 目录 | 否决。目录结构属实现细节，复用 artifact_service 内存缓存清单更稳 |
| 仪表盘直接改共享 TaskTable | 否决。任务队列页依赖操作列/派发列，会引入回归 |

## 影响

- **数据库**：1 个迁移——`tasks` 加 `priority` + 重建 status CHECK（含
  review）+ 新建 `llm_usage_records`。
- **中枢**：TaskStatus/Schema/服务/端点扩展；completed 事件与审批服务
  适配 review；DeepSeekClient 埋点。无 Agent 协议变更（Agent 端零改动）。
- **前端**：Dashboard 重构 + review 状态映射（共享 TaskTable/TaskQueue/
  PlanReview 条件扩展）。
- **api-standards.md**：登记 3 个新端点与 keyword 参数。

## 关联文档

- [038-dashboard-v2-spec.md](../spec/038-dashboard-v2-spec.md)
- [038-dashboard-v2-harness.md](../spec/038-dashboard-v2-harness.md)
- [038-dashboard-v2-changes.md](../spec/038-dashboard-v2-changes.md)
- ADR-007（前端管理控制台仪表盘，本 ADR 为其 v2 迭代）
- ADR-014（节点指标心跳）、ADR-028（任务产物资源管理器）
- 设计稿：`设计稿/集群仪表盘/设计说明.md`
