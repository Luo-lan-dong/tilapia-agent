# 亮色主题 Spec（ADR-052）

> 前置调研与颜色收敛清单见 [052-light-theme-color-plan.md](./052-light-theme-color-plan.md)。

## 1. 功能描述

为中枢前端（opc-web-console）提供亮色/暗色双主题支持。暗色为默认且迁移后视觉与现状完全一致；用户可通过顶栏按钮切换，选择持久化（localStorage，键 `opc:theme`）。

## 2. 用户故事

- US-1：作为用户，我可以在顶栏一键切换亮色/暗色主题，刷新后保持上次选择。
- US-2：作为用户，亮色模式下所有页面（仪表盘/节点/任务队列/新建任务/技能/文档/日志/记忆/设置）无"看不清"的元素（对比度达标），卡片颜色统一不花。
- US-3：作为开发者，新增组件时语义色只能从 14 个 CSS 变量取，写颜色字面量会被 CI 拦截。

## 3. 核心流程

```
App 启动
  → 读 localStorage("opc:theme")（缺省 "dark"）
  → html[data-theme] 置值（CSS 变量切换）+ ConfigProvider 切 algorithm/token
顶栏点击主题按钮
  → 取反主题态 → 同步 localStorage + data-theme + ConfigProvider
组件渲染
  → CSS/内联样式只引用 var(--*)（tokens.ts/index.css 定义处除外）
```

## 4. 语义变量定义（14 个）

| 变量 | 暗色（默认） | 亮色（初版，P3 定稿） |
| --- | --- | --- |
| `--bg-base` | `#1a1b1d` | `#f5f6fa` |
| `--bg-container` | `#222427` | `#ffffff` |
| `--bg-elevated` | `#222427` | `#ffffff` |
| `--border` | `rgba(224,226,242,.1)` | `rgba(20,25,40,.1)` |
| `--border-strong` | `rgba(224,226,242,.16)` | `rgba(20,25,40,.18)` |
| `--text-primary` | `#d1d3db` | `#1f2430` |
| `--text-secondary` | `#9599a6` | `#6b7280` |
| `--primary` | `#32f08c` | P3 定稿（倾向 `#0eaa6b` 系，保证亮底对比度） |
| `--primary-soft` | `rgba(50,240,140,.08)` | `rgba(14,170,107,.1)` |
| `--error` | `#f65a5a` | `#dc4444` |
| `--error-soft` | `rgba(246,90,90,.12)` | `rgba(220,68,68,.1)` |
| `--status-running` | `#33c192` | `#0e9f6e` |
| `--status-warn` | `#d27e24` | `#b45309` |
| `--status-pending` | `#d8a534` | `#a16207` |

> 灰态（idle）`#666b75` 映射到 `--text-secondary`，不单独设变量。

## 5. 替换映射（机械替换表）

`#1a1b1d→--bg-base`、`#222427→--bg-container`、`#d1d3db→--text-primary`、`#9599a6→--text-secondary`、`#666b75→--text-secondary`、`#32f08c→--primary`、`rgba(224,226,242,.1)→--border`、`rgba(224,226,242,.16)→--border-strong`、`rgba(50,240,140,.08)→--primary-soft`、`#f65a5a→--error`、`rgba(246,90,90,.12)→--error-soft`、`#33c192→--status-running`、`#d27e24→--status-warn`、`#d8a534→--status-pending`。

## 6. 降级链路

- localStorage 不可用（隐私模式等）：try/catch 兜底，主题回退暗色，功能不受影响；
- 变量未定义场景（理论上不存在）：CSS 变量在 `:root` 全局定义，所有页面可用。

## 7. 验收标准

1. `npm run build` 通过，tsc/eslint 零新增告警；
2. 暗/亮双态浏览器走查全部路由：文字/边框/卡片/Tag/状态点/代码块可读；
3. 扫描脚本 `scan_colors_20260927.py` 复跑：tokens.ts、index.css、WindowControls.css（`#e81123`）之外的颜色字面量清零；
4. 刷新页面主题保持；切换无页面级闪烁白屏。
