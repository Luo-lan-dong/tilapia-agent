# 亮色主题颜色收敛清单与迁移方案（ADR-052 前置）

> 扫描时间：2026-09-27 · 工具：`opc-web-console/scripts/scan_colors_20260927.py`（可重跑）
> 全量硬编码颜色 **125 处**，集中度很高：前三个文件占 **103 处（82%）**。

## 一、收敛清单（按文件）

| 文件 | 处数 | 主要色值 | 迁移目标变量 |
| --- | --- | --- | --- |
| [TaskNew.css](../../opc-web-console/src/pages/TaskNew.css) | **77** | `#9599a6`×11（次级文本）、`#32f08c`×8（主色）、`#d1d3db`×8（正文）、`rgba(224,226,242,.1)`×8（边框）、`#222427`×7（容器底） | `--text-secondary` / `--primary` / `--text-primary` / `--border` / `--bg-container` |
| [AppLayout.css](../../opc-web-console/src/components/AppLayout.css) | **21** | 同上一套语义（侧栏/顶栏/菜单） | 同上 + `--bg-base` |
| WindowControls.css | 5 | 桌面窗口控制钮（含 `#e81123` 关闭红，平台惯例保留） | `--text-primary` / `--border`；`#e81123` 保留 |
| utils/nodeStatus.ts | 5 | 节点状态色（绿 `#33c192`/橙 `#d27e24`/黄 `#d8a534`/灰 `#666b75`/红 `#f65a5a`） | `--status-*` 语义色 |
| components/NodeCluster.tsx | 3 | 同状态色 | 同上 |
| App.css / index.css | 4 | 全局 body 文本/滚动条 | `--bg-base` / `--text-primary` |
| TaskSummary / NodeMetrics / PlanReviewPanel 等 tsx 内联 | 10 | 主色/状态色/错误红 `rgba(246,90,90,.12)` | `--primary` / `--status-*` / `--error-soft` |

**语义变量规划**（写入 `:root`，暗色为默认值，亮色主题整体覆盖）：
`--bg-base` `--bg-container` `--bg-elevated` `--border` `--border-strong` `--text-primary` `--text-secondary` `--primary` `--primary-soft` `--error` `--error-soft` `--status-running/warn/pending/idle` 共 14 个。

## 二、迁移方案（四阶段，每阶段可构建可回滚）

**P0 基建（半天）**：tokens.ts 增加两组 token（dark 现值 / light 新值）；`index.css` 把 14 个语义变量定义为 CSS 变量（默认=暗色值）；App.tsx 的 ConfigProvider 按主题态切换 `darkAlgorithm`/`defaultAlgorithm` + 对应 token；设置页或顶栏加主题开关（存 localStorage）。

**P1 高频页面（1 天）**：`TaskNew.css`（77 处）与 `AppLayout.css`（21 处）的字面量全部替换为 `var(--*)`——机械替换 + 浏览器逐页过。替换规则：`#1a1b1d→--bg-base`、`#222427→--bg-container`、`#d1d3db→--text-primary`、`#9599a6→--text-secondary`、`#32f08c→--primary`、`rgba(224,226,242,.1)→--border`、`rgba(50,240,140,.08)→--primary-soft`。

**P2 语义色与散点（半天）**：nodeStatus.ts/NodeCluster/各 tsx 内联改为 CSS 变量引用（ts 里用 `getComputedStyle` 读取或改为 className 方案——**推荐 className**，状态色由 CSS 变量承载）。

**P3 亮色值定稿与全页面走查（1 天）**：亮色 tokens 定稿（背景 #f5f6fa / 容器 #ffffff / 主色可保留 #10b981 系或按品牌调整）；每个路由亮/暗双态浏览器走查，修对比度问题（重点：Tag/状态点/代码块/图表）。

**防"花卡"的硬规则**（写入 frontend-component-standards）：
1. 组件禁止新增颜色字面量（CI 加 grep 检查 `#[0-9a-f]{3,6}` 在 tsx/css 中出现即失败，tokens.ts/index.css 除外）；
2. antd 组件色一律走 ConfigProvider token，禁止 `.ant-*` 覆盖（既有规范重申）；
3. 语义色只允许从 14 个变量取，新增语义需先加变量。

## 三、风险与边界

- **图表/ECharts 类**（若有）：需要单独的亮色轴/网格配色（本次扫描未发现图表组件，若 P3 发现再补）。
- **WindowControls 关闭红 `#e81123`**：Windows 惯例，双主题保留不迁移。
- **图表/代码高亮**：TaskNewDocPanel 的 Markdown 代码块底色需在 P3 走查确认。
- **回滚**：每阶段独立构建产物，主题开关切回 dark 即回退视觉（CSS 变量双态并存，不存在半迁移态）。

## 四、实施建议

P0+P1 一个批次（收益最大：98/125=78% 处收敛）；P2+P3 第二批次。总工作量约 3 个工作日等价。实施时按规则立 ADR-052 + Harness（含双主题浏览器走查清单）。
