# 产物面板 UI 迭代 变更清单（目录树美化 + 可拖动分隔条 + Markdown 渲染）

> 关联：[ADR-028](../adr/028-task-workspace-binding-and-artifact-sync.md)（任务工作目录绑定与产物资源管理器）
> 规则：本次为 **UI 迭代**，沿用 `030-settings-ui-layout-changes.md` / `010-task-creation-spec-chat-ui-layout-changes.md` 先例——
> 不新开 ADR，只登记 Changes 并同步 INDEX。
> 加载的规范：`docs/standards/frontend-component-standards.md`（含 §3.1 主题与颜色防回潮硬规则）。

## 需求来源（用户 2026-09-27 反馈）

1. 「新建对话里的产物目录树看起来好简陋」；
2. 「只有预览区能自由拖动大小，在选择文件那里却不能拖动」；
3. 追加：「预览区把 MD 文档语法渲染一下，可切换，默认渲染后的」；
4. 追加：「中枢模型对话输出的正文（有时带 Markdown 符号）也顺手渲染一下」。

## 关键约束（决定了实现路径）

| 约束 | 事实 | 影响 |
|------|------|------|
| 无法引入依赖 | `registry.npmjs.org` / `npmmirror` / `yarnpkg` **全部不可达**（沙箱网络受限），`node_modules` 内无任何 markdown 包 | Markdown 渲染必须**自建零依赖** |
| 安全 | Agent 产物与模型正文均为**不可信内容** | 自建渲染器把 Markdown 解析为 **React 元素**、绝不用 `dangerouslySetInnerHTML` ⇒ 天然免疫 XSS；链接仅放行 `http/https/mailto` 与相对路径，`javascript:` 按字面量显示 |
| 颜色门禁 | `scripts/check_colors.py` 禁止 `#hex`/`rgb()`/`rgba()`（白名单仅 tokens.ts / index.css / WindowControls.css） | 新增样式全部 `var(--*)` 语义变量，复用既有 `--code-bg`/`--code-block-bg`/`--primary-bg`/`--primary-border`/`--border`/`--text-muted` |

## 变更清单（实际实施明细）

