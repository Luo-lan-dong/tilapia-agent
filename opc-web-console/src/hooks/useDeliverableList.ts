import { fetchDeliverables } from '@/api/dashboard';
import { usePolling } from './usePolling';
import type { DeliverableListResponse } from '@/types/dashboard';

/** 默认轮询间隔：60 秒（产物清单变化频率低，且聚合查询较重） */
const DEFAULT_INTERVAL_MS = 60000;

/**
 * useDeliverableList 返回值
 */
export interface UseDeliverableListResult {
  /** 交付物列表 */
  items: DeliverableListResponse['items'];
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
 * 交付物聚合列表轮询 Hook（ADR-038）。
 *
 * 组件挂载时立即请求，默认每 60 秒刷新一次。
 */
export function useDeliverableList(interval?: number): UseDeliverableListResult {
  const { data, loading, error, refresh } = usePolling<DeliverableListResponse>({
    fetcher: () => fetchDeliverables(),
    interval: interval ?? DEFAULT_INTERVAL_MS,
  });
  return {
    items: data?.items ?? [],
    total: data?.total ?? 0,
    loading,
    error,
    refresh,
  };
}
