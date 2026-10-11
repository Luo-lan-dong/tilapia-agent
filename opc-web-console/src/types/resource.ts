/**
 * Agent 资源管理类型（ADR-022 / ADR-025）。
 *
 * 字段对齐后端 `app/schemas/resource_settings.py`。
 */

/** 单资源条目信息（本地 vs 云端对比） */
export interface ResourceItemInfo {
  key: string;
  cloud_path: string | null;
  cloud_sha256: string | null;
  cloud_version: string | null;
  size: number | null;
  local_version: string | null;
  local_sha256: string | null;
  present: boolean;
  outdated: boolean;
  selected: boolean;
}

/** 按 agent 类型分组的状态（ADR-025） */
export interface AgentTypeInfo {
  type: string;
  has_update: boolean;
  resources: string[];
}

/** 资源管理状态响应 */
export interface ResourceStatus {
  status: string;
  manifest_url: string | null;
  local_revision: string | null;
  cloud_revision: string | null;
  last_checked_at: string | null;
  last_synced_at: string | null;
  message: string | null;
  resources: ResourceItemInfo[];
  agent_types: AgentTypeInfo[];
}

/** 应用更新 / 导入结果响应 */
export interface ResourceUpdateResult {
  status: string;
  updated_keys: string[];
  message: string | null;
}
