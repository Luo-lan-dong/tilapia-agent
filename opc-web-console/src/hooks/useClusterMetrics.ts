import { fetchClusterMetrics } from '@/api/dashboard';
import { usePolling } from './usePolling';
import type { ClusterMetrics } from '@/types/dashboard';

/** 默认轮询间隔：30 秒（仪表盘聚合指标变化频率低于任务列表） */
const DEFAULT_INTERVAL_MS = 30000;

/**
 * useClusterMetrics 返回值
 */
export interface UseClusterMetricsResult {
  /** 集群统计指标 */
  data: ClusterMetrics | null;
  /** 是否加载中 */
  loading: boolean;
  /** 请求错误 */
  error: Error | null;
  /** 手动刷新 */
  refresh: () => void;
}

/**
 * 集群统计指标轮询 Hook（ADR-038）。
 *
 * 组件挂载时立即请求，默认每 30 秒刷新一次。
 */
export function useClusterMetrics(interval?: number): UseClusterMetricsResult {
  const { data, loading, error, refresh } = usePolling<ClusterMetrics>({
    fetcher: () => fetchClusterMetrics(),
    interval: interval ?? DEFAULT_INTERVAL_MS,
  });
  return { data, loading, error, refresh };
}
