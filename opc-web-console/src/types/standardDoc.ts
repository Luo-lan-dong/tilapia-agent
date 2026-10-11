/**
 * 开发规则文档类型（ADR-048）
 */

export interface StandardDoc {
  id: string;
  name: string;
  category: string | null;
  description: string | null;
  apply_when: string | null;
  version: number;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface StandardDocDetail extends StandardDoc {
  content: string;
}

export interface StandardDocList {
  total: number;
  items: StandardDoc[];
}

export interface StandardDocCreateRequest {
  name: string;
  category?: string | null;
  description?: string | null;
  apply_when?: string | null;
  content: string;
}

export interface StandardDocUpdateRequest {
  category?: string | null;
  description?: string | null;
  apply_when?: string | null;
  content?: string;
}
