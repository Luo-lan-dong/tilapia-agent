import { useState } from 'react';
import {
  Alert,
  Button,
  Empty,
  Input,
  Progress,
  Space,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd';
import { SendOutlined } from '@ant-design/icons';
import type { TaskInsight } from '@/types/task';

const { Text } = Typography;

interface TaskInsightPanelProps {
  /** 只读洞察（用量/告警/时间线）；尚未取到时为 undefined */
  insight: TaskInsight | undefined;
  /**
   * 向该任务正在跑的节点会话发言（ADR-061 M5a-steer）。
   *
   * 未提供时不渲染输入框（只读场景）。
   */
  onSteer?: (text: string) => void | Promise<void>;
  /** 时间线最多展示条数（默认 8，避免面板过长） */
  timelineLimit?: number;
}

/** 时间线条目颜色：按事件类别区分（全用语义变量，符合 ADR-052 颜色禁令） */
const TIMELINE_COLORS: Record<string, string> = {
  dispatch: 'var(--primary)',
  steering: 'var(--warning, var(--primary))',
  llm_usage: 'var(--text-muted)',
  agent_task_event: 'var(--primary)',
};

/**
 * 只读洞察面板（ADR-061 M5c，Q12/Q13）。
 *
 * 追加在任务摘要既有区块之后：**不改动**上段 6 区块与下段中枢待办，只做追加。
 * 展示三件事：按需计算的告警、用量汇总（含 95.2% 基线对比）、动作级时间线；
 * 并提供 `@节点` 发言入口（向正在跑的会话 steer 一句话，不改变任务状态）。
 */
export function TaskInsightPanel({
  insight,
  onSteer,
  timelineLimit = 8,
}: TaskInsightPanelProps) {
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);

  if (!insight) {
    return null;
  }

  const { summary, alerts, timeline } = insight;
  const hasUsage = summary.steps > 0;
  const recent = timeline.slice(-timelineLimit).reverse();
  const hitPercent = Math.round(summary.cache_hit_rate * 1000) / 10;
  const baselinePercent = Math.round(summary.cache_hit_baseline * 1000) / 10;

  const handleSend = async () => {
    const text = draft.trim();
    if (!text || !onSteer) {
      return;
    }
    setSending(true);
    try {
      await onSteer(text);
      setDraft('');
    } finally {
      setSending(false);
    }
  };

  /** 发言入口（M5a-steer）：不改变任务状态，只影响后续行为 */
  const steerBox = onSteer ? (
    <div className="task-insight__steer" data-testid="task-insight-steer">
      <Input
        size="small"
        value={draft}
        placeholder="@节点：给正在跑的会话补充要求（不中断任务）"
        aria-label="向节点发言"
        onChange={(event) => setDraft(event.target.value)}
        onPressEnter={() => void handleSend()}
      />
      <Button
        size="small"
        type="primary"
        icon={<SendOutlined />}
        loading={sending}
        disabled={!draft.trim()}
        onClick={() => void handleSend()}
      >
        发言
      </Button>
    </div>
  ) : null;

  return (
    <div className="task-insight" data-testid="task-insight">
      {alerts.length > 0 && (
        <div className="task-insight__alerts" data-testid="task-insight-alerts">
          {alerts.map((alert) => (
            <Alert
              key={alert.code}
              type="warning"
              showIcon
              message={alert.title}
              description={alert.detail}
            />
          ))}
        </div>
      )}

      <div className="task-insight__usage" data-testid="task-insight-usage">
        <Space size="small" wrap>
          <Text className="task-insight__label">用量</Text>
          {hasUsage ? (
            <>
              <Tag>{summary.steps} 轮</Tag>
              <Tag>
                {summary.input_tokens + summary.output_tokens} tokens
              </Tag>
              <Tooltip title={`基线 ${baselinePercent}%（ADR-060 实测）`}>
                <Tag color={summary.hit_rate_below_baseline ? 'orange' : 'green'}>
                  缓存命中 {hitPercent}%
                </Tag>
              </Tooltip>
            </>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              暂无用量的统计点
            </Text>
          )}
        </Space>
        {hasUsage && (
          <Progress
            percent={Math.min(hitPercent, 100)}
            size="small"
            strokeColor="var(--primary)"
            trailColor="var(--border)"
            showInfo={false}
          />
        )}
      </div>

      <div className="task-insight__timeline" data-testid="task-insight-timeline">
        <Text className="task-insight__label">最近动作</Text>
        {recent.length > 0 ? (
          <Timeline
            items={recent.map((entry) => ({
              color: TIMELINE_COLORS[entry.kind] ?? 'var(--border)',
              children: (
                <div className="task-insight__entry">
                  <Text className="task-insight__entry-title">{entry.title}</Text>
                  {entry.detail && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {entry.detail}
                    </Text>
                  )}
                </div>
              ),
            }))}
          />
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Text type="secondary" style={{ fontSize: 12 }}>
                暂无动作记录
              </Text>
            }
          />
        )}
      </div>

      {steerBox}
    </div>
  );
}
