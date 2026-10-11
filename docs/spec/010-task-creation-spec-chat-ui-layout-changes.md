# 任务创建 Spec Chat — 输入底栏布局改版 Changes 清单

> 关联决策：ADR-010（任务创建 Spec Chat 多轮对话构建）、ADR-028（工作目录绑定）、
> ADR-029/030（执行模型选择 / 多模型管理热切换）
>
> 变更日期：2026-09-13
>
> 变更目标：移除新建对话页顶部两行工具栏，将历史会话、对话模型、对话模式
> （快速/思考 + build/plan 合并为一个下拉）、子 Agent 执行模型选择移入输入卡片
> 底栏；工作目录改为右侧面板内的文件夹图标按钮（仅系统目录选择，不再支持
> 手动输入与历史下拉）；右侧面板默认宽度 560px 收窄为 400px。

## 变更清单

| # | 文件 | 类型 | 函数/位置级明细 | 作用说明 |
|---|------|------|-----------------|----------|
| 1 | `opc-web-console/src/components/TaskNewInputBar.tsx` | 新增 | 组件 `TaskNewInputBar`：props 含 model/chatModels/mode/executionMode/executorModel/sending/input 长度与各回调；内部含模型下拉热切换逻辑（自 Toolbar 迁移） | 输入卡片底栏：历史会话、对话模型、模式下拉、执行模型、字数、发送按钮 |
| 2 | `opc-web-console/src/components/TaskNewToolbar.tsx` | 删除 | 顶部工具栏与绑定条整体移除；迭代上下文条迁至 `TaskNewIterationBar`；模型下拉迁移至 InputBar | 顶部两行工具栏不再需要 |
| 3 | `opc-web-console/src/components/TaskNewIterationBar.tsx` | 新增 | 组件 `TaskNewIterationBar`：原 Toolbar 中 `parentTaskId && (...)` 迭代条原样迁出 | 迭代模式上下文条独立成组件 |
| 4 | `opc-web-console/src/components/TaskNewChatPanel.tsx` | 修改 | 输入卡片内嵌 `TaskNewInputBar`；字数计数与发送按钮移交 InputBar；props 增加底栏所需透传参数 | 输入区与底栏组合渲染 |
| 5 | `opc-web-console/src/components/TaskNewRightPanel.tsx` | 修改 | 面板顶部新增工作目录行（文件夹图标按钮 + 已选目录展示）；接收 workspaceDir/onWorkspaceDirChange 等新 props | 工作目录入口移入右侧面板 |
| 6 | `opc-web-console/src/pages/TaskNew.tsx` | 修改 | 移除 Toolbar 渲染与 workspaceDirOptions 相关逻辑；新增 IterationBar；workspaceDir/浏览回调改传 RightPanel；右面板默认宽度 400 | 页面接线重组 |
| 7 | `opc-web-console/src/components/TaskNew.css` | 修改 | 删除 toolbar/binding 样式；新增 `task-new__input-bar`、`task-new__workspace-row` 样式 | 样式同步 |
| 8 | `opc-web-console/src/__tests__/components/TaskNewToolbar.test.tsx` | 删除/改写 | Toolbar 用例迁移为 TaskNewInputBar.test.tsx | 测试随组件迁移 |
| 9 | `opc-web-console/src/__tests__/pages/TaskNew.test.tsx` | 修改 | data-testid 断言位置更新（绑定条移至右面板等） | 回归对齐 |

## 实施记录

- 2026-09-13 已全部实施并验证：
  - 新增 `TaskNewInputBar.tsx`（模型下拉热切换/模式下拉合并/执行模型/计数/发送态随迁）；
  - 新增 `TaskNewIterationBar.tsx`（迭代上下文条迁出，含未使用参数清理）；
  - 删除 `TaskNewToolbar.tsx` 与 `TaskNewToolbar.test.tsx`；
  - `TaskNewChatPanel.tsx` 改为 `footer` 插槽注入底栏，字数/发送按钮移入 InputBar；
  - `TaskNewRightPanel.tsx` 顶部新增工作目录行（文件夹图标 + 目录省略号展示，
    桌面壳调系统选择器、浏览器环境点击提示，迭代模式禁用）；
  - `TaskNew.tsx` 移除 Toolbar 与 workspaceDirOptions（历史目录下拉），保留
    currentDirTaskIds 同目录过滤；右面板默认 400px、折叠阈值 880→920；
  - `useResizablePanel.ts` 支持 `defaultWidth` 参数（缺省仍 560）；
  - `TaskNew.css` 删 toolbar/binding 样式，新增 input-bar/workspace-row 样式；
  - 测试：新增 `TaskNewInputBar.test.tsx`（10 用例），`TaskNew.test.tsx` 模式断言更新；
  - 验证：tsc 无错误、eslint 通过、vitest 16/16 通过、浏览器截图确认
    布局/下拉菜单/会话自动恢复均正常。

