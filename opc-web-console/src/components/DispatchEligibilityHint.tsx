/* 职责说明（coding-standards 4.3）：从 TaskDetailModals 拆出的「派发准入提示」，
   承载 ADR-059 D3 的候选节点缺口展示：把「排队中 0%」变成可解释的等待。 */
import { Descriptions, Space, Tag, Typography } from 'antd';
import type { TaskEligibilityCandidate, TaskEligibilityResponse } from '@/api/tasks';
import { COLORS } from '@/theme/tokens';

const { Text } = Typography;

/** 单个候选节点的紧凑读数：内存 / 负载 / 槽位。 */
function metricsText(candidate: TaskEligibilityCandidate): string {
  const parts: string[] = [];
  parts.push(
    candidate.available_memory_gb == null
      ? '可用内存未上报'
      : `可用内存 ${candidate.available_memory_gb}GB`,
  );
  parts.push(
    candidate.load_score == null ? '负载未上报' : `负载 ${candidate.load_score}`,
  );
  parts.push(`槽位 ${candidate.slots_free}`);
  return parts.join(' · ');
}

/**
 * 派发准入提示。
 *
 * 展示提示语、可自主节点数，以及（选中节点时）该节点的完整缺口清单。
 * ``blocking_reasons`` 并列给出全部缺口，避免「解决内存后才发现负载也不达标」。
 */
export function DispatchEligibilityHint({
  eligibility,
  selectedNodeId,
}: {
  eligibility: TaskEligibilityResponse | null;
  selectedNodeId?: string;
}) {
  if (!eligibility) {
    return null;
  }
  const selected = eligibility.candidates.find((c) => c.node_id === selectedNodeId);

  return (
    <div style={{ marginTop: 12 }} data-testid="dispatch-eligibility-hint">
      <Text style={{ color: COLORS.textSecondary }}>{eligibility.hint}</Text>
      {selected && (
        <Descriptions
          column={1}
          size="small"
          bordered
          style={{ marginTop: 8 }}
          items={[
            {
              key: 'node',
              label: '所选节点',
              children: (
                <Space>
                  <Text>{selected.name}</Text>
                  {selected.online ? (
                    <Tag color="blue">在线</Tag>
                  ) : (
                    <Tag color="default">离线</Tag>
                  )}
                  {selected.eligible ? (
                    <Tag color="green">可自主</Tag>
                  ) : (
                    <Tag color="orange">不可自主</Tag>
                  )}
                </Space>
              ),
            },
            { key: 'metrics', label: '当前读数', children: metricsText(selected) },
            ...(selected.eligible
              ? []
              : [
                  {
                    key: 'blocking',
                    label: '缺口',
                    children: (
                      <div data-testid="dispatch-blocking-reasons">
                        {(selected.blocking_reasons.length
                          ? selected.blocking_reasons
                          : [selected.blocking ?? '暂不可用']
                        ).map((reason) => (
                          <div key={reason} style={{ color: COLORS.textSecondary }}>
                            · {reason}
                          </div>
                        ))}
                      </div>
                    ),
                  },
                ]),
          ]}
        />
      )}
    </div>
  );
}
