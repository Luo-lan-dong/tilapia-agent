import { useCallback, useEffect, useState } from 'react';
import { App, Card, Space, Switch, Typography } from 'antd';
import { fetchAIDispatchSettings, updateAIDispatchSettings } from '@/api/skills';
import { COLORS } from '@/theme/tokens';

const { Title, Text } = Typography;

/**
 * AI 智能调度设置卡片（ADR-033）。
 *
 * 开启后自动调度在多候选节点场景由 AI 综合决策（默认关闭走纯规则）；
 * LLM 配置缺失或决策失败时自动回退规则调度，不影响任务派发。
 */
export function AIDispatchCard({ style }: { style?: React.CSSProperties }) {
  const { message } = App.useApp();
  const [enabled, setEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchAIDispatchSettings()
      .then((data) => {
        if (!cancelled) {
          setEnabled(data.enabled);
        }
      })
      .catch(() => {
        if (!cancelled) {
          message.error('AI 智能调度设置加载失败');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [message]);

  const handleToggle = useCallback(
    async (checked: boolean) => {
      setSaving(true);
      try {
        const data = await updateAIDispatchSettings(checked);
        setEnabled(data.enabled);
        message.success(
          data.enabled ? 'AI 智能调度已开启，下一轮调度生效' : 'AI 智能调度已关闭，恢复纯规则调度',
        );
      } catch (err) {
        message.error(err instanceof Error ? err.message : '保存失败');
      } finally {
        setSaving(false);
      }
    },
    [message],
  );

  return (
    <Card style={style}>
      <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }}>
        <div>
          <Title level={5} style={{ color: COLORS.textPrimary, margin: 0 }}>
            AI 智能调度
          </Title>
          <Text style={{ color: COLORS.textSecondary, display: 'block', marginTop: 4 }}>
            开启后，多个节点均可用时由 AI 综合负载与任务特征选择最合理的节点并记录决策理由；
            关闭或 LLM 不可用时自动回退「负载最低优先」的规则调度。
          </Text>
        </div>
        <Switch checked={enabled} loading={loading || saving} onChange={(v) => void handleToggle(v)} />
      </Space>
    </Card>
  );
}
