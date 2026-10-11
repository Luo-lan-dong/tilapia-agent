/* 任务摘要从属面板：当前动作（ADR-055）与验收阻断决策（ADR-061 M4）。
   与主面板的头部/进度/待办渲染分离，保持各自单一职责。 */
import { ThunderboltOutlined, WarningOutlined } from '@ant-design/icons';
import { Alert, Button, Space, Tag, Typography } from 'antd';
import type { VerifyReportPayload } from '@/types/task';

const { Text } = Typography;

/**
 * 执行阶段中文标签（ADR-055 封闭枚举，与节点侧 stage 一一对应）。
 */
const STAGE_LABELS: Record<string, string> = {
  preparing: '准备中',
  planning: '规划中',
  coding: '编写中',
  editing: '修改文件',
  running_command: '执行命令',
  searching: '检索代码',
  testing: '验证中',
  compacting: '压缩上下文',
  summarizing: '生成说明',
  idle: '空闲',
};

/**
 * 当前动作行（ADR-055）。
 *
 * 展示 Agent 实时上报的执行阶段与具体动作（如「修改文件 · 正在处理 src/main.py」），
 * 使任务在执行期间始终能看出「进行到哪一步」，不再只依赖待办列表。
 */
export function CurrentAction({ stage, message }: { stage?: string | null; message?: string | null }) {
  // stage 可能是节点新增的枚举值：未知时原样展示，不隐藏信息
  const stageLabel = stage ? (STAGE_LABELS[stage] ?? stage) : null;
  const hasDetail = Boolean(stageLabel || message);

  return (
    <div className="task-summary__action" data-testid="task-summary-action">
      <ThunderboltOutlined className="task-summary__action-icon" />
      <div className="task-summary__action-body">
        <div className="task-summary__action-title">
          <Text className="task-summary__action-label">当前动作</Text>
          {stageLabel && (
            <Tag color="processing" className="task-summary__action-tag">
              {stageLabel}
            </Tag>
          )}
        </div>
        <Text className="task-summary__action-message">
          {hasDetail ? (message ?? '正在执行') : '等待 Agent 上报执行动作…'}
        </Text>
      </div>
    </div>
  );
}

/**
 * 验收阻断决策区（ADR-061 M4）。
 *
 * 必过层红/无法验证时，节点自修用尽后会停在此态；这里如实展示**未通过的必过项**
 * 并给出两个出口：接受现状（标记完成并留痕）或继续修复（回 running 再修一轮）。
 */
export function VerifyBlockedPanel({
  report,
  onDecide,
}: {
  report: VerifyReportPayload | null | undefined;
  onDecide?: (decision: 'accept' | 'retry') => void | Promise<void>;
}) {
  const failures = report?.must_pass_failures ?? [];
  const unverifiable = report?.status === 'unverifiable';
  const description = unverifiable
    ? '未找到可执行的验收判据（无法验证），无法据此判定成功。'
    : `自动验收未通过${report?.repair_rounds ? `（已自动自修 ${report.repair_rounds} 轮）` : ''}：`;

  return (
    <div className="task-summary__verify" data-testid="task-summary-verify">
      <Alert
        type={unverifiable ? 'info' : 'warning'}
        showIcon
        icon={<WarningOutlined />}
        message={description}
        description={
          failures.length > 0 ? (
            <ul className="task-summary__verify-list">
              {failures.map((item) => (
                <li key={`${item.layer}-${item.name}`}>
                  <Text code>{item.layer}</Text> {item.name}：
                  <Text code>{item.cmd}</Text>
                </li>
              ))}
            </ul>
          ) : (
            <Text type="secondary" style={{ fontSize: 12 }}>
              需要你决定：接受现状，或给出继续修复的机会。
            </Text>
          )
        }
      />
      {onDecide && (
        <Space size="small" className="task-summary__verify-actions">
          <Button size="small" type="primary" onClick={() => void onDecide('retry')}>
            继续修复
          </Button>
          <Button size="small" onClick={() => void onDecide('accept')}>
            接受现状
          </Button>
        </Space>
      )}
    </div>
  );
}
