import { request } from './request';
import type {
  StandardDocCreateRequest,
  StandardDocDetail,
  StandardDocList,
  StandardDocUpdateRequest,
} from '@/types/standardDoc';

/**
 * 获取规则文档列表（ADR-048，元数据不含正文）
 */
export function fetchStandardDocs(): Promise<StandardDocList> {
  return request.get<StandardDocList>('/v1/docs');
}

/**
 * 获取规则文档详情（含 Markdown 全文）
 */
export function fetchStandardDocDetail(docId: string): Promise<StandardDocDetail> {
  return request.get<StandardDocDetail>(`/v1/docs/${docId}`);
}

/**
 * 创建规则文档（上传 .md 由前端读文本后调用本接口）
 */
export function createStandardDoc(data: StandardDocCreateRequest): Promise<StandardDocDetail> {
  return request.post<StandardDocDetail>('/v1/docs', data);
}

/**
 * 更新规则文档（name 不可改，version 自增；已建任务使用创建时快照）
 */
export function updateStandardDoc(
  docId: string,
  data: StandardDocUpdateRequest,
): Promise<StandardDocDetail> {
  return request.put<StandardDocDetail>(`/v1/docs/${docId}`, data);
}

/**
 * 删除规则文档（已建任务的快照不受影响）
 */
export function deleteStandardDoc(docId: string): Promise<null> {
  return request.delete<null>(`/v1/docs/${docId}`);
}
