# 集群仪表盘 v2 Spec

> 依据设计稿 `设计稿/集群仪表盘`（v2）与 ADR-038 决策编写。
> 上游文档：[007-web-console-dashboard-spec.md](./007-web-console-dashboard-spec.md)（v1，仍然有效）。

## 1. 功能描述

集群仪表盘升级为实时指挥中心视图：

- 8 个统计卡片（两排）：总任务数 / 进行中 / 已完成 / 在线节点 +
  集群负载 / 异常事件 / Token 消耗 / 排队任务。
- 页头操作：刷新（立即重新拉取全部数据）、设置（跳系统设置）、新建任务。
- 左栏三 Tab 容器（1.7fr）：任务队列（默认）/ 异常告警（红色角标）/
  交付物聚合；仅任务 Tab 显示搜索框 + 筛选按钮。
- 右栏（1fr）：节点集群卡片网格（SVG 类型图标 + 状态点 + 计数标签 +
  负载彩色进度条）。
- 任务表格精简列：任务 ID / 名称 / 优先级（P0 红 P1 橙 P2 灰）/ 状态 /
  分配节点 / 进度；底部分页。

**与设计稿的偏差**（ADR-038 决策 4）：
- 不做 ETA 列（无数据来源，用户确认不做）。
- Token 消耗本期仅统计中枢侧 LLM 调用（Agent 侧无上报协议）。

## 2. 用户故事

- 作为 OPC 用户，我希望一屏看到集群负载、异常、排队与 token 消耗，掌握全局。
- 作为 OPC 用户，我希望在异常告警 Tab 看到离线节点/失败任务/待确认任务/排队超时，逐条处置。
- 作为 OPC 用户，我希望在交付物聚合 Tab 浏览并下载最近任务的产物文件。
- 作为 OPC 用户，我希望按关键词搜索任务并分页浏览。
- 作为 OPC 用户，plan 任务方案产出后进入"待确认"状态，我批准/驳回后任务继续流转。

## 3. 核心流程

```
仪表盘加载
    ↓
并行请求 GET /dashboard/metrics、/dashboard/alerts、/dashboard/deliverables、GET /tasks
    ↓
渲染 8 统计卡 + 三 Tab + 节点集群
    ↓
30s 轮询（metrics/alerts/deliverables）；任务 Tab 防抖搜索走 GET /tasks?keyword=
```

```
review 状态流转
plan 任务 Agent 上报 completed+plan_content
    ↓
中枢置 status=review（原为 completed）
    ↓
告警 Tab 出现 warning 项「任务等待人工确认」/ 任务列表显示橙色「待确认」Tag
    ↓
批准 → build 重新入队；驳回 → plan 重新入队（既有审批闭环不变）
```

## 4. 降级链路

| 异常场景 | 降级策略 |
|---------|---------|
| metrics/alerts/deliverables 任一失败 | 对应卡片/Tab 显示错误占位，其余区块不受影响 |
| 无告警 | 告警 Tab 空态，角标隐藏 |
| 无交付物缓存 | 交付物 Tab 空态引导 |
| 节点无 metrics 快照 | 集群负载显示 `—` |
| token 无当日记录 | 显示 0 |

## 5. 验收标准

- [ ] 8 个统计卡数值与后端 `/dashboard/metrics` 一致。
- [ ] 告警 Tab 角标数 = alerts 总数；danger/warning/info 三态背景色区分。
- [ ] 交付物 Tab 可下载（复用 `/tasks/{id}/artifacts/download`）与查看。
- [ ] 任务 Tab 搜索 300ms 防抖后走后端 keyword 查询；分页正确。
- [ ] plan 任务完成后状态为 `review`，前端显示橙色"待确认"，审批闭环可用。
- [ ] 全部图标为 @ant-design/icons，无 emoji。
- [ ] `alembic upgrade head` 成功；后端 pytest 通过；前端 tsc/eslint/vitest 通过。

## 6. 新增/修改后端接口

### 6.1 新增：GET /api/v1/dashboard/metrics

```json
data: {
  "total_tasks": 128, "running_tasks": 7, "completed_tasks": 116,
  "online_nodes": 24, "total_nodes": 26,
  "cluster_load_percent": 67.5,
  "alert_count": 3, "token_consumed_today": 1200000, "queued_tasks": 18
}
```

### 6.2 新增：GET /api/v1/dashboard/alerts?limit=50

```json
data: {
  "total": 3,
  "items": [{
    "id": "uuid 或 node:xxx / task:xxx 合成键",
    "level": "danger|warning|info",
    "title": "video-node-02 离线",
    "description": "节点状态 offline · 最后心跳 10:38",
    "task_id": null, "node_id": "uuid", "created_at": "..."
  }]
}
```

判定规则：danger=节点 offline；warning=failed 任务、review 任务；
info=created_at 早于 10 分钟前且仍 pending 的任务。
已读水位线：`created_at` 早于等于 `system_settings.alerts_read_at` 的告警不返回。

### 6.3 新增：POST /api/v1/dashboard/alerts/read-all

一键已读：将 `system_settings.alerts_read_at` 推进到当前时刻。

```json
data: { "read_at": "2026-09-17T13:44:35Z" }
```

### 6.4 新增：GET /api/v1/dashboard/deliverables?limit=20

```json
data: {
  "total": 4,
  "items": [{
    "task_id": "uuid", "task_title": "七夕活动主视觉设计",
    "node_name": "design-node-01", "path": "assets/kv.png",
    "file_name": "kv.png", "file_type": "image",
    "size": 102400, "mtime": 1730000000.0
  }]
}
```

file_type 按扩展名映射：image/doc/video/zip/code/other。

### 6.5 修改：GET /api/v1/tasks

新增可选 `keyword` 参数（ILIKE 匹配 title，与 status/agent_type 叠加）；
响应 TaskResponse 新增 `priority`。

### 6.6 状态语义变更

`TaskStatus` 增加 `review`；plan 模式任务上报 completed 且带 plan_content
时落库为 `review`；approve-plan/reject-plan 接受 `completed` 与 `review`。

## 7. 关联文档

- [ADR-038](../adr/038-dashboard-v2-cluster-metrics-alerts-deliverables.md)
- [038-dashboard-v2-harness.md](./038-dashboard-v2-harness.md)
- [038-dashboard-v2-changes.md](./038-dashboard-v2-changes.md)
- [007-web-console-dashboard-spec.md](./007-web-console-dashboard-spec.md)

## 8. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-17 | 初始创建 | AI助手 |
