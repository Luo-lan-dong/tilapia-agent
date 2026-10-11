import { request } from './request';
import type { ResourceStatus, ResourceUpdateResult } from '@/types/resource';

/**
 * 获取资源同步状态。
 */
export function fetchResourceStatus(): Promise<ResourceStatus> {
  return request.get<ResourceStatus>('/v1/settings/resources');
}

/**
 * 立即检查云端更新（仅比对版本，不下载；地址自动从管理后台获取，ADR-025）。
 */
export function checkResources(): Promise<ResourceStatus> {
  return request.post<ResourceStatus>('/v1/settings/resources/check');
}

/**
 * 应用更新（ADR-025）：type=按 agent 类型更新该类型全部条目（脚本随整体包自动更新）；
 * keys=按条目更新；均缺省=全部过时资源。
 */
export function applyResourceUpdateByType(type: string): Promise<ResourceUpdateResult> {
  return request.post<ResourceUpdateResult>('/v1/settings/resources/update', { type });
}

export function applyResourceUpdateByKeys(keys?: string[]): Promise<ResourceUpdateResult> {
  if (!keys || keys.length === 0) {
    return request.post<ResourceUpdateResult>('/v1/settings/resources/update', {});
  }
  return request.post<ResourceUpdateResult>('/v1/settings/resources/update', { keys });
}

/**
 * 导入资源包 zip（离线兜底）。
 */
export function importResourceZip(file: File): Promise<ResourceUpdateResult> {
  const formData = new FormData();
  formData.append('file', file);
  return request.post<ResourceUpdateResult>('/v1/settings/resources/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
}
