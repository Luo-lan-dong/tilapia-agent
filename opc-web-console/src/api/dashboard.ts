import { request } from './request';
import type {
  AlertListResponse,
  AlertReadAllResponse,
  ClusterMetrics,
  DeliverableListResponse,
} from '@/types/dashboard';

/**
 * 仪表盘接口超时（毫秒）。
 *
 * 聚合端点串行多次数据库往返，且开发库为远程 PostgreSQL（网络抖动时
 * 单次往返可达秒级），默认 10s 不够用，单独放宽到 30s。
 */
const DASHBOARD_TIMEOUT_MS = 30000;

/**
 * 查询集群统计指标（仪表盘 8 卡片数据源，ADR-038）
 */
export function fetchClusterMetrics(): Promise<ClusterMetrics> {
  return request.get<ClusterMetrics>('/v1/dashboard/metrics', { timeout: DASHBOARD_TIMEOUT_MS });
}

/**
 * 查询异常告警列表（danger/warning/info 后端推导）
 */
export function fetchAlerts(limit = 50): Promise<AlertListResponse> {
  return request.get<AlertListResponse>('/v1/dashboard/alerts', {
    params: { limit },
    timeout: DASHBOARD_TIMEOUT_MS,
  });
}

/**
 * 告警一键已读：将已读水位线推进到当前时刻
 */
export function markAllAlertsRead(): Promise<AlertReadAllResponse> {
  return request.post<AlertReadAllResponse>('/v1/dashboard/alerts/read-all', null, {
    timeout: DASHBOARD_TIMEOUT_MS,
  });
}

/**
 * 查询交付物聚合列表（最近任务产物缓存清单）
 */
export function fetchDeliverables(limit = 20): Promise<DeliverableListResponse> {
  return request.get<DeliverableListResponse>('/v1/dashboard/deliverables', {
    params: { limit },
    timeout: DASHBOARD_TIMEOUT_MS,
  });
}
