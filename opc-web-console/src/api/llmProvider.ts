import { request } from './request';
import type {
  LLMModelCreateRequest,
  LLMModelItem,
  LLMModelUpdateRequest,
  LLMProviderConfig,
  LLMProviderUpdateRequest,
} from '@/types/llmProvider';

/**
 * 获取当前 LLM Provider 配置
 */
export function fetchLLMProviderConfig(): Promise<LLMProviderConfig> {
  return request.get<LLMProviderConfig>('/v1/settings/llm');
}

/**
 * 更新当前 LLM Provider 配置
 */
export function updateLLMProviderConfig(
  data: LLMProviderUpdateRequest,
): Promise<LLMProviderConfig> {
  return request.patch<LLMProviderConfig>('/v1/settings/llm', data);
}

/**
 * 获取自定义模型列表（ADR-030）
 */
export function fetchLLMModels(): Promise<LLMModelItem[]> {
  return request.get<LLMModelItem[]>('/v1/settings/llm/models');
}

/**
 * 添加自定义 OpenAI 兼容模型（ADR-030）
 */
export function addLLMModel(data: LLMModelCreateRequest): Promise<LLMModelItem> {
  return request.post<LLMModelItem>('/v1/settings/llm/models', data);
}

/**
 * 编辑模型元数据（窗口大小/视觉能力，ADR-041）
 */
export function updateLLMModel(id: string, data: LLMModelUpdateRequest): Promise<LLMModelItem> {
  return request.patch<LLMModelItem>(`/v1/settings/llm/models/${id}`, data);
}

/**
 * 设为使用中模型（ADR-030，热生效）
 */
export function activateLLMModel(id: string): Promise<LLMModelItem> {
  return request.put<LLMModelItem>(`/v1/settings/llm/models/${id}/activate`);
}

/**
 * 删除自定义模型（使用中会 409）
 */
export function deleteLLMModel(id: string): Promise<void> {
  return request.delete<void>(`/v1/settings/llm/models/${id}`);
}