| 文件 | 变更类型 | 函数/类级明细 | 作用说明 |
|------|---------|--------------|---------|
| `src/components/Markdown.tsx` | 增 | `renderMarkdown(text)`、`tokenizeInline`、`renderInline`、`isSafeHref`；常量 `SAFE_LINK_PROTOCOLS`、`INLINE_PATTERN`、`HEADING_PATTERN`、`FENCE_PATTERN`、`UNORDERED_PATTERN`、`ORDERED_PATTERN`、`QUOTE_PATTERN`、`HR_PATTERN` | **零依赖 Markdown → React 元素**渲染器（标题/围栏码块/有序无序列表/引用/分割线/行内粗斜体与行内码/链接）；不产出 HTML 字符串故无 XSS 面 |
| `src/components/Markdown.css` | 增 | `.md-body` / `.md-heading--1..6` / `.md-paragraph` / `.md-list` / `.md-inline-code` / `.md-code-block` / `.md-quote` / `.md-hr` / 链接态 | 渲染样式，全部走语义变量 |
| `src/components/TaskNewChatPanel.tsx` | 修 | 正文渲染分支：`msg.role === 'assistant' ? renderMarkdown(msg.content) : msg.content` | 助手正文渲染 Markdown；**用户消息保持纯文本**（可能是命令/路径原文，不应被改写） |
| `src/pages/TaskNew.css` | 修 | `.task-new__message--assistant .task-new__message-content` 增 `white-space: normal`（关闭 pre-wrap）+ 码块间距微调 | 避免 Markdown 块级元素与 pre-wrap 叠加产生双倍换行 |
| `src/hooks/useResizablePanel.ts` | 修 | 新增 `ResizablePanelOptions`（`min`/`max`/`storageKey`/`direction`）；`readStoredWidth`；`handleResizeStart` 去掉对 `width` 的依赖改用 `widthRef`；effect 内同步 `widthRef`；拖拽结束写 localStorage | 复用为**内部分隔条**：支持自定义范围、宽度持久化、左右两种拖拽方向；缺省参数保持既有调用方行为不变 |
| `src/components/TaskDetailArtifacts.tsx` | 重写 | `buildTreeData(files, keyword)` 返回 `{nodes, keys}` 并渲染「名字+大小+命中高亮」；`iconForFile`（md/图片/压缩包/文本/通用）；`formatSize`；`isMarkdownPath`；`escapeRegExp`；新增状态 `keyword`/`showSource`/`expandedKeys`；`handleCopyPath`/`handleCopyContent`；树工具条（搜索/展开全部/收起全部/打包下载）；**分隔条**（`useResizablePanel`，持久化键 `opc:artifacts-tree-width:<variant>`）；预览区「渲染/源码」切换 + 行号源码视图 + 空态引导 | ①目录树不再「简陋」：类型图标 + 文件大小 + 命中高亮；②**树与预览之间可拖动**（此前写死 45%）；③Markdown 默认渲染、可切源码；④复制路径/内容 |
| `src/components/TaskDetailArtifacts.css` | 增 | `.artifacts__*`（toolbar / browse / tree / splitter / preview / source-line / node / hit） | 面板样式；分隔条用 `::before` 做「细线 + 宽命中区」，hover/拖拽态变色 |
| `src/components/MarkdownView.tsx` | 增 | `MarkdownView`（渲染/源码切换 + 复制 + 空态 + `resetKey` 重置）；模块私有 `renderSourceWithLineNumbers` | **可复用 Markdown 展示视图**：统一产物预览、三件套文档、方案全文三处的呈现；默认渲染、可切带行号源码 |
| `src/components/MarkdownView.css` | 增 | `.mdv` / `.mdv-head` / `.mdv-actions` / `.mdv-source(-line/-no/-text)` / `.mdv-empty` | 展示视图样式（语义变量） |
| `src/components/TaskNewDocPanel.tsx` | 修 | 新增 `DocViewMode`（rendered/source/edit）与 `viewMode`/`prevTab` 状态；三件套正文由 `TextArea` 改为 **默认 `MarkdownView` 渲染**，底部新增「编辑/预览」切换，切回编辑仍是原 `TextArea`；切换文档 Tab 时回到渲染视图 | **修复「三件套没渲染」**：规范文档 / 任务清单 / 验收清单默认渲染 Markdown，同时保留编辑能力 |
| `src/pages/TaskNew.css` | 修 | 增 `.task-new__doc-view`（渲染容器：flex 撑满 + 滚动 + 边框） | 渲染视图占位与滚动 |
| `src/components/PlanReviewPanel.tsx` | 修 | 方案全文由 `<pre>` 改为 `MarkdownView`（`resetKey=task.id`）；`CONTENT_STYLE` 去掉 `white-space: pre-wrap` | 「方案」Tab 渲染 Markdown |
| `src/components/TaskDetailInfo.tsx` | 修 | 「方案全文」由 `<pre>` 改为 `MarkdownView`（`resetKey=detail.id`） | 任务详情抽屉的方案全文渲染 Markdown |
| `src/components/TaskDetailArtifacts.tsx` | 修 | 预览区改用共用 `MarkdownView` 的渲染/源码切换能力（保留路径/内容复制与文件树） | 与三件套、方案全文呈现一致 |
| 构建产物 | 修 | `dist` 重新构建 → `copy_web_dist.py` 同步 69 文件 | hub 引用新包 `index-MdlgRrsR.js` |

## 交互与呈现细节

