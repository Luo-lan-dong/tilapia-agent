/**
 * 日志系统类型定义（ADR-047）。
 */

/** 运行日志条目（内存环形缓冲 / 文件尾部） */
export interface LogEntry {
  timestamp: string | null;
  level: string | null;
  logger: string | null;
  message: string | null;
  request_id: string | null;
  metadata: Record<string, unknown>;
}

/** 运行日志查询响应 */
export interface LogsResponse {
  entries: LogEntry[];
  total: number;
  source: string;
}

/** 执行链路事件（tool_call_logs） */
export interface TraceEvent {
  id: string;
  request_id: string | null;
  session_id: string | null;
  task_id: string | null;
  kind: string;
  name: string;
  status: string;
  args_summary: string | null;
  result_summary: string | null;
  error: string | null;
  duration_ms: number | null;
  created_at: string | null;
}

/** 执行链路查询响应（mode=recent 最近事件概览 / mode=trace 单链溯源瀑布） */
export interface TraceResponse {
  events: TraceEvent[];
  total: number;
  mode: 'recent' | 'trace';
}

/** 节点日志拉取响应 */
export interface AgentLogFetchResponse {
  client_id: string;
  content: string;
}
