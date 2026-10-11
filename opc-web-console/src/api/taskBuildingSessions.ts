import { request } from './request';
import type {
  TaskBuildingSession,
  TaskBuildingSessionCreateRequest,
  TaskBuildingSessionListResponse,
  TaskBuildingSessionUpdateRequest,
} from '@/types/taskBuilding';

/**
 * 创建任务构建会话。
 */
export function createTaskBuildingSession(
  data: TaskBuildingSessionCreateRequest,
): Promise<TaskBuildingSession> {
  return request.post<TaskBuildingSession>('/v1/tasks/build/sessions', data);
}

/**
 * 查询任务构建会话列表。
 */
export function fetchTaskBuildingSessions(
  limit = 100,
  offset = 0,
): Promise<TaskBuildingSessionListResponse> {
  return request.get<TaskBuildingSessionListResponse>('/v1/tasks/build/sessions', {
    params: { limit, offset },
  });
}

/**
 * 根据 ID 查询任务构建会话详情。
 */
export function fetchTaskBuildingSessionById(id: string): Promise<TaskBuildingSession> {
  return request.get<TaskBuildingSession>(`/v1/tasks/build/sessions/${id}`);
}

/**
 * 更新任务构建会话。
 */
export function updateTaskBuildingSession(
  id: string,
  data: TaskBuildingSessionUpdateRequest,
): Promise<TaskBuildingSession> {
  return request.patch<TaskBuildingSession>(`/v1/tasks/build/sessions/${id}`, data);
}

/**
 * 删除任务构建会话。
 */
export function deleteTaskBuildingSession(id: string): Promise<void> {
  return request.delete<void>(`/v1/tasks/build/sessions/${id}`);
}
