import { Card, Typography } from 'antd';
import { COLORS } from '@/theme/tokens';

/**
 * StatCard 属性定义
 */
interface StatCardProps {
  /** 指标标题 */
  label: string;
  /** 指标数值 */
  value: string | number;
  /** 趋势文案，可选 */
  trend?: string;
  /** 趋势类型，决定颜色方向 */
  trendType?: 'up' | 'down' | 'neutral';
  /** 数值是否以危险红色展示（如异常事件，ADR-038） */
  danger?: boolean;
}

const { Text } = Typography;

/**
 * KPI 统计卡片组件。
 *
 * 使用 Ant Design Card 实现，采用深色主题卡片风格，
 * 展示标题、数值与可选的趋势文案。
 */
export function StatCard({ label, value, trend, trendType = 'neutral', danger }: StatCardProps) {
  const trendColorMap = {
    up: COLORS.primary,
    down: COLORS.danger,
    neutral: COLORS.textSecondary,
  };

  return (
    <Card
      styles={{
        body: {
          padding: '16px',
          background: COLORS.bgContainer,
          border: '1px solid ' + COLORS.border,
          borderRadius: '8px',
        },
      }}
      style={{ background: 'transparent', border: 'none' }}
    >
      <Text style={{ display: 'block', color: COLORS.textSecondary, fontSize: '13px', marginBottom: '8px' }}>
        {label}
      </Text>
      <Text
        style={{
          display: 'block',
          color: danger ? COLORS.danger : COLORS.textPrimary,
          fontSize: '28px',
          fontWeight: 600,
          lineHeight: '36px',
          marginBottom: '8px',
        }}
      >
        {value}
      </Text>
      {trend && (
        <Text style={{ color: trendColorMap[trendType], fontSize: '12px' }}>{trend}</Text>
      )}
    </Card>
  );
}
