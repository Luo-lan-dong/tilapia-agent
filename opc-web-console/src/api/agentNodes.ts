import { request } from './request';
import type {
  AgentNode,
  AgentNodeListParams,
  AgentNodeListResponse,
  AgentNodeMetricsResponse,
} from '@/types/agentNode';
import type { InstallScriptInfo } from '@/types/installScript';

/**
 * 分页查询 Agent 节点列表
 */
export function fetchAgentNodes(params?: AgentNodeListParams): Promise<AgentNodeListResponse> {
  return request.get<AgentNodeListResponse>('/v1/agent-nodes', { params });
}

/**
 * 根据 ID 查询单个 Agent 节点详情
 */
export function getAgentNodeById(id: string): Promise<AgentNode> {
  return request.get<AgentNode>(`/v1/agent-nodes/${id}`);
}

/**
 * 获取指定类型和操作系统的安装脚本元数据
 */
export function getInstallScript(params: {
  agent_type: string;
  os: string;
}): Promise<InstallScriptInfo> {
  return request.get<InstallScriptInfo>('/v1/agent-nodes/install-scripts', { params });
}

/**
 * 重新生成指定 Agent 节点的注册 Token
 */
export function resetAgentNodeToken(id: string): Promise<{ registration_token: string }> {
  return request.post<{ registration_token: string }>(`/v1/agent-nodes/${id}/token`);
}

/**
 * 查询指定 Agent 节点的实时系统资源监控详情
 */
export function getAgentNodeMetrics(id: string): Promise<AgentNodeMetricsResponse> {
  return request.get<AgentNodeMetricsResponse>(`/v1/agent-nodes/${id}/metrics`);
}

/**
 * 更新 Agent 节点显示名（ADR-016）。
 *
 * 仅允许修改 ``name`` 字段；``agent_type`` / ``client_id`` 由后端 Pydantic
 * schema 直接屏蔽，传入也不会被采纳。
 */
export function updateAgentNodeName(
  id: string,
  name: string
): Promise<AgentNode> {
  return request.patch<AgentNode>(`/v1/agent-nodes/${id}`, { name });
}
