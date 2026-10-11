import { request } from './request';
import type { HubUpdateCheckResult, ReleasesData } from '@/types/release';

/**
 * 获取产品版本发布列表（按产品线分组，云端下发，ADR-026）。
 *
 * 版本服务不可达时返回 { products: [], error }（HTTP 仍为 200，业务降级）。
 */
export function fetchReleases(): Promise<ReleasesData> {
  return request.get<ReleasesData>('/v1/settings/releases');
}

/**
 * 检查中枢更新（比对云端 hub 最新版本与本地当前版本）。
 */
export function checkHubUpdate(): Promise<HubUpdateCheckResult> {
  return request.post<HubUpdateCheckResult>('/v1/settings/check-hub-update');
}
