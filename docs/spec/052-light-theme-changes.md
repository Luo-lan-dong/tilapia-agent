# 亮色主题 Changes 变更清单（ADR-052）

> 关联：[ADR-052](../adr/052-light-theme.md) · [Spec](./052-light-theme-spec.md) · [Harness](./052-light-theme-harness.md)
> 边开发边实时登记，编码前仅骨架。

## 变更清单

| 文件 | 类型 | 函数/类级明细 | 作用说明 |
| --- | --- | --- | --- |
| opc-web-console/src/theme/tokens.ts | 修 | `COLORS` 常量 → `PaletteColors` 接口 + `DARK_COLORS`/`LIGHT_COLORS`/`COLOR_SETS` 双态（`COLORS` 保留为暗色别名待 P2 移除） | 色板单一事实源双态化（ADR-052 P0） |
| opc-web-console/src/theme/theme.ts | 增 | `ThemeName`/`THEME_STORAGE_KEY`/`getInitialTheme()`/`applyTheme()` | 主题态读取与 html[data-theme] 应用 |
| opc-web-console/src/theme/ThemeProvider.tsx | 增 | `ThemeProvider`/`useTheme()` | 主题 Context：状态持有 + localStorage 持久化 + data-theme 同步 |
| opc-web-console/index.html | 修 | `<head>` 前置内联脚本 | 渲染前按 localStorage 设 data-theme，防启动主题闪烁 |
| opc-web-console/src/index.css | 修 | 新增 `:root` 14+5 语义 CSS 变量（暗色默认）与 `html[data-theme='light']` 整体覆盖；滚动条色变量化 | CSS 变量双态定义（与 tokens.ts 一致） |
| opc-web-console/src/App.css | 修 | `body` 背景/文字 → `var(--bg-base)`/`var(--text-primary)` | 全局底色接入变量 |
| opc-web-console/src/App.tsx | 修 | 重构为 `App`（ThemeProvider 包裹）+ `ThemedApp`（useTheme 驱动 ConfigProvider）；`components.Menu` darkItem* → 通用 item* token；token 取值改 `COLOR_SETS[theme]` | antd 侧按主题态切 darkAlgorithm/defaultAlgorithm |
| opc-web-console/src/components/AppLayout.tsx | 修 | import SunOutlined/MoonOutlined + useTheme；`AppLayout` 内 `Menu theme="dark"` → `theme={theme}`；header-actions 新增主题切换 Button | 顶栏亮/暗切换入口 + 菜单双态 |
| opc-web-console/src/components/AppLayout.css | 修 | 全部 21 处颜色字面量 → `var(--*)`（bg-base/bg-container/border/text-primary/text-secondary/primary/text-muted/status-running/primary-soft/surface） | 布局样式接入主题变量（ADR-052 P1） |
| opc-web-console/src/pages/TaskNew.css | 修 | 全部 77 处颜色字面量 → `var(--*)`（含 --border-strong 归并 0.12/0.14/0.16、--primary 归并主色边框 0.4/0.45/0.6、--shadow-overlay/--overlay-mask/--code-bg/--surface/--primary-bg） | 高频页面样式接入主题变量（ADR-052 P1） |
| opc-web-console/src/index.css | 修 | 补 `--primary-bg`/`--shadow-overlay`/`--overlay-mask`/`--code-block-bg`/`--code-block-text` 五个辅助变量（暗/亮两态） | P1/P2 替换所需语义补充 |
| opc-web-console/src/utils/nodeStatus.ts | 修 | `STATUS_COLOR_MAP` 值 `#33C192` 等 5 处 → `var(--status-running)` 等 | 状态色改 CSS 变量承载（ADR-052 P2） |
| opc-web-console/src/components/NodeStatusTag.tsx | 修 | 移除 `color` prop 与 `${color}1A/33` 十六进制拼接 style，改 `className={node-status-tag--${status}}` | var() 值不可拼接透明度，改 className 方案（ADR-052 P2） |
| opc-web-console/src/components/NodeStatusTag.css | 增 | 五状态规则：色取 `var(--status-*)`，淡背景/边框用 `color-mix(in srgb, var 10%/20%, transparent)` 派生 | 状态 Tag 双主题配色 |
| opc-web-console/src/components/NodeCluster.tsx | 修 | 删本地 STATUS_COLOR_MAP 拷贝（收敛 import utils）；`loadBarColor` 返回 var(--primary)/var(--status-warn)/var(--error)/var(--text-muted)；卡片/图标/状态点/文字/进度条全部 COLORS.* → `var(--*)`；删 COLORS import | 集群卡颜色双主题化（ADR-052 P2） |
| opc-web-console/src/components/WindowControls.css | 修 | `#d1d3db`/`#e2e4f2` → `var(--text-primary)`、hover 背景 → `var(--border)`；`#e81123`+`#fff` 保留（Windows 关闭钮惯例白名单） | 窗口控制钮双主题（ADR-052 P2） |
| opc-web-console/src/components/TaskSummary.tsx | 修 | 6 处 COLORS.*/#666b75/#32F08C → `var(--*)`；删 COLORS import | 摘要面板双主题（ADR-052 P2） |
| opc-web-console/src/components/PlanReviewPanel.tsx | 修 | CONTENT_STYLE `COLORS.onPrimary`/`#E8E9ED` → `var(--code-block-bg)`/`var(--code-block-text)`；删 COLORS import（保留 primaryButtonStyle） | 方案全文代码块双主题（ADR-052 P2） |
| opc-web-console/src/pages/NodeMetrics.tsx | 修 | 2 处 `strokeColor="#32F08C"` → `"var(--primary)"` | 监控页进度条双主题（ADR-052 P2） |
| opc-web-console/src/pages/TaskQueue.tsx / Settings.tsx / ResourceManage.tsx / Nodes.tsx / components/InstallCommandModal.tsx | 修 | 各 1 处 Alert `background: rgba(246,90,90,0.12)` → `var(--error-soft)` | 错误告警条双主题（ADR-052 P2） |
| opc-web-console/src/theme/ThemeProvider.tsx | 修 | 拆分：Context 定义移 `ThemeContext.ts`、`useTheme` 移 `useTheme.ts`，Provider 仅导出组件 | 消除 react-refresh/only-export-components 警告 |
| opc-web-console/src/components/AppLayout.tsx / src/App.tsx | 修 | useTheme import 路径随拆分调整 | 同上 |
| opc-web-console/src/index.css | 修 | 补 `--on-primary`/`--info`/`--primary-border`/`--alert-warning-soft`/`--alert-info-soft`（暗/亮两态） | COLORS 兼容层 var 化所需变量 |
| opc-web-console/src/theme/tokens.ts | 修 | `COLORS` 别名由 DARK_COLORS 引用改为 var() 映射表（21 键全量）；`primaryButtonStyle` 改 var()；`COLOR_SETS`（真实色值）仅供 ConfigProvider | **关键补充决策**：274 处存量 COLORS.* 引用零改动双主题化（首轮亮色走查"半亮半暗"根因即 COLORS 暗色常量不随主题切换） |
| opc-web-console/scripts/check_colors.py | 增 | 颜色字面量防回潮门禁（白名单 3 文件；WindowControls 仅放行 `#e81123`/`#fff`），exit 1 + 违例行定位 | 防回潮强制（已做拦截反向验证） |
| .github/workflows/color-literal-check.yml | 增 | CI workflow：push/PR 跑 check_colors.py | CI 门禁 |
| opc-web-console/package.json | 修 | scripts 新增 `check:colors` | 本地门禁入口 |
| docs/standards/frontend-component-standards.md | 修 | 新增 3.1「主题与颜色管理」三规则（禁止字面量/语义色只从变量取/antd 色走 token + 禁止 var() 拼接透明度） | 规范固化 |
| opc-web-console/src/App.tsx | 修 | components.Menu 补 `darkItem*` 六项双态 token（theme="dark" 时 antd 用 darkItem* 默认深藏青 #001529 覆盖通用 item*，导致暗色侧栏菜单变藏青） | 修复暗色侧边栏颜色变化 |
| opc-web-console/public/app-logo-light.svg | 增 | 亮色主题 logo（白底圆角方块 + 深绿 T，源 ico/tilapia-icon-light.svg） | 亮色侧栏品牌图标适配 |
| opc-web-console/src/components/AppLayout.tsx | 修 | Sider 与 Drawer 两处 logo `src` 按主题态切换（dark→/app-logo.svg、light→/app-logo-light.svg） | 亮色下不再显示暗色图标 |
