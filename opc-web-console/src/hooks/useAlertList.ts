import { useCallback, useState } from 'react';
import { fetchAlerts, markAllAlertsRead } from '@/api/dashboard';
import { usePolling } from './usePolling';
import type { AlertListResponse } from '@/types/dashboard';

/** 默认轮询间隔：30 秒 */
const DEFAULT_INTERVAL_MS = 30000;

/**
 * useAlertList 返回值
 */
export interface UseAlertListResult {
  /** 告警列表 */
  items: AlertListResponse['items'];
  /** 告警总数（未读，角标用） */
  total: number;
  /** 是否加载中 */
  loading: boolean;
  /** 请求错误 */
  error: Error | null;
  /** 手动刷新 */
  refresh: () => void;
  /** 一键已读（进行中时为 true） */
  markingRead: boolean;
  /** 一键已读：推进已读水位线并刷新列表 */
  markAllRead: () => Promise<void>;
}

/**
 * 异常告警列表轮询 Hook（ADR-038）。
 *
 * 组件挂载时立即请求，默认每 30 秒刷新一次；
 * 支持一键已读（水位线持久化在后端，跨浏览器生效）。
 */
export function useAlertList(interval?: number): UseAlertListResult {
  const [markingRead, setMarkingRead] = useState(false);
  const { data, loading, error, refresh } = usePolling<AlertListResponse>({
    fetcher: () => fetchAlerts(),
    interval: interval ?? DEFAULT_INTERVAL_MS,
  });

  const markAllRead = useCallback(async () => {
    setMarkingRead(true);
    try {
      await markAllAlertsRead();
      refresh();
    } finally {
      setMarkingRead(false);
    }
  }, [refresh]);

  return {
    items: data?.items ?? [],
    total: data?.total ?? 0,
    loading,
    error,
    refresh,
    markingRead,
    markAllRead,
  };
}
