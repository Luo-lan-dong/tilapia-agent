/** 宝塔 MCP 服务器连接类型（ADR-049）。 */

export interface BtMcpServerItem {
  id: string;
  name: string;
  local_url: string;
  public_url: string | null;
  address_mode: 'local' | 'public';
  /** 凭据脱敏：仅返回是否已配置 */
  has_token: boolean;
  has_ca_cert: boolean;
  enabled: boolean;
  remark: string | null;
  created_at: string;
  updated_at: string;
}

export interface BtMcpServerListResponse {
  total: number;
  items: BtMcpServerItem[];
}

export interface BtMcpServerCreateRequest {
  name: string;
  local_url?: string | null;
  public_url?: string | null;
  address_mode?: 'local' | 'public';
  api_token?: string | null;
  ca_cert?: string | null;
  enabled?: boolean;
  remark?: string | null;
  /** 宝塔插件生成的接入 JSON（一键导入） */
  mcp_info?: Record<string, unknown> | null;
}

export interface BtMcpServerUpdateRequest {
  name?: string;
  local_url?: string | null;
  public_url?: string | null;
  address_mode?: 'local' | 'public';
  /** 缺省保留现值；空串清除 */
  api_token?: string | null;
  ca_cert?: string | null;
  enabled?: boolean;
  remark?: string | null;
  mcp_info?: Record<string, unknown> | null;
}

export interface BtMcpTestResponse {
  success: boolean;
  message: string;
  server_info: { name?: string; version?: string } | null;
  tool_count: number;
  tool_summary: Record<string, number> | null;
}

export interface BtMcpToolInfo {
  name: string;
  description: string;
  category: string;
  risk: 'low' | 'medium' | 'high';
}

export interface BtMcpToolsResponse {
  total: number;
  items: BtMcpToolInfo[];
}