## 二次改版（2026-09-13 同日，用户反馈纠偏）：右侧面板改为「工具面板」

用户反馈：①历史会话入口藏太深；②右侧面板应为「选择式工具面板」——
空态展示工具入口列表，用户选择打开哪些内容，打开内容以标签页共存（参考
Trae/摹苉工具面板交互）。

| # | 文件 | 类型 | 函数/位置级明细 | 作用说明 |
|---|------|------|-----------------|----------|
| 1 | `components/TaskNewToolDefs.tsx` | 新增 | `ToolKey` 类型（DocTabKey + 'summary'）、`TOOLS` 元数据（标题/描述/图标）、`toolMeta()` | 工具面板入口清单 |
| 2 | `components/TaskToolEmpty.tsx` | 新增 | 组件 `TaskToolEmpty`：空态「从这里开始」入口列表 | 选择式打开工具 |
| 3 | `components/TaskNewRightPanel.tsx` | 重构 | 工具面板：空态入口列表 / 已打开标签栏（激活+关闭）/ 内容切换（TaskSummary 或 DocPanel 单视图）/ 标签栏「+」下拉打开未开工具 / 任务摘要无任务空态占位 | 右侧面板改为选择式工具面板 |
| 4 | `components/TaskNewDocPanel.tsx` | 修改 | 新增 `showTabs`（默认 true）属性；工具面板模式隐藏内置 Tab 栏 | 单工具视图复用 |
| 5 | `components/TaskNewChatPanel.tsx` | 修改 | 新增 `onOpenSessions` prop；顶部新增 `task-new__chat-header` 行渲染显眼「历史会话」按钮 | 历史会话入口上移 |
| 6 | `components/TaskNewInputBar.tsx` | 修改 | 移除时钟图标与 `onOpenSessions` prop | 入口去重 |
| 7 | `pages/TaskNew.tsx` | 修改 | `activeTab` → `openTools[]` + `activeTool`；`openTool`(useCallback)/`closeTool`；渲染期状态调整模式（lastTrackedTaskId）在 `?task=` 出现时自动打开 plan；接线更新 | 工具打开状态管理 |
| 8 | `pages/TaskNew.css` | 修改 | 新增 chat-header/history-button/tool-empty/tool-entry/tool-tabs/tool-tab/tool-menu/tool-placeholder 样式 | 样式同步 |
| 9 | `__tests__/components/TaskNewInputBar.test.tsx` | 修改 | 删除历史会话用例（9 用例） | 对齐 |
| 10 | `__tests__/pages/TaskNew.test.tsx` | 修改 | 「创建任务」用例先点 `tool-entry-spec` 打开文档工具；折叠阈值注释 880→920 | 对齐 |

验证：tsc 无错误、eslint 通过（规避 react-hooks/set-state-in-effect，采用渲染期
状态调整模式）、vitest 15/15 通过；浏览器实测：空态入口列表、打开工具、
「+」多标签共存、任务摘要空态、`?task=` 自动打开方案标签均正常。

## 三次迭代（2026-09-13 同日）：产物工具迁移 + 卡片间隙收紧

用户反馈：①工具面板卡片之间缝隙太大，调小到刚好能区分；②任务产物
（原在任务队列 → 任务详情抽屉 → 产物 Tab 查看）迁入工具面板，节省查看链路
（参考 Trae 资源管理器：文件树 + 选中文件预览内容）。

