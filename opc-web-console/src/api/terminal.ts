import { request } from './request';

/** 终端 API（ADR-053）：权限模式与命令审批。 */

export function fetchPermissionMode(): Promise<{ mode: string }> {
  return request.get<{ mode: string }>('/v1/terminal/permission-mode');
}

export function updatePermissionMode(mode: string): Promise<{ mode: string }> {
  return request.put<{ mode: string }>('/v1/terminal/permission-mode', { mode });
}

export function decideTerminalApproval(
  approvalId: string,
  approve: boolean
): Promise<void> {
  return request.post<void>(`/v1/terminal/approvals/${approvalId}`, { approve });
}
