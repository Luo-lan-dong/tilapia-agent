import { useCallback, useState } from 'react';
import {
  createTaskBuildingSession,
  deleteTaskBuildingSession,
  fetchTaskBuildingSessions,
  updateTaskBuildingSession,
} from '@/api/taskBuildingSessions';
import type {
  ChatMessage,
  TaskBuildMode,
  TaskBuildingSession,
  TaskBuildingSessionSummary,
  TaskSpecification,
} from '@/types/taskBuilding';

export interface UseTaskBuildingSessionsState {
  /** 轻量列表项（不含 messages/specification 大字段） */
  sessions: TaskBuildingSessionSummary[];
  loading: boolean;
  error: string | null;
}

export interface UseTaskBuildingSessionsActions {
  refresh: () => Promise<void>;
  createSession: (
    title: string,
    messages: ChatMessage[],
    specification: TaskSpecification,
    mode: TaskBuildMode,
    model: string,
    taskId?: string,
  ) => Promise<TaskBuildingSession>;
  updateSession: (
    id: string,
    updates: {
      messages?: ChatMessage[];
      specification?: TaskSpecification;
      mode?: TaskBuildMode;
      model?: string;
      taskId?: string;
    },
  ) => Promise<TaskBuildingSession>;
  removeSession: (id: string) => Promise<void>;
}

/**
 * 管理任务构建会话列表的查询与变更。
 */
export function useTaskBuildingSessions(): [
  UseTaskBuildingSessionsState,
  UseTaskBuildingSessionsActions,
] {
  const [sessions, setSessions] = useState<TaskBuildingSessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchTaskBuildingSessions();
      setSessions(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载历史会话失败');
    } finally {
      setLoading(false);
    }
  }, []);

  const createSession = useCallback(
    async (
      title: string,
      messages: ChatMessage[],
      specification: TaskSpecification,
      mode: TaskBuildMode,
      model: string,
      taskId?: string,
    ): Promise<TaskBuildingSession> => {
      const session = await createTaskBuildingSession({
        title,
        messages,
        specification,
        mode,
        model,
        task_id: taskId,
      });
      setSessions((prev) => [session, ...prev]);
      return session;
    },
    [],
  );

  const updateSession = useCallback(
    async (
      id: string,
      updates: {
        messages?: ChatMessage[];
        specification?: TaskSpecification;
        mode?: TaskBuildMode;
        model?: string;
        taskId?: string;
      },
    ): Promise<TaskBuildingSession> => {
      const session = await updateTaskBuildingSession(id, {
        messages: updates.messages,
        specification: updates.specification,
        mode: updates.mode,
        model: updates.model,
        task_id: updates.taskId,
      });
      setSessions((prev) => prev.map((s) => (s.id === id ? session : s)));
      return session;
    },
    [],
  );

  const removeSession = useCallback(async (id: string): Promise<void> => {
    await deleteTaskBuildingSession(id);
    setSessions((prev) => prev.filter((s) => s.id !== id));
  }, []);

  return [
    { sessions, loading, error },
    { refresh, createSession, updateSession, removeSession },
  ];
}
