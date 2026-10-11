import { useCallback, useEffect, useState } from 'react';
import { App } from 'antd';
import { getAgentNodeById, getAgentNodeMetrics } from '@/api/agentNodes';
import type { AgentNode, AgentNodeMetricsResponse } from '@/types/agentNode';
import { usePolling } from '@/hooks/usePolling';

/** antd message 实例类型（App.useApp() 消费，避免静态方法警告） */
type MessageInstance = ReturnType<typeof App.useApp>['message'];

/**
 * 监控数据轮询间隔（毫秒）。
 *
 * 与 Agent 心跳间隔（默认 5 秒）保持一致，确保页面展示的数据
 * 与数据库最新快照同步。
 */
const METRICS_POLL_INTERVAL_MS = 5000;

interface UseNodeMetricsPollingResult {
  /** 节点详情 */
  node: AgentNode | null;
  /** 监控快照数据 */
  metricsData: AgentNodeMetricsResponse | null;
  /** 节点详情加载中 */
  nodeLoading: boolean;
  /** 监控数据加载中（含轮询刷新） */
  metricsLoading: boolean;
  /** 最近一次监控刷新时间（本地时区字符串） */
  lastUpdatedAt: string;
  /** 手动触发一次监控刷新 */
  refreshMetrics: () => void;
}

/**
 * 节点监控数据轮询（从 NodeMetrics 页面拆出）。
 *
 * 节点详情挂载时加载一次；监控快照复用通用 usePolling 以固定间隔轮询，
 * 并由 usePolling 统一提供 AbortController 取消、切后台暂停、切前台刷新能力，
 * 本 hook 不再自行实现定时器与请求取消。
 */
export function useNodeMetricsPolling(
  nodeId: string | undefined,
  message: MessageInstance,
): UseNodeMetricsPollingResult {
  const [node, setNode] = useState<AgentNode | null>(null);
  const [nodeLoading, setNodeLoading] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string>('');

  // ---- 监控快照轮询：复用通用 usePolling，避免重复实现轮询逻辑 ----
  const fetchMetrics = useCallback(async (): Promise<AgentNodeMetricsResponse | null> => {
    if (!nodeId) {
      return null;
    }
    const data = await getAgentNodeMetrics(nodeId);
    // 每次拉取成功即更新「最近刷新时间」（在 fetcher 回调中同步，避免 effect 内 setState）
    setLastUpdatedAt(new Date().toLocaleString('zh-CN'));
    return data;
  }, [nodeId]);

  const {
    data: metricsData,
    loading: metricsLoading,
    error: metricsError,
    refresh: refreshMetrics,
  } = usePolling<AgentNodeMetricsResponse | null>({
    fetcher: fetchMetrics,
    interval: METRICS_POLL_INTERVAL_MS,
  });

  // usePolling 仅收集错误不弹窗，统一在此经 antd message 提示
  useEffect(() => {
    if (metricsError) {
      message.error(metricsError.message || '加载监控数据失败');
    }
  }, [metricsError, message]);

  // ---- 节点详情：挂载后加载一次（非轮询） ----
  const loadNode = useCallback(async () => {
    if (!nodeId) {
      return;
    }
    setNodeLoading(true);
    try {
      setNode(await getAgentNodeById(nodeId));
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '加载节点信息失败';
      message.error(errorMessage);
    } finally {
      setNodeLoading(false);
    }
  }, [nodeId, message]);

  useEffect(() => {
    // 延迟到 effect 结束后触发，避免在 effect 内同步 setState（react-hooks/set-state-in-effect）
    const timeoutId = setTimeout(() => {
      void loadNode();
    }, 0);
    return () => {
      clearTimeout(timeoutId);
    };
  }, [loadNode]);

  return { node, metricsData, nodeLoading, metricsLoading, lastUpdatedAt, refreshMetrics };
}
