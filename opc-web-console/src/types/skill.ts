/**
 * 中枢技能相关类型（ADR-033）。
 */

/**
 * 技能列表项。
 */
export interface SkillSummary {
  skill_key: string;
  name: string;
  description: string;
  parameters_summary: string;
  returns: string;
  enabled: boolean;
}

/**
 * 技能详情（含完整参数 JSON Schema）。
 */
export interface SkillDetail extends SkillSummary {
  parameters: Record<string, unknown>;
}

/**
 * 技能启用开关更新请求。
 */
export interface SkillUpdateRequest {
  enabled: boolean;
}

/**
 * AI 智能调度设置。
 */
export interface AIDispatchSettings {
  enabled: boolean;
  timeout_seconds: number;
  max_tool_rounds: number;
}
