import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchOrchestratorTodos, fetchTaskById, fetchTaskInsight } from '@/api/tasks';
import type { OrchestratorTodo, Task, TaskInsight } from '@/types/task';

/** 轮询间隔（毫秒） */
const POLL_INTERVAL_MS = 5000;

export interface UseTaskPlanTrackingState {
  /** 最近一次轮询到的任务详情，尚未取到时为 undefined */
  task: Task | undefined;
  /** 首次加载中 */
  loading: boolean;
  /**
   * 中枢编排器自己的待办（ADR-061 M5d，任务摘要下段）。
   *
   * 与任务详情里的 `todos`（节点上报，上段）物理分离；取数失败时保持上一次结果
   * （下段是辅助信息，不应因偶发失败把已有清单清空）。
   */
  orchestratorTodos: OrchestratorTodo[];
  /**
   * 任务只读洞察（ADR-061 M5c）：用量曲线/告警/时间线。
   *
   * 与待办同理：取数失败保持上一次结果（辅助信息，不应因偶发失败清空）。
   */
  insight: TaskInsight | undefined;
}

/**
 * 任务跟踪轮询：创建任务后每 5 秒拉取任务详情，供方案 Tab 展示状态、
 * plan 方案与 build 修改说明（见 ADR-012 对话式评审增补决策）。
 *
 * 轮询在到达终态后自动停止：
 * - failed
 * - completed 且 build 模式（开发完成，change_summary 已回传）
 * - completed 且 plan 模式（方案已回传，等待用户批准/驳回）
 *
 * @param taskId 跟踪的任务 ID，undefined 时不轮询
 * @param resumeKey 审批等操作后自增以立即重查并恢复轮询
 */
export function useTaskPlanTracking(
  taskId: string | undefined,
  resumeKey: number,
): UseTaskPlanTrackingState {
  const [task, setTask] = useState<Task>();
  const [loading, setLoading] = useState(false);
  const [orchestratorTodos, setOrchestratorTodos] = useState<OrchestratorTodo[]>([]);
  const [insight, setInsight] = useState<TaskInsight>();
  const [prevTaskId, setPrevTaskId] = useState(taskId);
  const timerRef = useRef<number>();

  // 渲染期状态调整（React 官方推荐模式）：taskId 清空时同步重置，
  // 替代 effect 内同步 setState（react-hooks/set-state-in-effect 禁止）
  if (taskId !== prevTaskId) {
    setPrevTaskId(taskId);
    if (!taskId) {
      setTask(undefined);
      setOrchestratorTodos([]);
      setInsight(undefined);
    }
  }

  const isTerminal = useCallback((current: Task) => {
    if (current.status === 'failed') {
      return true;
    }
    // review（待确认，ADR-038）：方案已回传等待人工批准/驳回，停止轮询
    return current.status === 'completed' || current.status === 'review';
  }, []);

  useEffect(() => {
    if (!taskId) {
      return;
    }

    let cancelled = false;

    const poll = async () => {
      if (cancelled) {
        return;
      }
      setLoading(true);
      try {
        // 任务详情、中枢待办（下段）与只读洞察（M5c）同轮拉取；后两者失败不影响任务轮询
        const [detail, todos, taskInsight] = await Promise.all([
          fetchTaskById(taskId),
          fetchOrchestratorTodos(taskId).catch(() => undefined),
          fetchTaskInsight(taskId).catch(() => undefined),
        ]);
        if (cancelled) {
          return;
        }
        setTask(detail);
        if (todos) {
          setOrchestratorTodos(todos.items);
        }
        if (taskInsight) {
          setInsight(taskInsight);
        }
        if (!isTerminal(detail)) {
          timerRef.current = window.setTimeout(poll, POLL_INTERVAL_MS);
        } else {
          // 终态后任务详情不再变化，但中枢会被 ADR-066 自动唤醒并继续维护
          // 待办（2026-10-08 用户实测：唤醒轮改了待办右侧面板却冻结在终态前）
          // ——切到低频「只拉待办」模式，任务详情与洞察不再重复拉取。
          timerRef.current = window.setTimeout(pollTodosAfterTerminal, POLL_INTERVAL_MS);
        }
      } catch {
        if (!cancelled) {
          // 查询失败不终止轮询，等待下一周期重试
          timerRef.current = window.setTimeout(poll, POLL_INTERVAL_MS);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    const pollTodosAfterTerminal = async () => {
      if (cancelled) {
        return;
      }
      try {
        const todos = await fetchOrchestratorTodos(taskId).catch(() => undefined);
        if (cancelled) {
          return;
        }
        if (todos) {
          setOrchestratorTodos(todos.items);
        }
      } finally {
        if (!cancelled) {
          timerRef.current = window.setTimeout(pollTodosAfterTerminal, POLL_INTERVAL_MS);
        }
      }
    };

    void poll();

    return () => {
      cancelled = true;
      window.clearTimeout(timerRef.current);
    };
  }, [taskId, resumeKey, isTerminal]);

  return { task, loading, orchestratorTodos, insight };
}
