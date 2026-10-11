import { useEffect, useRef, useState } from 'react';
import { fetchSessionOrchestratorTodos } from '@/api/tasks';
import type { OrchestratorTodo } from '@/types/task';

/** 轮询间隔（毫秒），与 useTaskPlanTracking 对齐 */
const POLL_INTERVAL_MS = 5000;

/**
 * 会话维度中枢待办轮询（ADR-067 待办先行）。
 *
 * 任务创建前编排 AI 的待办挂构建会话（todo_write 无任务时挂会话），任务
 * 创建成功后自动迁移归属到任务（届时父组件改用 useTaskPlanTracking）。
 * 本 hook 在「尚无跟踪任务、已有构建会话」阶段拉取会话名下待办，供任务
 * 摘要**下段**展示，避免「待办先行」对用户不可见。
 *
 * 取数失败保持上一次结果（下段是辅助信息，不应因偶发失败清空清单）。
 *
 * @param sessionId 当前构建会话 ID；undefined（会话未创建）时不拉取
 */
export function useSessionOrchestratorTodos(
  sessionId: string | undefined,
): OrchestratorTodo[] {
  const [todos, setTodos] = useState<OrchestratorTodo[]>([]);
  const timerRef = useRef<number>();

  useEffect(() => {
    if (!sessionId) {
      return;
    }

    let cancelled = false;

    const poll = async () => {
      try {
        const data = await fetchSessionOrchestratorTodos(sessionId);
        if (cancelled) {
          return;
        }
        setTodos(data.items);
        timerRef.current = window.setTimeout(poll, POLL_INTERVAL_MS);
      } catch {
        if (!cancelled) {
          // 查询失败保持已有清单，等待下一周期重试
          timerRef.current = window.setTimeout(poll, POLL_INTERVAL_MS);
        }
      }
    };

    void poll();

    return () => {
      cancelled = true;
      window.clearTimeout(timerRef.current);
    };
  }, [sessionId]);

  return todos;
}
