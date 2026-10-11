/**
 * 日志查询 API（ADR-047）。
 */

import { request } from './request';
import type { AgentLogFetchResponse, LogsResponse, TraceResponse } from '@/types/logs';

export interface LogsQueryParams {
  start?: string;
  end?: string;
  level?: string[];
  keyword?: string;
  source?: 'memory' | 'file';
  limit?: number;
}

/** 查询运行日志（默认内存环形缓冲最近 1 小时） */
export async function fetchLogs(params: LogsQueryParams): Promise<LogsResponse> {
  return request.get<LogsResponse>('/v1/logs', { params });
}

/** 溯源执行链路；均不传时返回最近事件列表（mode=recent，时间倒序） */
export async function fetchTrace(params: {
  request_id?: string;
  session_id?: string;
  limit?: number;
}): Promise<TraceResponse> {
  return request.get<TraceResponse>('/v1/logs/trace', { params });
}

/** 按需拉取节点本地日志（中枢下发指令，同步等待回传） */
export async function fetchAgentLogs(body: {
  client_id: string;
  tail_lines?: number;
  level?: string | null;
}): Promise<AgentLogFetchResponse> {
  // 节点回传等待最长 45s，超时上限放宽到 60s
  return request.post<AgentLogFetchResponse>('/v1/logs/agent-fetch', body, {
    timeout: 60000,
  });
}
