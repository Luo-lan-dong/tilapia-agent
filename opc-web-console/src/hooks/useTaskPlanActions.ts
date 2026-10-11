import { useCallback, useState } from 'react';
import { App } from 'antd';
import { approvePlan, dispatchTask, rejectPlan } from '@/api/tasks';
import type { Task } from '@/types/task';

interface UseTaskPlanActionsParams {
  /** 当前展示的任务详情（局部 state，动作成功后就地更新） */
  detail: Task | null;
  /** 详情局部更新器（用于动作成功后同步状态标签） */
  onPatchDetail: (updater: (prev: Task | null) => Task | null) => void;
  /** 动作成功后通知外层刷新列表 */
  onChanged?: () => void;
  onCloseRejectModal: () => void;
  onCloseDispatchModal: () => void;
}

interface UseTaskPlanActionsResult {
  /** 驳回反馈内容 */
  feedback: string;
  setFeedback: (value: string) => void;
  /** 批准请求进行中 */
  approving: boolean;
  /** 驳回请求进行中 */
  rejecting: boolean;
  /** 手动派发请求进行中 */
  dispatching: boolean;
  handleApprove: () => Promise<void>;
  handleReject: () => Promise<void>;
  /**
   * 手动派发到指定节点。
   *
   * force 为真时才允许越过 ADR-059 准入门槛；否则目标节点不达标则任务
   * 转为排队等待该节点（界面如实展示排队原因，不谎报已指派）。
   */
  handleManualDispatch: (target: string, force?: boolean) => Promise<void>;
}

/**
 * 任务详情抽屉的审批/派发动作（ADR-017）。
 *
 * 从 TaskDetailDrawer 拆出：批准方案、驳回方案、手动派发三个动作及
 * 其请求状态；动作成功后就地修正 detail 状态并通知外层刷新。
 */
export function useTaskPlanActions({
  detail,
  onPatchDetail,
  onChanged,
  onCloseRejectModal,
  onCloseDispatchModal,
}: UseTaskPlanActionsParams): UseTaskPlanActionsResult {
  const { message } = App.useApp();
  const [feedback, setFeedback] = useState('');
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [dispatching, setDispatching] = useState(false);

  const handleApprove = useCallback(async () => {
    if (!detail) {
      return;
    }
    setApproving(true);
    try {
      await approvePlan(detail.id);
      message.success('方案已批准，任务已转入开发队列');
      onChanged?.();
      onPatchDetail((prev) => (prev ? { ...prev, status: 'pending' } : prev));
    } catch (error) {
      message.error(error instanceof Error ? error.message : '批准方案失败');
    } finally {
      setApproving(false);
    }
  }, [detail, message, onChanged, onPatchDetail]);

  const handleReject = useCallback(async () => {
    if (!detail || !feedback.trim()) {
      return;
    }
    setRejecting(true);
    try {
      await rejectPlan(detail.id, feedback.trim());
      message.success('驳回成功，任务将以规划模式重新入队');
      onCloseRejectModal();
      onChanged?.();
      onPatchDetail((prev) => (prev ? { ...prev, status: 'pending' } : prev));
    } catch (error) {
      message.error(error instanceof Error ? error.message : '驳回方案失败');
    } finally {
      setRejecting(false);
    }
  }, [detail, feedback, message, onCloseRejectModal, onChanged, onPatchDetail]);

  const handleManualDispatch = useCallback(
    async (nodeId: string, force = false) => {
      if (!detail) {
        return;
      }
      setDispatching(true);
      try {
        const result = await dispatchTask(detail.id, 'code', undefined, nodeId, force);
        // ADR-059 D2：目标节点未达准入水位时后端**不派送**，改为排队等待该节点。
        // 必须按返回区分两种结果，否则界面会谎报「已指派」而实际还在排队。
        const queued = Boolean(result.queued_on_node) || result.status === 'pending';
        if (queued) {
          message.warning(
            result.hold_reason ?? `节点暂不可自主，任务已排队等待「${result.queued_on_node ?? ''}」`,
          );
        } else {
          message.success(
            force ? '已按你的要求越过资源水位强行派送' : '任务已指派给所选节点',
          );
        }
        onCloseDispatchModal();
        onChanged?.();
        onPatchDetail((prev) =>
          prev
            ? {
                ...prev,
                dispatch_type: queued ? prev.dispatch_type : 'manual',
                dispatch_window_until: queued ? prev.dispatch_window_until : null,
                // Task.assigned_node_id 为 optional string（不接受 null）：
                // queued 时以 undefined 覆盖清空指派节点
                assigned_node_id: queued ? undefined : nodeId,
                target_node_id: queued ? nodeId : prev.target_node_id,
                dispatch_hold_reason: queued ? (result.hold_reason ?? '等待目标节点达标') : null,
                forced_dispatch: force,
                status: queued ? 'pending' : 'assigned',
              }
            : prev,
        );
      } catch (error) {
        message.error(error instanceof Error ? error.message : '手动派发失败');
      } finally {
        setDispatching(false);
      }
    },
    [detail, message, onCloseDispatchModal, onChanged, onPatchDetail],
  );

  return {
    feedback,
    setFeedback,
    approving,
    rejecting,
    dispatching,
    handleApprove,
    handleReject,
    handleManualDispatch,
  };
}
