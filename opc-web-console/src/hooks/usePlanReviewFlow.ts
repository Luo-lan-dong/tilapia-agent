import { useCallback, useState } from 'react';
import { App } from 'antd';
import { useSearchParams } from 'react-router-dom';
import { approvePlan, rejectPlan } from '@/api/tasks';
import type { Task } from '@/types/task';

/** antd message 实例类型（App.useApp() 消费，避免静态方法警告） */
type MessageInstance = ReturnType<typeof App.useApp>['message'];

interface PlanReviewFlowParams {
  /** 当前跟踪的任务 ID（URL ?task=） */
  activeTaskId: string | undefined;
  /** 跟踪中的任务详情（build 完成后转入迭代用） */
  trackedTask: Task | undefined;
  /** 全局 message 实例 */
  message: MessageInstance;
  /** 审批动作后自增恢复轮询 */
  onResume: () => void;
}

interface PlanReviewFlowResult {
  /** 审批请求进行中（批准/驳回） */
  reviewing: boolean;
  /** 批准方案：任务转 build 重新入队 */
  handleApprovePlan: () => Promise<void>;
  /** 驳回方案：携带反馈以 plan 模式重新入队 */
  handleRejectPlan: (feedback: string) => Promise<void>;
  /** build 完成后从方案 Tab 转入迭代模式（继续修改） */
  handleContinueIteration: () => void;
}

/**
 * 方案审批闭环动作（ADR-012 对话式评审增补决策）。
 *
 * 从 TaskNew 页面拆出：批准 / 驳回 / 转迭代三个动作及其请求状态，
 * URL 操作（task → parent 参数切换）内聚在本 Hook 中。
 */
export function usePlanReviewFlow({
  activeTaskId,
  trackedTask,
  message,
  onResume,
}: PlanReviewFlowParams): PlanReviewFlowResult {
  const [reviewing, setReviewing] = useState(false);
  const [, setSearchParams] = useSearchParams();

  /**
   * 批准方案：任务转 build 重新入队，恢复轮询跟踪开发进度。
   */
  const handleApprovePlan = useCallback(async () => {
    if (!activeTaskId) {
      return;
    }
    setReviewing(true);
    try {
      await approvePlan(activeTaskId);
      message.success('方案已批准，任务已转入开发队列');
      onResume();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '批准失败');
    } finally {
      setReviewing(false);
    }
  }, [activeTaskId, message, onResume]);

  /**
   * 驳回方案：携带反馈以 plan 模式重新入队，恢复轮询等待新方案。
   */
  const handleRejectPlan = useCallback(
    async (feedback: string) => {
      if (!activeTaskId) {
        return;
      }
      setReviewing(true);
      try {
        await rejectPlan(activeTaskId, feedback);
        message.success('驳回成功，任务将以规划模式重新入队');
        onResume();
      } catch (err) {
        message.error(err instanceof Error ? err.message : '驳回失败');
      } finally {
        setReviewing(false);
      }
    },
    [activeTaskId, message, onResume],
  );

  /**
   * build 完成后从方案 Tab 转入迭代模式（继续修改）。
   */
  const handleContinueIteration = useCallback(() => {
    if (!trackedTask) {
      return;
    }
    const taskId = trackedTask.id;
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('task');
        next.set('parent', taskId);
        return next;
      },
      { replace: true },
    );
    message.info('已进入任务迭代模式');
  }, [trackedTask, setSearchParams, message]);

  return { reviewing, handleApprovePlan, handleRejectPlan, handleContinueIteration };
}
