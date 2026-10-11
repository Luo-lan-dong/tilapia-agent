# 集群仪表盘 v2 Harness

## 1. 测试金字塔

```
        E2E（浏览器联调）
       /                \
    集成（API 端点测试）
   /                        \
单元（服务层 + 状态流转 + 用量埋点）
```

## 2. 单元/集成测试清单（中枢 pytest）

| 编号 | 层级 | 场景 | 断言 |
|------|------|------|------|
| UT-1 | 单元 | review 流转：plan 任务上报 completed+plan_content | status=review、progress=100 |
| UT-2 | 单元 | build 任务上报 completed | status=completed（不变） |
| UT-3 | 单元 | 上报 completed 无 plan_content 的 plan 任务 | status=completed |
| UT-4 | 集成 | approve-plan 对 review 任务 | 200，execution_mode=build、status=pending |
| UT-5 | 集成 | reject-plan 对 review 任务 | 200，status=pending 且 mode=plan |
| UT-6 | 集成 | 存量 completed plan 任务 approve-plan（向后兼容） | 200 |
| UT-7 | 集成 | GET /tasks?keyword=xxx | 仅返回 title 命中任务；与其他筛选可叠加 |
| UT-8 | 集成 | GET /dashboard/metrics | 8 字段齐全；CPU 均值/排队数正确；无快照时 cluster_load=null |
| UT-9 | 集成 | GET /dashboard/alerts | 离线节点=danger、failed/review=warning、排队>10min pending=info |
| UT-10 | 集成 | GET /dashboard/deliverables | 只含缓存产物；file_type 映射正确；按 mtime 倒序 |
| UT-11 | 单元 | DeepSeekClient 非流式 usage 落库 | llm_usage_records 新增 1 行，total_tokens 正确 |
| UT-12 | 单元 | usage 缺失/写库失败不影响主调用 | 调用正常返回 |
| UT-13 | 迁移 | alembic upgrade head | priority 列默认 P2；status CHECK 接受 review |

## 3. E2E 测试场景

1. 打开仪表盘 → 8 卡片渲染真实数据；任务 Tab 搜索"测试"命中后端过滤结果；
   切换告警/交付物 Tab 正常，搜索框随 Tab 隐藏。
2. 创建 plan 任务并派发 → Agent 完成上报后任务显示"待确认"；
   告警 Tab 出现 warning 项；批准后任务转 build 重新入队。

## 4. CI 门禁

- 中枢：`pytest` 全量通过（含本模块新增用例）。
- 前端：`tsc --noEmit`、`eslint`、`vitest run` 通过。
- ruff format/check 通过；文件 ≤200 行（豁免需注释）。
