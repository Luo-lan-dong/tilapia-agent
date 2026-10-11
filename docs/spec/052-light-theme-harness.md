# 亮色主题 Harness（ADR-052）

## 1. 测试金字塔

本决策以**静态扫描 + 构建 + 浏览器走查**为主（纯前端视觉改造，无后端逻辑）：

| 层级 | 工具 | 说明 |
| --- | --- | --- |
| 静态扫描 | `opc-web-console/scripts/scan_colors_20260927.py`（复用/增强为防回潮 CI 检查） | tsx/css 颜色字面量白名单外清零 |
| 构建 | `tsc` + `eslint` + `vite build` | 类型与规范门禁 |
| 既有单测 | `npx vitest run` | 存量 31+ 项不回归（nodeStatus 改造涉及相关测试需同步更新） |
| 浏览器走查 | 人工双主题 | 见 §2 E2E 清单 |

## 2. E2E 走查清单（暗/亮 × 每路由）

| 场景 | 检查点 |
| --- | --- |
| 主题切换 | 顶栏按钮切换即时生效；刷新保持；无白闪 |
| 仪表盘 | 统计卡/三 Tab 左栏/集群卡/IP 行在亮色下对比度 |
| 节点管理 | 表格、状态 Tag（running/warn/pending/idle）、IP 列 |
| 节点监控详情 | 负载三态色、图表区、指标卡 |
| 新建任务（TaskNew） | 对话区/输入底栏/右侧文档面板/消息气泡/代码块 |
| 任务队列 | 表格、状态 Tag、派发列 |
| 设置/文档/日志/记忆 | 表单、Tabs、代码/日志块 |
| 窗口控制钮（桌面壳） | 关闭红 `#e81123` 双主题保持 |

## 3. CI 门禁（防回潮）

- 扫描脚本加入检查：`src/**/*.{ts,tsx,css}` 中出现 `#[0-9a-fA-F]{3,8}` 或 `rgba(` 即失败；
- 白名单：`src/theme/tokens.ts`、`src/index.css`（变量定义区）、`src/components/WindowControls.css`（`#e81123` 例外）；
- 接入方式：package.json script（如 `npm run check-colors`），CI 中执行。

## 4. 回归范围

- nodeStatus.ts 状态色改 className 后：相关单测（若断言了颜色值）同步更新为断言 className；
- 既有前端测试全量跑通。
