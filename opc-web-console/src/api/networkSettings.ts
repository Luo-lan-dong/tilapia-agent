import { request } from './request';
import type {
  NetworkSettings,
  NetworkSettingsUpdateRequest,
} from '@/types/networkSettings';

/**
 * 获取当前网络配置
 */
export function fetchNetworkSettings(): Promise<NetworkSettings> {
  return request.get<NetworkSettings>('/v1/settings/network');
}

/**
 * 更新网络配置
 */
export function updateNetworkSettings(
  data: NetworkSettingsUpdateRequest,
): Promise<NetworkSettings> {
  return request.patch<NetworkSettings>('/v1/settings/network', data);
}
