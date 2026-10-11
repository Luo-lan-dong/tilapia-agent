/** SOP 知识条目类型（ADR-051）。 */

export interface SopItem {
  id: string;
  name: string;
  scene: string;
  content: string;
  source: 'builtin' | 'learned';
  enabled: boolean;
  hit_count: number;
  created_at: string;
  updated_at: string;
}

export interface SopListResponse {
  total: number;
  page: number;
  page_size: number;
  items: SopItem[];
}

export interface SopCreateRequest {
  name: string;
  scene: string;
  content: string;
  source?: string;
  enabled?: boolean;
}

export interface SopUpdateRequest {
  name?: string;
  scene?: string;
  content?: string;
  enabled?: boolean;
}
