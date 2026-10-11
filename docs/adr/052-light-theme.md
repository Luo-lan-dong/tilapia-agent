# ADR-052：前端亮色主题与颜色收敛（14 语义 CSS 变量双态）

- **状态**：Accepted（2026-09-27 用户确认方案并实施）
- **日期**：2026-09-27
- **关联**：[052-light-theme-color-plan.md](../spec/052-light-theme-color-plan.md)（颜色收敛清单与迁移方案，本 ADR 的前置调研）、frontend-component-standards（防回潮规则将追加至此）

## 1. 问题背景

前端（opc-web-console）自立项起仅支持暗色主题，颜色以字面量形式散落在 125 处（TaskNew.css 77、AppLayout.css 21、WindowControls.css 5、nodeStatus.ts 5、NodeCluster.tsx 3 等，扫描工具 `opc-web-console/scripts/scan_colors_20260927.py` 可重跑）。导致：

1. **无法支持亮色主题**：用户需要亮色模式，但所有色值硬编码，任何整体换肤都要全量改文件；
2. **卡片"花色"风险**：新增组件随手写色值字面量，同一语义（边框/次级文本）出现多种近似色，视觉不一致且难维护；
3. **回归不可控**：无防回潮机制，颜色收敛后还会再次发散。

## 2. 决策（Decision）

### 2.1 语义变量双态（核心）

建立 **14 个语义 CSS 变量**（`:root` 定义暗色默认值，`html[data-theme="light"]` 整体覆盖亮色值），组件一律 `var(--*)` 引用：

`--bg-base` `--bg-container` `--bg-elevated` `--border` `--border-strong` `--text-primary` `--text-secondary` `--primary` `--primary-soft` `--error` `--error-soft` `--status-running` `--status-warn` `--status-pending`

- 暗色值 = 现硬编码值（如 `#1a1b1d`/`#222427`/`#d1d3db`/`#9599a6`/`#32f08c`），迁移后视觉零变化；
- 亮色建议：bg `#f5f6fa` / container `#ffffff`，文字深灰系，主色保留品牌绿（可微调）。

### 2.2 antd 侧切主题

`App.tsx` ConfigProvider 按主题态切换 `theme.darkAlgorithm` / `theme.defaultAlgorithm`，token 值从 `tokens.ts` 的双态常量取（dark=现值，light=新值）。主题态存 localStorage（键 `opc:theme`），顶栏加切换按钮。

### 2.3 状态色 className 化

`nodeStatus.ts` / `NodeCluster.tsx` 的状态色字面量改为 className 方案（状态色由 CSS 变量承载），tsx 内联色收敛为 `var(--*)` 或 className。

### 2.4 保留项

- WindowControls 关闭红 `#e81123`：Windows 惯例，双主题保留；
- `tokens.ts` / `index.css`（变量定义文件）是唯一允许色值字面量的地方。

### 2.5 防回潮硬规则（写入 frontend-component-standards）

1. 组件禁止新增颜色字面量：CI 脚本检查 tsx/css 中出现 `#[0-9a-fA-F]{3,8}` / `rgba(` 即失败（tokens.ts、index.css、变量定义文件、保留项白名单除外）；
2. antd 组件色一律走 ConfigProvider token，禁止 `.ant-*` 全局覆盖（既有规范重申）；
3. 语义色只允许从 14 个变量取，新增语义需先加变量。

## 3. 备选方案（Considered Options）

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| CSS 变量双态 + ConfigProvider 切算法（本方案） | ✅ 采纳 | 暗色=现值零视觉变化；CSS 变量承载 tsx 内联/className 场景；antd 侧只需换 algorithm |
| antd 全托管（全部走 token，不用 CSS 变量） | ❌ 否决 | tsx 内联样式与 className 无法读 antd token；TaskNew.css 等 100+ 处自定义样式无法收敛 |
| CSS-in-JS / styled-components | ❌ 否决 | 引入新依赖与运行时成本，与现有纯 CSS 方案冲突 |
| 只做亮色不做收敛（按需覆盖） | ❌ 否决 | 125 处字面量不收敛则亮色覆盖会漏，且"花卡"问题不解决 |

## 4. 影响分析

- **前端**（全部改动均在 opc-web-console）：tokens.ts 双态、index.css 变量定义、App.tsx 主题态、AppLayout 顶栏开关、TaskNew.css/AppLayout.css/WindowControls.css 机械替换、nodeStatus.ts/NodeCluster.tsx className 化、散点 tsx 内联收敛；
- **无后端改动**，无数据库迁移，无协议变更；
- **迁移四阶段**（每阶段可构建可回滚）：P0 基建 → P1 高频页面（98/125=78% 收敛）→ P2 语义色与散点 → P3 亮色定稿+双主题走查；
- **回滚**：CSS 变量双态并存，主题开关切回 dark 即回退视觉，不存在半迁移态。

## 5. 实际实施情况（2026-09-27 实施完成）

四阶段全部完成，构建/类型检查通过，浏览器双主题走查通过（仪表盘/节点管理/新建任务）：

- **P0 基建**：tokens.ts 双态（`DARK_COLORS`/`LIGHT_COLORS`/`COLOR_SETS`）、theme.ts + ThemeContext/ThemeProvider/useTheme、index.css 变量定义（`:root` 暗色默认 + `html[data-theme='light']` 覆盖）、App.tsx 拆 `ThemedApp` 切 algorithm、AppLayout 顶栏切换按钮、index.html 防闪烁内联脚本。
- **P1**：TaskNew.css 77 处 + AppLayout.css 21 处全部收敛为 `var(--*)`。
- **P2**：nodeStatus.ts 状态色 → `var(--status-*)`；NodeStatusTag 改 className 方案（发现原 `${color}1A` 十六进制拼接与 var() 不兼容，淡背景/边框用 `color-mix()` 派生）；NodeCluster/TaskSummary/PlanReviewPanel/WindowControls/NodeMetrics 及 5 处 Alert 收敛。
- **实施中的关键补充决策（偏离初版方案，效果更优）**：初版计划逐一改写 274 处 `COLORS.*` 引用（字面量扫描抓不到的暗色常量引用，是首轮亮色走查"半亮半暗"的根因）。实际改为**将 `COLORS` 兼容层整体改为 var() 映射**（`COLORS.primary = 'var(--primary)'` 等），274 处引用零改动即双主题化；`COLOR_SETS`（真实色值）仅供 ConfigProvider token 使用。新增 `--on-primary`/`--info`/`--primary-border`/`--alert-warning-soft`/`--alert-info-soft` 变量支撑。
- **防回潮**：`opc-web-console/scripts/check_colors.py` 门禁（白名单 3 文件，已做拦截反向验证）+ `.github/workflows/color-literal-check.yml` + package.json `check:colors` + frontend-component-standards 新增 3.1 节三规则。
- **验证**：tsc/build 通过；扫描 125→0（白名单外）；暗色视觉与迁移前一致；亮色三页面对比度良好；主题刷新持久化（localStorage `opc:theme`）与防闪烁生效。
- **已知存量（与本次无关）**：5 个 TaskNew 对话流测试失败（组件 9.25/9.27 已改、测试断言未同步）、2 个 eslint 存量 error（usePolling refs / Logs set-state-in-effect）。
