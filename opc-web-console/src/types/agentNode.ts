import type { PaginationMeta } from './common';

/**
 * Agent 节点状态枚举
 */
export type NodeStatus = 'offline' | 'starting' | 'idle' | 'busy' | 'error';

/**
 * Agent 节点模型
 */
export interface AgentNode {
  id: string;
  name: string;
  agent_type: string;
  client_id?: string;
  capabilities?: string[];
  status: NodeStatus;
  load: number;
  last_heartbeat_at?: string;
  /** 节点 IP（WebSocket 连接来源优先，ADR-035 自报地址兜底） */
  reported_ip?: string | null;
  registration_token?: string;
  created_at: string;
  updated_at: string;
}

/**
 * 节点列表查询参数
 */
export interface AgentNodeListParams {
  status?: NodeStatus;
  agent_type?: string;
  limit?: number;
  offset?: number;
}

/**
 * CPU 监控指标
 */
export interface CpuMetrics {
  percent: number;
  count_physical?: number;
  count_logical?: number;
  per_core?: number[];
}

/**
 * 内存监控指标
 */
export interface MemoryMetrics {
  percent: number;
  total_gb?: number;
  used_gb?: number;
  available_gb?: number;
  swap_percent?: number;
  swap_used_gb?: number;
  swap_total_gb?: number;
}

/**
 * 磁盘监控指标
 */
export interface DiskMetrics {
  percent: number;
  total_gb?: number;
  used_gb?: number;
  free_gb?: number;
}

/**
 * 网络监控指标
 */
export interface NetworkMetrics {
  bytes_sent: number;
  bytes_recv: number;
  packets_sent?: number;
  packets_recv?: number;
  mb_sent?: number;
  mb_recv?: number;
  send_speed_mb?: number;
  recv_speed_mb?: number;
  total_mb_sent?: number;
  total_mb_recv?: number;
}

/**
 * 系统平均负载
 */
export interface LoadAverageMetrics {
  '1m': number;
  '5m': number;
  '15m': number;
}

/**
 * 综合负载得分
 */
export interface LoadScoreMetrics {
  score: number;
  level: string;
  weights?: Record<string, number>;
  breakdown?: Record<string, { score: number; weight: number }>;
}

/**
 * Agent 节点系统资源监控快照
 */
export interface AgentNodeMetrics {
  hostname?: string;
  platform?: string;
  timestamp?: string;
  cpu?: CpuMetrics;
  memory?: MemoryMetrics;
  disk?: DiskMetrics;
  network?: NetworkMetrics;
  load_average?: LoadAverageMetrics;
  load_score?: LoadScoreMetrics;
}

/**
 * 节点监控详情响应
 */
export interface AgentNodeMetricsResponse {
  node_id: string;
  is_online: boolean;
  last_heartbeat_at?: string;
  metrics?: AgentNodeMetrics;
}

/**
 * 节点列表响应
 */
export interface AgentNodeListResponse {
  items: AgentNode[];
  pagination: PaginationMeta;
}
