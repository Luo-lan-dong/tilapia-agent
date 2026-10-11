# ADR-001：前端技术栈选型

## 状态

Accepted

## 日期

2026-08-03

## 问题背景

OPC 项目需要一个人机交互入口，用于自然语言需求提交、任务看板、进度查看、交付物管理、高风险操作确认。前端需要支持：

- 复杂后台管理界面（表格、表单、状态看板、实时进度）。
- 与后端 WebSocket 实时通信，展示任务进度和节点状态变化。
- 多人协作下代码可维护、类型安全、组件可复用。

## 决策

采用 **React 18 + TypeScript + Ant Design 5** 作为前端技术栈。

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| React + TypeScript + Ant Design | 企业级后台生态最成熟，组件丰富，TypeScript 类型安全，社区活跃 | 学习曲线较 Vue 略陡 | ✅ 选中 |
| Vue 3 + TypeScript + Element Plus | 上手快，模板语法直观，国内生态好 | 大型项目类型系统和组件生态不如 React 完善 | ❌ 否决 |
| Next.js + TypeScript + Ant Design | SSR/SSG 能力强，SEO 友好 | 本项目是后台系统，SEO 不是刚需，增加部署复杂度 | ❌ 否决 |

## 决策原因

1. **匹配项目类型**：OPC 前端本质是后台管理系统，Ant Design 提供了最完整的后台组件（表格、表单、看板、步骤条、消息通知等）。
2. **类型安全**：TypeScript 能在编译期发现大量接口契约问题，对前后端协作非常重要。
3. **实时通信友好**：React 生态对 WebSocket/SSE 实时数据更新有成熟方案（SWR、React Query、Zustand 等）。
4. **团队可维护性**：React + TypeScript 是企业级项目主流选择，后续招聘和知识传承成本更低。

## 影响

- 前端工程目录命名为 `opc-web-console/`。
- 状态管理优先使用 Zustand 或 React Query，避免过度使用 Redux。
- UI 组件优先使用 Ant Design，自定义组件遵循 `docs/standards/coding-standards.md` 命名规范。
- 构建工具使用 Vite。

## 关联文档

- [项目设计文档](../OPC超级个体%20·%20AI员工智能体协作平台｜项目设计文档（正式版）.md)
- [coding-standards.md](../standards/coding-standards.md)
- [api-standards.md](../standards/api-standards.md)
