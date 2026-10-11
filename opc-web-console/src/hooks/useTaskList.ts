import { useMemo } from 'react';
import { fetchTasks } from '@/api/tasks';
import { usePolling } from './usePolling';
import type { Task, TaskListParams, TaskListResponse } from '@/types/task';

/**
 * useTaskList 配置项
 */
export interface UseTaskListOptions extends TaskListParams {
  /** 轮询间隔，单位毫秒，默认 5000ms */
  interval?: number;
}

/**
 * useTaskList 返回值
 */
export interface UseTaskListResult {
  /** 任务列表 */
  data: Task[];
  /** 总数 */
  total: number;
  /** 是否加载中 */
  loading: boolean;
  /** 请求错误 */
  error: Error | null;
  /** 手动刷新 */
  refresh: () => void;
}

/**
 * 自动轮询任务列表 Hook。
 *
 * 组件挂载时立即请求，默认每 5 秒刷新一次；
 * 筛选条件变化时自动重新请求并清理旧定时器。
 */
export function useTaskList(options: UseTaskListOptions = {}): UseTaskListResult {
  const { interval, status, agent_type, keyword, limit, offset } = options;

  // 用基本类型作为依赖，避免每次渲染 options 对象引用变化导致 fetcher 重建，
  // 进而触发 usePolling 的 effect 无限重执行
  const fetcher = useMemo(() => {
    const params: TaskListParams = { status, agent_type, keyword, limit, offset };
    return () => fetchTasks(params);
  }, [status, agent_type, keyword, limit, offset]);

  const { data, loading, error, refresh } = usePolling<TaskListResponse>({
    fetcher,
    interval,
  });

  return {
    data: data?.items ?? [],
    total: data?.pagination?.total ?? 0,
    loading,
    error,
    refresh,
  };
}
