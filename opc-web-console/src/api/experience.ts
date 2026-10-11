import { request } from './request';
import type {
  ExperienceListResponse,
  ExperienceItem,
  ExperienceUpdateRequest,
} from '@/types/experience';

/** 经验记忆管理 API（ADR-050）。 */

export function fetchExperienceList(params: {
  keyword?: string;
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<ExperienceListResponse> {
  return request.get<ExperienceListResponse>('/v1/experience', { params });
}

export function updateExperience(
  id: string,
  data: ExperienceUpdateRequest
): Promise<ExperienceItem> {
  return request.patch<ExperienceItem>(`/v1/experience/${id}`, data);
}

export function deleteExperience(id: string): Promise<void> {
  return request.delete<void>(`/v1/experience/${id}`);
}
