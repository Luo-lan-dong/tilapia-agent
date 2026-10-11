import { Tag } from 'antd';
import { STATUS_LABEL_MAP } from '@/utils/nodeStatus';
import type { NodeStatus } from '@/types/agentNode';

/**
 * NodeStatusTag 属性定义
 */
interface NodeStatusTagProps {
  /** 节点状态 */
  status: NodeStatus;
}

/**
 * 节点状态标签组件。
 *
 * 状态色由 CSS 变量承载（ADR-052 className 方案），淡背景/边框由
 * NodeStatusTag.css 的 color-mix 从同一变量派生，双主题自动适配。
 */
export function NodeStatusTag({ status }: NodeStatusTagProps) {
  return (
    <Tag className={`node-status-tag node-status-tag--${status}`}>
      {STATUS_LABEL_MAP[status]}
    </Tag>
  );
}
