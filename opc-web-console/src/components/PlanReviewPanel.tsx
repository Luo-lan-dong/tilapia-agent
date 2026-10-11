/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行，仅超 3 行）：方案审批面板职责单一，故豁免。 */
import { useState } from 'react';
import { Alert, Button, Empty, Input, Modal, Popconfirm, Space, Tag, Typography } from 'antd';
import type { Task, TaskStatus } from '@/types/task';
import { MarkdownView } from '@/components/MarkdownView';
import { primaryButtonStyle } from '@/theme/tokens';

const { Paragraph, Text } = Typography;

interface PlanReviewPanelProps {
  /** 跟踪中的任务详情；未创建任务时为 undefined */
  task: Task | undefined;
  /** 详情加载中 */
  loading: boolean;
  /** 审批请求进行中（批准/驳回） */
  reviewing: boolean;
  onApprove: () => void;
  onReject: (feedback: string) => void;
  /** build 完成后转入迭代模式（继续修改） */
  onContinueIteration: () => void;
  /** 取消任务（问题修复：执行中可打断子节点开发） */
  onCancelTask?: () => Promise<void>;
}

/** 状态中文标签 */
const STATUS_LABELS: Record<TaskStatus, string> = {
  pending: '排队中',
  assigned: '已分配节点',
  running: '执行中',
  review: '待确认',
  completed: '已完成',
  failed: '执行失败',
  requeue_pending: '待确认改派',
  verify_blocked: '待验收决定',
};

/** 方案/说明全文展示区样式（与任务详情抽屉保持一致；色为主题变量，ADR-052）。
    不含 white-space:pre-wrap —— 内容改由 MarkdownView 渲染（块级元素自带间距）。 */
const CONTENT_STYLE: React.CSSProperties = {
  maxHeight: 360,
  overflow: 'auto',
  background: 'var(--code-block-bg)',
  color: 'var(--code-block-text)',
  padding: 12,
  borderRadius: 8,
  fontSize: 12,
};

/**
 * 构建页「方案」Tab 内容：跟踪任务状态、展示 plan 方案与 build 修改说明，
 * 并提供批准/驳回审批闭环（见 ADR-012 对话式评审增补决策）。
 */
