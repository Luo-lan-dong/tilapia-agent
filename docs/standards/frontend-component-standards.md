# OPC 前端组件使用规范

## 1. 适用范围

本规范适用于 `opc-web-console/` 项目。

涉及**新增/修改前端 React 组件**前，必须加载本规范。

---

## 2. 核心原则：禁止凭经验使用第三方组件

Ant Design 等第三方组件库的 API 会随版本迭代变化（props 废弃、行为调整、新增约束）。**所有组件用法必须以官方文档为准，禁止凭记忆或经验猜测。**

### 2.1 强制流程：使用组件前查阅文档

在代码中使用任何不熟悉的 Ant Design 组件时，按以下顺序操作：

1. 打开 [ant.design](https://ant.design) 搜索目标组件。
2. 阅读该组件的 **何时不使用** 和 **注意事项** 小节。
3. 查看 **演示代码** 中的用法模式，复制正确模式而非自行构造。
4. 如果组件有版本变更说明（如 4.x → 5.x 的 breaking change），确认当前项目版本后选择对应 API。

### 2.2 强制流程：修改已有组件代码时校验

修改已存在的组件调用时：

1. 先 `grep` 项目中该组件的其他使用方式，确认是否存在统一模式。
2. 检查是否有被废弃的 props（如 `visible` → `open`、`destroyOnClose` 等）。
3. 修改后检查浏览器控制台是否有 antd 的 warning，有 warning 必须修复。

---

## 3. 通用约束规则

以下规则适用于所有第三方组件库的使用，而非针对特定组件：

| 规则 | 说明 |
|------|------|
| **文档优先** | 使用组件前必须查阅官方文档，禁止凭记忆构造 props 组合 |
| **版本对齐** | 修改已有代码前先确认项目依赖的组件库版本，确保 API 兼容 |
| **Warning 零容忍** | 控制台出现第三方库的 warning 时，必须查明原因并修复，不能忽略 |
| **统一模式** | 同一组件在项目中的用法必须统一，发现不一致时以文档推荐方式为准 |
| **不造轮子** | 组件已有的功能（如 Spin 的 loading、Modal 的 destroyOnClose）禁止自行实现 |

### 3.1 主题与颜色管理（ADR-052，防"花卡"硬规则）

| 规则 | 说明 |
|------|------|
| **禁止颜色字面量** | tsx/ts/css 中禁止出现 `#hex`/`rgb()`/`rgba()` 字面量；CI 以 `opc-web-console/scripts/check_colors.py` 强制检查，白名单仅 `src/theme/tokens.ts`（色板常量）、`src/index.css`(CSS 变量定义)、`src/components/WindowControls.css`（Windows 关闭钮惯例红 `#e81123`/`#fff`） |
| **语义色只从变量取** | 组件样式一律 `var(--*)` 引用 `src/index.css` 定义的语义变量（与 `src/theme/tokens.ts` 双态保持一致）；新增语义色必须先在两处定义变量再使用 |
| **antd 色走 token** | antd 组件配色一律走 ConfigProvider token（双态取 `COLOR_SETS[theme]`），禁止 `.ant-*` 全局覆盖；禁止对颜色值做字符串拼接透明度（如 `` `${color}1A` ``），淡背景/边框用 CSS `color-mix()` 从同一变量派生（参考 NodeStatusTag.css） |

### 3.2 设计 token：圆角 / 间距 / 尺寸（ADR-065 前端统一规范）

> 目标：**同一种组件全站只有一种规格**（不会有的圆、有的方），且"把方按钮改成椭圆"**只需改一处**。

#### 3.2.1 三层 token（唯一数据源 `src/theme/tokens.ts`）

| 层 | 内容 | 说明 |
|---|---|---|
| **primitive（档位）** | `radius{none:0,sm:4,md:8,lg:12,xl:16,full:999}`、`space{xxs:2,xs:4,sm:8,md:12,lg:16,xl:24,xxl:32}`、`control{heightSm:24,heightMd:32,heightLg:40}`、`icon{sm:14,md:16,lg:20}` | 只给档位，不表达用途 |
| **semantic（语义）** | `--space-inline`（同行元素间）、`--space-stack`（纵向相邻段落）、`--space-group`（组与组）、`--card-padding`、`--section-gap` | **页面只允许用语义变量**，避免"卡片内边距"与"按钮内边距"互相误伤 |
| **component（组件）** | `src/theme/components.ts`：Button/Input/Card/Table/Tag 的圆角、高度、内边距 | 集中声明，**禁止页面覆盖** |

**两路输出、一处修改**：`tokens.ts` → ① antd `ConfigProvider` token（组件行为）② `src/index.css` 的 CSS 变量（自定义布局用）。改变视觉基调（方 ↔ 圆润 ↔ 大圆角）只改 `radius` 一处。

#### 3.2.2 已定基调（用户 2026-10-05 确认）

| 项 | 取值 |
|---|---|
| 视觉基调 | **圆润**：`radius.md = 8`（保持现状）、小控件 `radius.sm = 4`、容器 `radius.lg = 12`；**同种组件全站只用同一档** |
| 密度 | **默认 32**（`control.heightMd`）；紧凑用 `size="small"`（24），重要操作可 40 |
| 间距基数 | **4pt 基数 + 8pt 主档**（`space.sm=8` 主档，`md=12` 次之） |
| 页面覆盖 token | **默认禁止**；确需覆盖须加白名单注释并在 §3.2.3 登记理由 |

#### 3.2.3 硬规则与门禁

| 规则 | 说明 |
|---|---|
| **禁止尺寸字面量** | `src/**` 的 tsx/ts/css 禁止裸写 `borderRadius: <n>`、`padding/margin/gap: <n>`、`fontSize: <n>`（`0` 视为重置，允许）；一律引用 token 或语义变量 |
| 白名单 | `src/theme/tokens.ts`、`src/index.css`、`src/theme/components.ts`；其余文件需在**文件头注释**写 `token-override: <理由>` 方可豁免 |
| 门禁 | `opc-web-console/scripts/check_tokens.py`（与 `check_colors.py` 同款白名单机制）：默认**只报告不阻断**，迁移完成后以 `--strict` 升级为**报错**，防止回退 |
| 迁移纪律 | 规范 + 门禁告警 → 按域逐批替换硬编码 → 全量替换后门禁升 strict |

**已知待迁移（门禁首次运行会列出）**：`NodeMetrics.tsx:206`（`borderRadius: 6`，与全局 8 不一致）、`Logs.tsx:642`、`Skills.tsx:169`、`Docs.tsx:339`、`AlertList.tsx:55`、`DeliverableList.tsx:85`、`PlanReviewPanel.tsx:45`、`TaskDetailInfo.tsx:172`、`TaskNewChatPanel.tsx:154` 等硬编码 `borderRadius: 8`。

---

## 4. 已知踩坑点（持续补充）

> 以下为项目中已发现并修复的具体问题，作为「文档优先」原则的补充验证。
> 新发现的踩坑点按此格式追加，但**日常开发以第 2-3 节的通用约束为准**，不要依赖本节。

### 4.1 Spin tip 必须嵌套或 fullscreen

`<Spin tip="...">` 裸用会触发 `[antd: Spin] tip only work in nest or fullscreen pattern`。

```tsx
// ✅ 嵌套模式
<Spin tip="加载中..." spinning={loading}>
  <div style={{ minHeight: 48 }}>...</div>
</Spin>

// ❌ 裸用（warning）
<Spin tip="加载中..." />
```

**根本原因**：`tip` 层需要子元素作为挂载点，裸用时找不到挂载 DOM。

### 4.2 Modal visible → open

Ant Design 5.x 中 `visible` 已废弃，统一使用 `open`。控制台会出现 deprecation warning。

### 4.3 Form initialValues 稳定引用

`initialValues` 必须用 `useMemo` 或定义在组件外，禁止在 JSX 中每次渲染生成新对象，否则 Form 会反复 reset。

---

## 5. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-06 | 初始创建：以「文档优先」原则约束第三方组件使用，替代逐条列举踩坑点 | AI助手 |
| 2026-10-05 | 新增 §3.2「设计 token：圆角/间距/尺寸」（ADR-065 前端统一规范）：三层 token（primitive→semantic→component）+ 已定基调（圆润 `radius.md=8`、密度默认 32、4pt 基数 8pt 主档、页面默认禁止覆盖）+ 硬规则与门禁 `scripts/check_tokens.py`（默认告警、迁移完成后 `--strict` 报错） | AI助手 |
