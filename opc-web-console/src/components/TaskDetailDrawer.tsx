/* 职责说明（coding-standards 4.3）：详情信息区已拆至 TaskDetailInfo，审批/派发动作
   已拆至 useTaskPlanActions，驳回与手动派发弹窗已拆至 TaskDetailModals。 */
import { useCallback, useEffect, useState } from 'react';
import { App, Button, Drawer, Popconfirm, Space, Spin, Tabs } from 'antd';
import { CheckCircleOutlined, SendOutlined } from '@ant-design/icons';
import { confirmRedispatch, fetchTaskById } from '@/api/tasks';
import { TaskDetailArtifacts } from '@/components/TaskDetailArtifacts';
import { TaskDetailInfo } from '@/components/TaskDetailInfo';
import { TaskDetailModals } from '@/components/TaskDetailModals';
import { useTaskPlanActions } from '@/hooks/useTaskPlanActions';
import type { Task } from '@/types/task';

interface TaskDetailDrawerProps {
  /** 是否打开 */
  open: boolean;
  /** 任务 ID，为 null 时不加载 */
  taskId: string | null;
  /** 关闭回调 */
  onClose: () => void;
  /** 审批等操作成功后的回调，用于外层刷新列表 */
  onChanged?: () => void;
}

/**
 * 任务详情抽屉。
 *
 * 展示任务基础信息、派发模式、分配节点、迭代说明与 plan 方案全文；
 * - plan 模式已完成且产出方案的任务可在底部直接批准/驳回；
 * - 状态为 pending（含批准方案后的 pending+manual）或 failed 的任务可手动派发（ADR-017）。
 */
export function TaskDetailDrawer({ open, taskId, onClose, onChanged }: TaskDetailDrawerProps) {
  const { message } = App.useApp();
  const [detail, setDetail] = useState<Task | null>(null);
  const [loading, setLoading] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [dispatchTarget, setDispatchTarget] = useState<string>('');
  const [confirming, setConfirming] = useState(false);

  const loadDetail = useCallback(
    async (id: string) => {
      setLoading(true);
      try {
        const task = await fetchTaskById(id);
        setDetail(task);
      } catch (error) {
        message.error(error instanceof Error ? error.message : '任务详情加载失败');
      } finally {
        setLoading(false);
      }
    },
    [message],
  );

  // 关闭时清理本地状态：Drawer 状态同步依赖 useEffect 触发 resetDetail；
  // 该路径为 React「响应 prop 变化重置状态」的必要场景，同步 setState 保留。
  /* eslint-disable react-hooks/set-state-in-effect */
  const resetDetail = useCallback(() => {
    setDetail(null);
    setRejectOpen(false);
    setManualOpen(false);
    setConfirming(false);
  }, []);

  useEffect(() => {
    if (open && taskId) {
      void loadDetail(taskId);
    } else if (!open) {
      resetDetail();
    }
  }, [open, taskId, loadDetail, resetDetail]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const {
    feedback,
    setFeedback,
    approving,
    rejecting,
    dispatching,
    handleApprove,
    handleReject,
    handleManualDispatch,
  } = useTaskPlanActions({
    detail,
    onPatchDetail: setDetail,
    onChanged,
    onCloseRejectModal: () => setRejectOpen(false),
    onCloseDispatchModal: () => setManualOpen(false),
  });

  /** 仅 plan + 已完成（含待确认 review，ADR-038）+ 有方案的任务允许审批（后端同样校验） */
  const canReview =
    detail?.execution_mode === 'plan' &&
    (detail.status === 'review' || detail.status === 'completed') &&
    Boolean(detail.plan_content);

  /** 手动派发：pending（auto/manual 均可，批准方案后为 pending+manual）可选节点派发；
      failed 终态允许重新派发（后端同样校验，ADR-017）；
      requeue_pending（待确认改派）也可直接指定节点改派（ADR-059 D5） */
  const canManualDispatch =
    detail?.status === 'pending' ||
    detail?.status === 'failed' ||
    detail?.status === 'requeue_pending';

  /** 待确认改派：只在 requeue_pending 显示「确认改派」（ADR-059 D5） */
  const canConfirmRedispatch = detail?.status === 'requeue_pending';

  const handleConfirmRedispatch = useCallback(async () => {
    if (!detail) {
      return;
    }
    setConfirming(true);
    try {
      await confirmRedispatch(detail.id);
      message.success('已确认改派，任务交回调度器重新派发');
      onChanged?.();
      setDetail((prev) =>
        prev ? { ...prev, status: 'pending', dispatch_hold_reason: null } : prev,
      );
    } catch (error) {
      message.error(error instanceof Error ? error.message : '确认改派失败');
    } finally {
      setConfirming(false);
    }
  }, [detail, message, onChanged]);

  return (
    <Drawer
      title="任务详情"
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
      footer={
        canReview || canManualDispatch || canConfirmRedispatch ? (
          <Space style={{ display: 'flex', justifyContent: 'flex-end' }}>
            {canManualDispatch && (
              <Button
                icon={<SendOutlined />}
                onClick={() => setManualOpen(true)}
                data-testid="manual-dispatch-button"
              >
                手动派发
              </Button>
            )}
            {canConfirmRedispatch && (
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                loading={confirming}
                onClick={() => void handleConfirmRedispatch()}
                data-testid="confirm-redispatch-button"
              >
                确认改派
              </Button>
            )}
            {canReview && (
              <Button danger onClick={() => setRejectOpen(true)} data-testid="reject-plan-button">
                驳回方案
              </Button>
            )}
            {canReview && (
              <Popconfirm
                title="确认批准该方案？"
                description="批准后将转入 build 模式重新入队开发。"
                okText="批准"
                cancelText="取消"
                onConfirm={() => void handleApprove()}
              >
                <Button type="primary" loading={approving} data-testid="approve-plan-button">
                  批准方案
                </Button>
              </Popconfirm>
            )}
          </Space>
        ) : undefined
      }
    >
      <Spin spinning={loading}>
        <Tabs
          defaultActiveKey="detail"
          items={[
            {
              key: 'detail',
              label: '详情',
              children: detail ? <TaskDetailInfo detail={detail} /> : null,
            },
            {
              key: 'artifacts',
              label: '产物',
              children: <TaskDetailArtifacts taskId={taskId} open={open} />,
            },
          ]}
        />
      </Spin>

      <TaskDetailModals
        taskId={taskId}
        agentType={detail?.agent_type}
        rejectOpen={rejectOpen}
        onCloseReject={() => setRejectOpen(false)}
        feedback={feedback}
        onFeedbackChange={setFeedback}
        rejecting={rejecting}
        onReject={() => void handleReject()}
        manualOpen={manualOpen}
        onCloseManual={() => setManualOpen(false)}
        dispatching={dispatching}
        onDispatch={(nodeId, force) => void handleManualDispatch(nodeId, force)}
        dispatchTarget={dispatchTarget}
        onDispatchTargetChange={setDispatchTarget}
      />
    </Drawer>
  );
}