| # | 文件 | 类型 | 函数/位置级明细 | 作用说明 |
|---|------|------|-----------------|----------|
| 1 | `components/TaskNewToolDefs.tsx` | 修改 | `ToolKey` 联合类型加 `'artifacts'`；`TOOLS` 增加产物入口（folder 图标） | 产物工具元数据 |
| 2 | `components/TaskDetailArtifacts.tsx` | 修改 | 新增 `variant`（drawer 默认/panel）属性：panel 模式树/预览去 maxHeight 改 flex 填满容器，内外 gap 12→8 | 复用产物资源管理器适配工具面板高度 |
| 3 | `components/TaskNewRightPanel.tsx` | 修改 | `activeTool === 'artifacts'` 分支：有跟踪任务渲染 `TaskDetailArtifacts(variant=panel)`，否则空态占位；DocPanel 分支排除 artifacts | 产物工具接入面板 |
| 4 | `pages/TaskNew.css` | 修改 | `.task-new__right` gap 16→8；`.task-new__tool-panel` gap 8→6 | 卡片缝隙收紧 |
| 5 | `pages/TaskNew.tsx` | 无修改 | 产物数据源为 `trackedTask`（useTaskPlanTracking），无独立接线 | — |

验证：tsc 无错误、eslint 通过、vitest 15/15 通过；浏览器实测（任务
30f4f253，4 个产物文件）：产物工具打开、文件树展示、点击 login.html 预览
HTML 源码、打包下载/刷新清单入口齐全；卡片缝隙视觉收紧。

## 四次迭代（2026-09-14）：任务名称列截断 + 悬停全名

用户反馈：任务名称过长撑开表格（任务队列页与集群仪表盘的任务队列卡片），
需要向右滚动才能看到右侧列；应默认截断显示，悬停显示全名。

| # | 文件 | 类型 | 函数/位置级明细 | 作用说明 |
|---|------|------|-----------------|----------|
| 1 | `components/TaskTable.tsx` | 修改 | ①「任务名称」列 render 改用 `Text ellipsis={{ tooltip: title }}`（悬停 Tooltip 全名）；②Table `scroll.x` 由 `max-content` 改为固定 1100（其余列宽合计），长标题列弹性收缩省略 | 两页共用组件，一处修改生效；桌面端无横向滚动，窄屏仍可滑动 |

注：原 `ellipsis: true` 因 `scroll.x='max-content'` 按内容最大宽度展开而失效，
两者需配合调整。

验证：tsc 无错误、eslint 通过、vitest 全量 30/30 通过；浏览器实测任务队列页
与集群仪表盘：名称截断省略号、悬停 Tooltip 全名、无横向滚动。

## 五次迭代（2026-09-25）：按设计稿「新建任务页-布局优化」调整布局

依据设计稿 `设计稿/新建任务页-布局优化/index.html`（仅布局、不动配色）：
①产物面板内预览区在左、文件树在右占 45% 宽紧贴面板最右缘，空态居中小图标提示，
刷新清单底部居中；②输入框功能工具行内嵌输入框（textarea 下方分隔线一行），
上传加号并入工具行左侧；③历史会话按钮浮层化，聊天区顶部不再占布局行，
空态标题垂直居中。

| # | 文件 | 类型 | 函数/位置级明细 | 作用说明 |
|---|------|------|-----------------|----------|
| 1 | `components/TaskDetailArtifacts.tsx` | 修改 | 树/预览拆为 `treeBlock`/`previewBlock` JSX 变量；panel 形态渲染顺序对调（预览 flex:1 在左、树 45% 在右），drawer 形态维持「树左 220px/预览右」；panel 预览空态居中（FileTextOutlined + 「点击右侧文件预览内容」）；刷新清单按钮包 flex 容器 panel 形态居中 | 对齐设计稿三处标注 |
| 2 | `components/TaskNewChatPanel.tsx` | 修改 | 删除 `task-new__input-shell-footer` 独立加号行；Upload 上传按钮与 `footer`（TaskNewInputBar）合并为 `task-new__input-toolbar` 行，渲染移入 `task-new__input-shell` 内部（textarea 之后） | 输入工具行内嵌输入框，对齐设计稿 input-box 结构 |
| 3 | `pages/TaskNew.css` | 修改 | `.task-new__chat` 加 position:relative；`.task-new__chat-header` 改绝对定位浮层（top 8px/right 12px，去 padding）；新增 `.task-new__input-toolbar`（border-top 分隔）与内嵌 `.task-new__input-bar` flex:1；删除 `.task-new__input-shell-footer` | 浮层化 + 工具行样式 |
| 4 | `pages/TaskNew.tsx`、`components/TaskNewInputBar.tsx` | 无修改 | footer 组装方式与 InputBar props 不变，仅 ChatPanel 内渲染位置调整 | — |
