/**
 * 设计 token 单一事实源。
 *
 * - 颜色双态（ADR-052）：暗色 = 迁移前原硬编码值（收敛后视觉零变化）；
 *   亮色 = 初版亮色值（P3 走查定稿）。ConfigProvider 依据主题态取
 *   COLOR_SETS[theme]；CSS 变量定义于 index.css（:root 暗色默认 +
 *   html[data-theme='light'] 覆盖），两侧必须保持一致。
 * - 尺寸档位（frontend-component-standards §3.2.1 primitive 层，2026-10-07 落地）：
 *   RADIUS/SPACE/CONTROL/ICON 四组档位，尺寸不分主题态；两路输出
 *   （ConfigProvider token + index.css 语义变量），改基调只改本文件。
 */
export type ThemeName = 'dark' | 'light';

export interface PaletteColors {
  /** 主色（品牌绿） */
  primary: string;
  /** 主色底上的文字色（深色） */
  onPrimary: string;
  /** 页面基础背景 */
  bgBase: string;
  /** 容器/卡片背景 */
  bgContainer: string;
  /** 浮层/ elevated 背景 */
  bgElevated: string;
  /** 主文字 */
  textPrimary: string;
  /** 次级文字 */
  textSecondary: string;
  /** 弱化文字 */
  textMuted: string;
  /** 常规边框 */
  border: string;
  /** 强调边框 */
  borderStrong: string;
  /** 进度条轨迹/弱分割 */
  borderFaint: string;
  /** 弱面板/悬停背景 */
  surface: string;
  /** 代码/弱高亮背景 */
  codeBg: string;
  /** 主色弱背景（能力标签等） */
  primaryBg: string;
  /** 主色弱边框 */
  primaryBorder: string;
  /** 危险/告警红（负载 critical 等） */
  danger: string;
  /** 警告橙（负载 high 等） */
  warning: string;
  /** 信息蓝（负载 normal 等） */
  info: string;
  /** 告警 danger 弱背景（ADR-038 告警三态） */
  alertDangerBg: string;
  /** 告警 warning 弱背景（ADR-038 告警三态） */
  alertWarningBg: string;
  /** 告警 info 弱背景（ADR-038 告警三态） */
  alertInfoBg: string;
}

/** 暗色（默认）：迁移前原硬编码值 */
export const DARK_COLORS: PaletteColors = {
  primary: '#32F08C',
  onPrimary: '#0C0C0D',
  bgBase: '#1A1B1D',
  bgContainer: '#222427',
  bgElevated: '#222427',
  textPrimary: '#D1D3DB',
  textSecondary: '#9599A6',
  textMuted: '#666B75',
  border: 'rgba(224, 226, 242, 0.1)',
  borderStrong: 'rgba(224, 226, 242, 0.16)',
  borderFaint: 'rgba(224, 226, 242, 0.08)',
  surface: 'rgba(224, 226, 242, 0.04)',
  codeBg: 'rgba(224, 226, 242, 0.06)',
  primaryBg: 'rgba(50, 240, 140, 0.12)',
  primaryBorder: 'rgba(50, 240, 140, 0.2)',
  danger: '#F65A5A',
  warning: '#F6A935',
  info: '#5B8DEF',
  alertDangerBg: 'rgba(246, 90, 90, 0.12)',
  alertWarningBg: 'rgba(246, 169, 53, 0.12)',
  alertInfoBg: 'rgba(91, 141, 239, 0.12)',
} as const;

/** 亮色（初版，P3 走查定稿） */
export const LIGHT_COLORS: PaletteColors = {
  primary: '#0EAA6B',
  onPrimary: '#FFFFFF',
  bgBase: '#F5F6FA',
  bgContainer: '#FFFFFF',
  bgElevated: '#FFFFFF',
  textPrimary: '#1F2430',
  textSecondary: '#6B7280',
  textMuted: '#9CA3AF',
  border: 'rgba(20, 25, 40, 0.1)',
  borderStrong: 'rgba(20, 25, 40, 0.18)',
  borderFaint: 'rgba(20, 25, 40, 0.06)',
  surface: 'rgba(20, 25, 40, 0.04)',
  codeBg: 'rgba(20, 25, 40, 0.05)',
  primaryBg: 'rgba(14, 170, 107, 0.12)',
  primaryBorder: 'rgba(14, 170, 107, 0.25)',
  danger: '#DC4444',
  warning: '#B45309',
  info: '#2563EB',
  alertDangerBg: 'rgba(220, 68, 68, 0.1)',
  alertWarningBg: 'rgba(180, 83, 9, 0.1)',
  alertInfoBg: 'rgba(37, 99, 235, 0.08)',
} as const;

/** 主题态 → 色板（ConfigProvider token 数据源） */
export const COLOR_SETS: Record<ThemeName, PaletteColors> = {
  dark: DARK_COLORS,
  light: LIGHT_COLORS,
};

/* ============================================================
 * 尺寸档位（primitive 层，frontend-component-standards §3.2.1）
 * 只给档位不表达用途；语义/组件层见 src/theme/components.ts 与
 * index.css 语义变量。用户已定基调（2026-10-05）：
 * 圆润 radius.md=8 / 小控件 sm=4 / 容器 lg=12；密度默认 32。
 * ============================================================ */

/** 圆角档位：sm=小控件（Tag/Menu 项）、md=控件默认（按钮/输入框）、lg=容器（卡片/抽屉） */
export const RADIUS = { none: 0, sm: 4, md: 8, lg: 12, xl: 16, full: 999 } as const;

/** 间距档位：4pt 基数 + 8pt 主档（sm=8 主档、md=12 次之） */
export const SPACE = { xxs: 2, xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 } as const;

/** 控件高度档位：紧凑 24（size="small"）/ 默认 32 / 重要操作 40 */
export const CONTROL = { heightSm: 24, heightMd: 32, heightLg: 40 } as const;

/** 图标尺寸档位 */
export const ICON = { sm: 14, md: 16, lg: 20 } as const;

/**
 * 存量内联引用兼容层（ADR-052）：值为主题 CSS 变量引用（var(--*)），
 * 主题切换时自动跟随；仅可用于 style/strokeColor/color prop 等 CSS 颜色
 * 上下文，禁止字符串拼接透明度。新代码直接写 var(--*)，逐步迁移后删除本别名。
 */
export const COLORS = {
  primary: 'var(--primary)',
  onPrimary: 'var(--on-primary)',
  bgBase: 'var(--bg-base)',
  bgContainer: 'var(--bg-container)',
  bgElevated: 'var(--bg-elevated)',
  textPrimary: 'var(--text-primary)',
  textSecondary: 'var(--text-secondary)',
  textMuted: 'var(--text-muted)',
  border: 'var(--border)',
  borderStrong: 'var(--border-strong)',
  borderFaint: 'var(--border-faint)',
  surface: 'var(--surface)',
  codeBg: 'var(--code-bg)',
  primaryBg: 'var(--primary-bg)',
  primaryBorder: 'var(--primary-border)',
  danger: 'var(--error)',
  warning: 'var(--status-warn)',
  info: 'var(--info)',
  alertDangerBg: 'var(--error-soft)',
  alertWarningBg: 'var(--alert-warning-soft)',
  alertInfoBg: 'var(--alert-info-soft)',
} as const;

/**
 * 主色按钮内联样式（主色与文字色均走 CSS 变量，双主题自动适配；
 * 新代码优先直接使用 type="primary"）。
 */
export const primaryButtonStyle = {
  background: 'var(--primary)',
  borderColor: 'var(--primary)',
  color: 'var(--on-primary)',
} as const;
