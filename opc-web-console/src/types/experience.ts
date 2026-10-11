/** 经验记忆条目类型（ADR-050）。 */

export type ExperienceStatus = 'pending' | 'resolved';

export interface ExperienceItem {
  id: string;
  tool: string;
  error_signature: string;
  problem: string | null;
  root_cause: string | null;
  solution: string | null;
  status: ExperienceStatus;
  session_id: string | null;
  hit_count: number;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExperienceListResponse {
  total: number;
  page: number;
  page_size: number;
  items: ExperienceItem[];
}

export interface ExperienceUpdateRequest {
  tool?: string;
  error_signature?: string;
  problem?: string;
  root_cause?: string;
  solution?: string;
  status?: ExperienceStatus;
}
