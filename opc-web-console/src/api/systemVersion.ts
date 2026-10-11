import { request } from './request';
import type { SystemVersion } from '@/types/systemVersion';

/**
 * 获取系统版本列表（按发布日期倒序）
 */
export function fetchSystemVersions(): Promise<SystemVersion[]> {
  return request.get<SystemVersion[]>('/v1/settings/versions');
}
