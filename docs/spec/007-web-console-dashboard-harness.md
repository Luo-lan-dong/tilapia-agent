# 前端管理控制台仪表盘模块 Harness

## 1. 测试目标

验证 `opc-web-console` 前端仪表盘满足 [007-web-console-dashboard-spec.md](./007-web-console-dashboard-spec.md) 中的功能与质量要求。

## 2. 测试金字塔

| 层级 | 范围 | 工具 |
|---|---|---|
| 单元测试 | 工具函数、Hooks、纯展示组件 | Vitest + React Testing Library |
| 集成测试 | API 封装、数据转换、带状态的组件 | Vitest + MSW |
| E2E 测试 | 完整用户操作链路 | Playwright |

## 3. 单元/集成测试清单

- [ ] `request.ts` 封装 axios/fetch，正确处理统一响应格式 `{code, message, data, request_id}`。
- [ ] `useTaskList` Hook 能轮询任务列表并暴露 loading/error/data 状态。
- [ ] `useAgentNodeList` Hook 能轮询节点列表并暴露 loading/error/data 状态。
- [ ] `StatCard` 组件正确展示标题、数值、趋势文案。
- [ ] `TaskTable` 组件正确渲染任务行，状态标签颜色与状态对应。
- [ ] `NodeCluster` 组件正确渲染节点卡片，状态颜色与状态对应。
- [ ] `CreateTaskModal` 表单校验：空需求时禁止提交。
- [ ] 创建任务成功后，任务列表自动刷新。

## 4. E2E 测试清单

- [ ] 用户打开首页，能看到 KPI 卡片、任务列表、节点列表。
- [ ] 用户点击「新建任务」，输入需求，提交后任务出现在列表中且状态为 `pending`。
- [ ] 启动 Mock Agent 后，任务状态最终变为 `completed`，进度变为 100。

## 5. CI 门禁

- [ ] `npm run typecheck` 通过。
- [ ] `npm run lint` 通过。
- [ ] `npm run test:unit` 通过。
- [ ] `scripts/check_file_size.py` 扫描前端目录无超长文件。

## 6. 关联文档

- [007-web-console-dashboard-spec.md](./007-web-console-dashboard-spec.md)
- [ADR-007 前端管理控制台仪表盘](../adr/007-web-console-dashboard.md)
