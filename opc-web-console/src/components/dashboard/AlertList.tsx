import { Empty, Typography } from 'antd';
import { WarningOutlined } from '@ant-design/icons';
import type { AlertItem, AlertLevel } from '@/types/dashboard';
import { COLORS } from '@/theme/tokens';

const { Text } = Typography;

/**
 * 告警级别到背景色/图标的映射（ADR-038 告警三态）
 */
const LEVEL_STYLE_MAP: Record<AlertLevel, { bg: string; color: string }> = {
  danger: { bg: COLORS.alertDangerBg, color: COLORS.danger },
  warning: { bg: COLORS.alertWarningBg, color: COLORS.warning },
  info: { bg: COLORS.alertInfoBg, color: COLORS.info },
};

/**
 * AlertList 属性定义
 */
interface AlertListProps {
  /** 告警列表数据 */
  items: AlertItem[];
  /** 是否加载中 */
  loading: boolean;
  /** 点击告警条目（任务类告警查看任务详情） */
  onSelectTask?: (taskId: string) => void;
}

/**
 * 异常告警列表（ADR-038 仪表盘 v2 告警 Tab 内容）。

danger=节点离线（红）、warning=失败/待确认（琥珀）、info=排队超时（蓝），
垂直堆叠展示，任务类告警可点击查看任务详情。
 */
export function AlertList({ items, loading, onSelectTask }: AlertListProps) {
  if (!loading && items.length === 0) {
    return (
      <div style={{ padding: 32 }}>
        <Empty description={<Text type="secondary">暂无异常，集群运行正常</Text>} />
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map((alert) => {
        const style = LEVEL_STYLE_MAP[alert.level];
        const clickable = Boolean(alert.task_id && onSelectTask);
        return (
          <div
            key={alert.id}
            onClick={clickable ? () => onSelectTask?.(alert.task_id as string) : undefined}
            style={{
              background: style.bg,
              borderRadius: 8,
              padding: '10px 12px',
              cursor: clickable ? 'pointer' : 'default',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <WarningOutlined style={{ color: style.color, fontSize: 13 }} />
              <Text
                strong
                style={{ color: COLORS.textPrimary, fontSize: 13 }}
                ellipsis={{ tooltip: alert.title }}
              >
                {alert.title}
              </Text>
            </div>
            <Text
              style={{
                display: 'block',
                color: COLORS.textSecondary,
                fontSize: 12,
                marginTop: 4,
              }}
              ellipsis={{ tooltip: alert.description }}
            >
              {alert.description}
            </Text>
          </div>
        );
      })}
    </div>
  );
}