| 场景 | 行为 |
|------|------|
| 拖动分隔条 | 把手位于「预览/树」之间，视觉 2px、命中区 8px；hover 与拖拽态变主色；拖拽期间禁用文本选择；松手写入 localStorage |
| 宽度记忆 | `panel` 与 `drawer` 分别记忆（键含 variant），下次打开沿用；localStorage 不可用（隐私模式）静默回退默认宽度 |
| 拖拽范围 | 树宽限制在 `[160, 560]` 像素，避免拖到不可用 |
| 拖拽方向 | `panel`（预览在左、树在右）向**右**拖变宽；`drawer`（树在左）向**左**拖变宽——由 `direction` 参数区分 |
| Markdown 预览 | `.md`/`.markdown` 默认**渲染视图**；点「源码」切换为带行号源码；非 Markdown 文件直接显示行号源码；二进制文件提示改用打包下载 |
| 对话正文 | assistant 消息渲染 Markdown；user 消息保持纯文本原文；`thinking`/`steps` 保持既有呈现 |
| 搜索 | 按文件名/路径子串筛选，命中处高亮；工具条显示「共 N 个文件（筛选后 M / 总数）」，无筛选时不重复显示 |

## 已知限制（自建渲染器，按需扩展）

- **不支持**：表格、嵌套列表、任务列表（`- [ ]`）、脚注、HTML 透传、语法高亮着色；
- 行内解析为单层（不支持「粗体内再嵌斜体」这类嵌套）；
- 这些会在需要时按同一「React 元素」方式扩展，**不引入 HTML 字符串路径**以维持 XSS 免疫。

## 覆盖清单（哪些内容已被渲染）

| 位置 | 组件 | 默认视图 |
|------|------|---------|
| 对话 assistant 正文 | `TaskNewChatPanel` → `renderMarkdown` | 渲染（用户消息保持纯文本） |
| 三件套：规范文档 / 任务清单 / 验收清单 | `TaskNewDocPanel` → `MarkdownView` | **渲染**（可切编辑 / 源码） |
| 方案 Tab（Agent 实施方案） | `PlanReviewPanel` → `MarkdownView` | 渲染（可切源码） |
| 任务详情抽屉「方案全文」 | `TaskDetailInfo` → `MarkdownView` | 渲染（可切源码） |
| 产物文件预览（`.md`/`.markdown`） | `TaskDetailArtifacts` | 渲染（可切源码；二进制提示下载） |

## 验证

- `tsc -p tsconfig.app.json --noEmit` 通过；
- `eslint`（改动源文件）**0 error / 0 warning**（过程中修正两处：`react-hooks/refs` 渲染期写 ref → effect 同步；`react-refresh/only-export-components` 混导出 → 辅助函数收为模块私有）；
- 定向颜色字面量检查：**改动文件全部 0 处**颜色字面量；
- `npm run build` 通过；
- `copy_web_dist.py` 同步 69 个文件到 hub `web/`；hub `index.html` 已引用新包 **`index-MdlgRrsR.js`**；产物内可检索到 `md-code-block` / `artifacts__splitter` / `artifacts__node-size` / `复制文件路径`；
- 受影响的既有前端测试 `src/__tests__/components/TaskNewChatPanel.test.tsx` **3 项全过**。
- **未做**：浏览器实机走查（需你在页面上确认观感）。

## 登记记录

- 2026-09-27：创建并完成实施（UI 迭代，不新开 ADR；Spec/Harness 不涉及，验证以 tsc/eslint/build/产物稽核/既有测试为准）。
- 2026-09-27：`scripts/check_colors.py` 全量运行仍报 `App.tsx:57 #001529`——经核对属**另一路未提交的亮色主题工作**（在注释中，`git diff` 可见为新增行），**非本次引入**；本次改动文件均无颜色字面量。未改动该文件以免干扰在制品。
- 2026-09-27：构建与测试命令在沙箱下报 `EPERM`（vite/vitest 内部 spawn 子进程被限制），按规则原样重试并申请提升权限后通过；产出物已同步。
