import { Collapse, Tag, Typography } from 'antd';
import {
  ApiOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  LoadingOutlined,
} from '@ant-design/icons';
import type { SkillStep } from '@/types/taskBuilding';
import { COLORS } from '@/theme/tokens';

const { Text } = Typography;

interface SkillStepListProps {
  steps: SkillStep[];
}

/**
 * 单个步骤的状态图标。
 */
function StepIcon({ status }: { status: SkillStep['status'] }) {
  if (status === 'running') {
    return <LoadingOutlined spin style={{ color: COLORS.primary }} />;
  }
  if (status === 'success') {
    return <CheckCircleFilled style={{ color: COLORS.primary }} />;
  }
  return <CloseCircleFilled style={{ color: COLORS.danger }} />;
}

/**
 * 单个步骤的展开内容：参数与结果摘要（等宽字体，模拟终端观感）。
 */
function StepBody({ step }: { step: SkillStep }) {
  return (
    <div className="skill-step__body">
      {step.args_summary && step.args_summary !== '{}' && (
        <div className="skill-step__line">
          <Text type="secondary">参数 </Text>
          <span className="skill-step__mono">{step.args_summary}</span>
        </div>
      )}
      {step.result_summary && (
        <div className="skill-step__line">
          <Text type="secondary">{step.status === 'success' ? '结果 ' : '失败 '}</Text>
          <span className="skill-step__mono">{step.result_summary}</span>
        </div>
      )}
    </div>
  );
}

/**
 * 技能执行步骤列表（ADR-033 执行链路可视化）。
 *
 * 对话消息内逐步展示 AI 调用的技能：执行中旋转、成功绿色对勾、失败红色叉；
 * 点击可展开参数与结果摘要。
 */
export function SkillStepList({ steps }: SkillStepListProps) {
  if (steps.length === 0) {
    return null;
  }
  return (
    <div className="skill-step__list">
      <Collapse
        ghost
        size="small"
        items={steps.map((step) => ({
          key: step.step_id,
          label: (
            <span className="skill-step__label">
              <StepIcon status={step.status} />
              <ApiOutlined style={{ color: COLORS.textSecondary }} />
              <span>{step.skill_display_name || step.skill_name}</span>
              {step.status === 'running' ? (
                <Tag color="processing" style={{ marginInlineStart: 'auto' }}>
                  执行中
                </Tag>
              ) : (
                <span className="skill-step__duration">
                  {((step.duration_ms ?? 0) / 1000).toFixed(1)}s
                </span>
              )}
            </span>
          ),
          children: <StepBody step={step} />,
        }))}
      />
    </div>
  );
}
