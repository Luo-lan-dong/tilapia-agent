/**
 * 集群仪表盘聚合接口类型（ADR-038）
 */

/**
 * 集群统计指标（8 统计卡数据源）
 */
export interface ClusterMetrics {
  total_tasks: number;
  running_tasks: number;
  completed_tasks: number;
  queued_tasks: number;
  online_nodes: number;
  total_nodes: number;
  /** 集群负载（在线节点 CPU 均值 %），无快照时为 null */
  cluster_load_percent: number | null;
  alert_count: number;
  /** 今日 token 消耗（当前仅统计中枢侧） */
  token_consumed_today: number;
}

/**
 * 告警级别：danger 红 / warning 琥珀 / info 蓝
 */
export type AlertLevel = 'danger' | 'warning' | 'info';

/**
 * 告警条目
 */
export interface AlertItem {
  id: string;
  level: AlertLevel;
  title: string;
  description: string;
  task_id?: string | null;
  node_id?: string | null;
  created_at: string;
}

/**
 * 告警列表响应
 */
export interface AlertListResponse {
  total: number;
  items: AlertItem[];
}

/**
 * 告警一键已读响应
 */
export interface AlertReadAllResponse {
  /** 写入的已读水位线（UTC） */
  read_at: string;
}

/**
 * 交付物文件类型（按扩展名映射）
 */
export type DeliverableFileType = 'image' | 'video' | 'zip' | 'doc' | 'code' | 'other';

/**
 * 交付物条目
 */
export interface DeliverableItem {
  task_id: string;
  task_title: string;
  node_name?: string | null;
  path: string;
  file_name: string;
  file_type: DeliverableFileType;
  size: number;
  mtime: number;
}

/**
 * 交付物聚合列表响应
 */
export interface DeliverableListResponse {
  total: number;
  items: DeliverableItem[];
}