export function PlanReviewPanel({
  task,
  loading,
  reviewing,
  onApprove,
  onReject,
  onContinueIteration,
  onCancelTask,
}: PlanReviewPanelProps) {
  const [rejectOpen, setRejectOpen] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [cancelling, setCancelling] = useState(false);

  const handleCancel = async () => {
    if (!onCancelTask) {
      return;
    }
    setCancelling(true);
    try {
      await onCancelTask();
    } finally {
      setCancelling(false);
    }
  };

  if (!task) {
    return (
      <div style={{ padding: 32 }}>
        <Empty
          description={
            <Text type="secondary">
              创建任务（建议选择「先规划 (plan)」模式）后，
              <br />
              Agent 返回的实施方案与本轮修改说明将展示在这里，支持直接批准或驳回
            </Text>
          }
        />
      </div>
    );
  }

  // ADR-038：方案产出落 review（待确认）；存量 completed 的 plan 任务保持可审批
  const canReview =
    task.execution_mode === 'plan' &&
    (task.status === 'review' || task.status === 'completed') &&
    Boolean(task.plan_content);
  const buildCompleted =
    task.execution_mode === 'build' && task.status === 'completed';
  const buildFailed = task.status === 'failed';

  const handleRejectSubmit = () => {
    const trimmed = feedback.trim();
    if (!trimmed) {
      return;
    }
    onReject(trimmed);
    setRejectOpen(false);
    setFeedback('');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%' }}>
      <Space wrap data-testid="plan-review-status">
        <Tag color={buildFailed ? 'red' : task.status === 'completed' ? 'green' : 'processing'}>
          {STATUS_LABELS[task.status]}
        </Tag>
        <Tag>{task.execution_mode === 'plan' ? '规划模式' : '开发模式'}</Tag>
        {typeof task.progress === 'number' && <Tag>进度 {task.progress}%</Tag>}
        {loading && <Text type="secondary">刷新中…</Text>}
      </Space>

      {buildFailed && (
        <Alert type="error" showIcon message="任务执行失败" description="可在任务队列查看详情并重试派发" />
      )}

      {!buildFailed && !canReview && !buildCompleted && (
        <Alert
          type="info"
          showIcon
          message={
            task.execution_mode === 'plan'
              ? 'Agent 正在规划中，方案返回后将展示在这里'
              : 'Agent 正在开发中，完成后将在此展示本轮修改说明'
          }
          description={
            <Space direction="vertical" size={6} style={{ width: '100%' }}>
              <span>{`任务 ${task.id.slice(0, 8)} 已进入任务队列，可留在本页等待，也可前往任务队列查看。`}</span>
              {onCancelTask && (
                <Popconfirm
                  title="确认取消该任务？"
                  description="将中断节点上正在进行的开发，任务标记为失败。"
                  onConfirm={() => void handleCancel()}
                >
                  <Button danger size="small" loading={cancelling}>
                    取消任务
                  </Button>
                </Popconfirm>
              )}
            </Space>
          }
        />
      )}

      {canReview && (
        <>
          <Text strong>Agent 返回的实施方案</Text>
          {/* 方案全文渲染 Markdown（可切源码），替代原纯文本 pre */}
          <MarkdownView
            content={task.plan_content ?? ''}
            resetKey={task.id}
            className="plan-review__content"
            style={{ ...CONTENT_STYLE, flex: 1 }}
            testId="plan-review-content"
            emptyText="方案内容为空"
          />
          <Space>
            <Button
              danger
              data-testid="plan-review-reject"
              loading={reviewing}
              onClick={() => setRejectOpen(true)}
            >
              驳回方案
            </Button>
            <Popconfirm
              title="确认批准该方案？"
              description="批准后任务将转入开发队列，Agent 按方案直接开发"
              okText="批准"
              cancelText="取消"
              onConfirm={onApprove}
            >
              <Button
                type="primary"
                data-testid="plan-review-approve"
                loading={reviewing}
                style={{ ...primaryButtonStyle }}
              >
                批准方案
              </Button>
            </Popconfirm>
          </Space>
        </>
      )}

      {task.execution_mode === 'plan' && task.status === 'completed' && !task.plan_content && (
        <Alert type="warning" showIcon message="任务已完成但未回传方案内容" description="可前往任务队列查看详情，或驳回后重新规划" />
      )}

      {buildCompleted && (
        <>
          <Alert type="success" showIcon message="开发已完成" />
          {task.change_summary ? (
            <>
              <Text strong>本轮修改说明</Text>
              <pre data-testid="plan-review-summary" style={{ ...CONTENT_STYLE, margin: 0, flex: 1 }}>
                {task.change_summary}
              </pre>
            </>
          ) : (
            <Paragraph type="secondary">Agent 未回传本轮修改说明。</Paragraph>
          )}
          <Space>
            <Button
              type="primary"
              data-testid="plan-review-continue"
              onClick={onContinueIteration}
              style={{ ...primaryButtonStyle }}
            >
              继续修改
            </Button>
          </Space>
        </>
      )}

      <Modal
        title="驳回方案"
        open={rejectOpen}
        okText="提交驳回"
        okButtonProps={{ disabled: !feedback.trim() }}
        cancelText="取消"
        onOk={handleRejectSubmit}
        onCancel={() => setRejectOpen(false)}
      >
        <Input.TextArea
          rows={4}
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder="请填写驳回原因与修改期望，Agent 将基于反馈修订方案"
          data-testid="plan-review-reject-input"
        />
      </Modal>
    </div>
  );
}
