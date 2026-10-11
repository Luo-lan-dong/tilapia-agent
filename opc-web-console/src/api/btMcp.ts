import { request } from './request';
import type {
  BtMcpServerCreateRequest,
  BtMcpServerItem,
  BtMcpServerListResponse,
  BtMcpServerUpdateRequest,
  BtMcpTestResponse,
  BtMcpToolInfo,
  BtMcpToolsResponse,
} from '@/types/btMcp';

/** 宝塔 MCP 服务器管理 API（ADR-049）。 */

export function fetchBtMcpServers(): Promise<BtMcpServerListResponse> {
  return request.get<BtMcpServerListResponse>('/v1/bt-mcp/servers');
}

export function addBtMcpServer(data: BtMcpServerCreateRequest): Promise<BtMcpServerItem> {
  return request.post<BtMcpServerItem>('/v1/bt-mcp/servers', data);
}

export function updateBtMcpServer(
  id: string,
  data: BtMcpServerUpdateRequest
): Promise<BtMcpServerItem> {
  return request.patch<BtMcpServerItem>(`/v1/bt-mcp/servers/${id}`, data);
}

export function deleteBtMcpServer(id: string): Promise<void> {
  return request.delete<void>(`/v1/bt-mcp/servers/${id}`);
}

/** 连接测试（握手 + 拉全量工具清单，放宽超时）。 */
export function testBtMcpServer(id: string): Promise<BtMcpTestResponse> {
  return request.post<BtMcpTestResponse>(
    `/v1/bt-mcp/servers/${id}/test`,
    undefined,
    { timeout: 60000 }
  );
}

export function fetchBtMcpTools(id: string): Promise<BtMcpToolInfo[]> {
  return request
    .get<BtMcpToolsResponse>(`/v1/bt-mcp/servers/${id}/tools`, { timeout: 60000 })
    .then((res) => res.items);
}
