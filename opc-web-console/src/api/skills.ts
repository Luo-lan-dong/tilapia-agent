import { request } from './request';
import type {
  AIDispatchSettings,
  SkillDetail,
  SkillSummary,
  SkillUpdateRequest,
} from '@/types/skill';

/**
 * 获取技能列表（ADR-033）
 */
export function fetchSkills(): Promise<SkillSummary[]> {
  return request.get<SkillSummary[]>('/v1/skills');
}

/**
 * 获取技能详情（含完整参数 JSON Schema）
 */
export function fetchSkillDetail(skillKey: string): Promise<SkillDetail> {
  return request.get<SkillDetail>(`/v1/skills/${skillKey}`);
}

/**
 * 更新技能启用开关（ADR-033）
 */
export function updateSkillEnabled(
  skillKey: string,
  data: SkillUpdateRequest,
): Promise<SkillSummary> {
  return request.patch<SkillSummary>(`/v1/skills/${skillKey}`, data);
}

/**
 * 获取 AI 智能调度设置（ADR-033）
 */
export function fetchAIDispatchSettings(): Promise<AIDispatchSettings> {
  return request.get<AIDispatchSettings>('/v1/settings/ai-dispatch');
}

/**
 * 更新 AI 智能调度开关（ADR-033，下一轮调度周期热生效）
 */
export function updateAIDispatchSettings(enabled: boolean): Promise<AIDispatchSettings> {
  return request.put<AIDispatchSettings>('/v1/settings/ai-dispatch', { enabled });
}
