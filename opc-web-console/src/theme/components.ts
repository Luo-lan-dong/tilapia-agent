/**
 * 组件层规格（frontend-component-standards §3.2.1 component 层）。
 *
 * 集中声明各基础组件的圆角 / 高度 / 图标规格，全部从 tokens.ts 档位取值，
 * **禁止页面覆盖**——同一组件全站只有一种规格；改变基调只改 tokens.ts。
 * ConfigProvider（App.tsx）与页面自定义样式一律引用本文件或 index.css
 * 语义变量，不得裸写尺寸字面量（check_tokens.py 门禁）。
 *
 * 密度（内边距）暂按 antd 默认值呈现，待各域迁移时再收敛到 SPACE 档位。
 */
import { CONTROL, ICON, RADIUS } from './tokens';

/** 按钮：默认圆角 md，高度三档对齐 antd controlHeight */
export const buttonSpec = {
  radius: RADIUS.md,
  heightSm: CONTROL.heightSm,
  heightMd: CONTROL.heightMd,
  heightLg: CONTROL.heightLg,
} as const;

/** 输入框：与按钮同档（同排出现的控件必须等高同圆角） */
export const inputSpec = {
  radius: RADIUS.md,
  heightSm: CONTROL.heightSm,
  heightMd: CONTROL.heightMd,
  heightLg: CONTROL.heightLg,
} as const;

/** 卡片/容器类（Card、抽屉、弹层）：容器圆角 lg */
export const cardSpec = {
  radius: RADIUS.lg,
} as const;

/** 表格：容器圆角 lg（外层 Card 同档） */
export const tableSpec = {
  radius: RADIUS.lg,
} as const;

/** 标签/小控件：圆角 sm + 小图标 */
export const tagSpec = {
  radius: RADIUS.sm,
  iconSize: ICON.sm,
} as const;

/** 图标三档 */
export const iconSpec = {
  sm: ICON.sm,
  md: ICON.md,
  lg: ICON.lg,
} as const;
