# ADR-011：前端测试框架选型

## 状态

Accepted

## 日期

2026-08-07

## 问题背景

OPC 前端管理控制台（`opc-web-console`）在 ADR-007 阶段使用 Vite + React + TypeScript + Ant Design 搭建，但一直未引入前端测试框架。随着任务创建 Spec Chat 模块（ADR-010）复杂度提升，以及后续节点管理、LLM 配置等模块迭代，缺少自动化测试带来以下问题：

1. **回归成本高**：手动浏览器测试无法覆盖所有交互路径，每次后端 API 或状态管理调整都需重新手动验证。
2. **组件重构风险大**：`TaskNew.tsx`、`TaskQueue.tsx` 等页面状态复杂，缺少单元测试时重构容易引入 UI 回归。
3. **API 对接契约缺失**：前后端并行开发时，前端无法快速验证 API 请求/响应处理是否符合预期。
4. **CI 门禁不完整**：后端已有 `pytest`、`ruff`、`check_file_size` 门禁，前端仅有 `typecheck` 和 `lint`，缺少测试门禁。

因此需要为 `opc-web-console` 引入一套完整的前端测试方案。

## 决策

1. **单元/集成测试框架选用 Vitest**：与 Vite 生态深度集成，配置简单，支持 ES Modules 和 TypeScript，无需额外转译；API 与 Jest 兼容，学习成本低。
2. **组件测试选用 React Testing Library (RTL)**：遵循"测试行为而非实现"原则，优先从用户视角验证组件交互；配合 `jsdom` 运行，无需真实浏览器即可覆盖大部分 UI 逻辑。
3. **E2E 测试选用 Playwright**：支持多浏览器（Chromium/Firefox/WebKit），提供可靠的等待机制和截图/录像能力，适合验证任务创建 → 三件套生成 → 派发的完整用户流。
4. **Mock HTTP 层使用 MSW (Mock Service Worker)**：前端 API 测试中统一拦截 `axios` 请求，避免直接 mock `axios` 实例，测试更接近真实运行时行为。
5. **测试脚本统一纳入 package.json**：新增 `test`（运行 vitest）、`test:ui`（Vitest UI）、`test:e2e`（Playwright）、`test:e2e:ci`（Playwright CI 无头模式）。
6. **CI 门禁扩展**：前端 CI 增加 `npm run test` 和 `npm run test:e2e:ci`，与后端 `pytest` 并列；E2E 测试依赖后端服务启动，CI 中采用 `concurrently` 或 GitHub Actions `services` 同时启动前后端。
7. **测试目录结构**：`src/__tests__/` 放组件/Hook 测试，`src/api/__tests__/` 放 API 层测试，`e2e/` 放 Playwright 测试；与源码目录保持镜像，便于定位。

## 备选方案

### 方案 A：Vitest + RTL + Playwright（已采纳）

- **优点**：与 Vite 生态无缝集成；Vitest 启动速度和 HMR 体验好；RTL 测试理念与 React 官方推荐一致；Playwright 比 Cypress 更适合多浏览器和 CI 稳定性。
- **缺点**：需要新增 3 套依赖和配置，初期有一定学习成本；Playwright 需要下载浏览器二进制，CI 构建时间增加。
- **采纳原因**：Vite 官方推荐 Vitest；项目已有 Vite 构建，迁移成本最低；Playwright 在 OPC 后续 E2E 场景（Agent 节点安装、WebSocket 通信）中扩展性更好。

### 方案 B：Jest + RTL + Cypress

- **优点**：Jest 生态成熟，团队熟悉度高；Cypress 实时重载调试体验好。
- **缺点**：Jest 与 Vite 需要额外配置 `vite-jest` 或 `babel-jest` 转译；Cypress 仅支持 Chromium/Firefox，不支持 WebKit，且对现代 React Router v7 的测试支持不如 Playwright。
- **否决原因**：与现有 Vite  toolchain 不原生兼容，增加配置复杂度；Cypress 在跨浏览器和 CI 稳定性上不如 Playwright。

### 方案 C：仅引入 Vitest 单元测试，E2E 继续手动

- **优点**：工作量最小，短期内提升组件测试覆盖。
- **缺点**：无法覆盖前后端联调场景；ADR-010 的完整用户流（对话 → 创建任务 → 派发）仍依赖手动验证，回归成本高。
- **否决原因**：E2E 自动化是 OPC 后续快速迭代的基础，MVP 阶段就应建立最小 E2E 覆盖（至少 1-2 个核心场景）。

## 影响分析

1. **依赖影响**：`opc-web-console/package.json` 新增 `vitest`、`@testing-library/react`、`@testing-library/jest-dom`、`@testing-library/user-event`、`jsdom`、`msw`、`@playwright/test` 等 devDependencies。
2. **配置影响**：新增 `vitest.config.ts`（复用 Vite 配置并指定 `environment: 'jsdom'`）、`playwright.config.ts`（指定 baseURL、projects、reporter）、`src/test/setup.ts`（RTL 全局配置和 MSW 初始化）。
3. **目录影响**：新增 `src/__tests__/pages/TaskNew.test.tsx`、`src/__tests__/api/taskBuilding.test.ts`、 `e2e/task-creation.spec.ts` 等。
4. **开发流程影响**：本地开发可通过 `npm run test` 快速验证；提交前需确保新增/修改的组件有对应测试；E2E 测试在 PR 合并前运行。
5. **CI 影响**：前端 CI 步骤从 `typecheck + lint` 扩展为 `install deps → install Playwright browsers → typecheck → lint → unit tests → build → e2e tests`。
6. **性能影响**：本地单元测试秒级完成；Playwright E2E 首次下载浏览器约 30-60 秒，CI 缓存后显著降低。
7. **向后兼容**：仅新增 devDependencies 和测试文件，不影响生产构建产物。

## 关联文档

- [010-task-creation-spec-chat-harness.md](../spec/010-task-creation-spec-chat-harness.md)
- [007-web-console-dashboard.md](./007-web-console-dashboard.md)
- [frontend-component-standards.md](../standards/frontend-component-standards.md)
