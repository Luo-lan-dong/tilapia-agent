import { request } from './request';
import type { SopItem, SopListResponse, SopCreateRequest, SopUpdateRequest } from '@/types/sop';

/** SOP 知识管理 API（ADR-051）。 */

export function fetchSopList(params: {
  keyword?: string;
  scene?: string;
  source?: string;
  page?: number;
  page_size?: number;
}): Promise<SopListResponse> {
  return request.get<SopListResponse>('/v1/sops', { params });
}

export function createSop(data: SopCreateRequest): Promise<SopItem> {
  return request.post<SopItem>('/v1/sops', data);
}

export function updateSop(id: string, data: SopUpdateRequest): Promise<SopItem> {
  return request.patch<SopItem>(`/v1/sops/${id}`, data);
}

export function deleteSop(id: string): Promise<void> {
  return request.delete<void>(`/v1/sops/${id}`);
}
