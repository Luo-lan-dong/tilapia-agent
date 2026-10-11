import type { NodeStatus } from '@/types/agentNode';

/**
 * 节点状态到展示文案的映射
 */
export const STATUS_LABEL_MAP: Record<NodeStatus, string> = {
  idle: '空闲',
  busy: '繁忙',
  starting: '启动中',
  offline: '离线',
  error: '异常',
};

/**
 * 节点状态到颜色的映射（ADR-052：值为主题 CSS 变量，双主题自动适配）。
 * 仅可用于 background/color/strokeColor 等 CSS 颜色上下文，
 * 禁止字符串拼接透明度（如 `${color}1A`）——需要淡色时用 NodeStatusTag.css
 * 的 color-mix 派生方案。
 */
export const STATUS_COLOR_MAP: Record<NodeStatus, string> = {
  idle: 'var(--status-running)',
  busy: 'var(--status-warn)',
  starting: 'var(--status-pending)',
  offline: 'var(--text-muted)',
  error: 'var(--error)',
};
