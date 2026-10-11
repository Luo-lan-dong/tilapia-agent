import { request } from './request';
import type {
  AgentTraceListResponse,
  OrchestratorTodoListResponse,
  Task,
  TaskCreateRequest,
  TaskCreateResponse,
  TaskExecutionMode,
  TaskInsight,
  TaskListParams,
  TaskListResponse,
} from '@/types/task';

/**
 * 分页查询任务列表
 */
export function fetchTasks(params?: TaskListParams): Promise<TaskListResponse> {
  return request.get<TaskListResponse>('/v1/tasks', { params });
}

/**
 * 创建任务
 */
export function createTask(data: TaskCreateRequest): Promise<TaskCreateResponse> {
  return request.post<TaskCreateResponse>('/v1/tasks', data);
}

/**
 * 根据来源过滤任务列表（MVP 用于筛选 spec_chat 任务）。
 */
export function fetchTasksBySource(source: string): Promise<TaskListResponse> {
  return request.get<TaskListResponse>('/v1/tasks', { params: { source } });
}

/**
 * 根据 ID 查询任务详情
 */
export function fetchTaskById(id: string): Promise<Task> {
  return request.get<Task>(`/v1/tasks/${id}`);
}

/**
 * 派发/重新入队响应。
 *
 * status 为重新入队后的任务状态；dispatch_type 标识本次派发模式
 * （manual 表示用户手动，auto 表示调度器自动，ADR-017）。
 * queued_on_node / hold_reason 用于 ADR-059：目标节点未达准入水位时改为排队等待。
 */
export interface TaskDispatchResponse {
  status: string;
  dispatch_type?: 'auto' | 'manual';
  /** 目标节点未达标时的排队等待对象（节点名）；正常派送时为 null/undefined */
  queued_on_node?: string | null;
  /** 排队原因（含缺口数值）；正常派送时为 null/undefined */
  hold_reason?: string | null;
}

/**
 * 手动派发任务到指定子系统。
 *
 * mode 用于派发时指定执行模式（plan/build），缺省由后端按任务当前模式处理；
 * nodeId 非空时任务直接指派给该节点（ADR-017 增强），target 不再必填；
 * force 为真时才允许越过 ADR-059 准入门槛（否则目标节点不达标则排队等待）。
 */
export function dispatchTask(
  taskId: string,
  target: string,
  mode?: TaskExecutionMode,
  nodeId?: string,
  force = false,
): Promise<TaskDispatchResponse> {
  return request.post<TaskDispatchResponse>(`/v1/tasks/${taskId}/dispatch`, {
    target,
    mode,
    node_id: nodeId,
    force,
  });
}

/**
 * 设置派发偏好（ADR-059 D2）：指定目标节点 / 要求强行派送。
 *
 * targetNodeId 非空 → 任务排队只等该节点（不经 AI，达标当轮派送）；
 * targetNodeId 为 null → 清除指定，回到常规预筛；
 * force 为真 → 唯一允许越过准入门槛的开关（落库 forced_dispatch 供审计）。
 * failed 任务可借此改派（复用既有文档与产物，不重新生成三件套）。
 */
export function setDispatchPreference(
  taskId: string,
  targetNodeId: string | null,
  force = false,
): Promise<Task> {
  return request.put<Task>(`/v1/tasks/${taskId}/dispatch-preference`, {
    target_node_id: targetNodeId,
    force,
  });
}

/** 单个候选节点的准入明细（ADR-059 D3）。 */
export interface TaskEligibilityCandidate {
  node_id: string;
  name: string;
  online: boolean;
  available_memory_gb: number | null;
  load_score: number | null;
  slots_free: number;
  eligible: boolean;
  /** 首要缺口描述（紧凑展示） */
  blocking: string | null;
  /** 全部缺口描述（内存/负载/CPU 兜底/槽位/回避/离线并列） */
  blocking_reasons: string[];
}

/** 任务派发准入诊断响应（ADR-059 D3）。 */
export interface TaskEligibilityResponse {
  task_id: string;
  target_node_id: string | null;
  forced_dispatch: boolean;
  eligible_count: number;
  candidates: TaskEligibilityCandidate[];
  hint: string;
}

/**
 * 确认改派（ADR-059 D5）：`requeue_pending` → `pending`。
 *
 * 节点离线且 AI 智能调度关闭时任务停在待确认状态等用户答复；本调用即「确认」，
 * 任务交回调度器重新派发，复用既有文档与产物。非 requeue_pending 状态后端返回 409。
 */
export function confirmRedispatch(taskId: string): Promise<Task> {
  return request.post<Task>(`/v1/tasks/${taskId}/confirm-redispatch`);
}

/**
 * 查询任务派发准入诊断：每个候选节点缺什么、下一步建议。
 *
 * 用于把「排队中 0%」变成可解释的等待。
 */
export function fetchTaskEligibility(taskId: string): Promise<TaskEligibilityResponse> {
  return request.get<TaskEligibilityResponse>(`/v1/tasks/${taskId}/eligibility`);
}

/**
 * 删除任务（running/assigned 状态会被后端拒绝，ADR-028 期增强）。
 */
export function deleteTask(taskId: string): Promise<void> {
  return request.delete<void>(`/v1/tasks/${taskId}`);
}

/**
 * 取消任务（问题修复：执行中可打断子节点开发）。
 *
 * pending 直接取消；assigned/running 置 failed 并向节点推送中断指令；
 * 终态返回 409。
 */
export function cancelTask(
  taskId: string,
): Promise<{ task_id: string; status: string; node_notified: boolean }> {
  return request.post(`/v1/tasks/${taskId}/cancel`);
}

/**
 * 批准 plan 任务方案：自动转 build 模式并重新入队。
 *
 * 仅 execution_mode 为 plan、状态为 completed 且已有方案的任务可用。
 */
export function approvePlan(taskId: string): Promise<TaskDispatchResponse> {
  return request.post<TaskDispatchResponse>(`/v1/tasks/${taskId}/approve-plan`);
}

/**
 * 驳回 plan 任务方案：携带反馈意见以 plan 模式重新入队。
 */
export function rejectPlan(taskId: string, feedback: string): Promise<TaskDispatchResponse> {
  return request.post<TaskDispatchResponse>(`/v1/tasks/${taskId}/reject-plan`, { feedback });
}

/**
 * 产物文件清单条目（ADR-028）
 */
export interface ArtifactTreeNode {
  path: string;
  size: number;
  mtime: number;
}

/**
 * 产物文件清单响应
 */
export interface ArtifactTreeResponse {
  task_id: string;
  updated_at?: number | null;
  truncated?: boolean;
  files: ArtifactTreeNode[];
  cached_zip?: boolean;
}

/**
 * 产物单文件预览响应
 */
export interface ArtifactFileResponse {
  path: string;
  binary: boolean;
  size: number;
  content?: string | null;
}

/**
 * 获取任务产物文件清单（hub 缓存；无缓存返回空列表）
 */
export function fetchArtifactTree(taskId: string): Promise<ArtifactTreeResponse> {
  return request.get<ArtifactTreeResponse>(`/v1/tasks/${taskId}/artifacts/tree`);
}

/**
 * 预览产物单文件（文本内容；二进制文件 binary=true 且无 content）
 */
export function fetchArtifactFile(taskId: string, path: string): Promise<ArtifactFileResponse> {
  return request.get<ArtifactFileResponse>(`/v1/tasks/${taskId}/artifacts/file`, {
    params: { path },
  });
}

/**
 * 打包下载任务产物：hub 请求 Agent 打包后返回 zip 流（浏览器直接下载）。
 */
export function downloadArtifactsUrl(taskId: string): string {
  const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api';
  return `${base}/v1/tasks/${taskId}/artifacts/download`;
}

/**
 * 获取中枢编排器待办（ADR-061 M5d）：任务摘要**下段**数据源。
 *
 * 与任务详情里的 `todos`（节点上报，上段）物理分离；中枢尚未写待办时返回空列表。
 */
export function fetchOrchestratorTodos(
  taskId: string,
): Promise<OrchestratorTodoListResponse> {
  return request.get<OrchestratorTodoListResponse>(
    `/v1/tasks/${taskId}/orchestrator-todos`,
  );
}

/**
 * 获取本会话的中枢编排器待办（ADR-067 待办先行）。
 *
 * 任务创建前编排 AI 的待办挂构建会话；任务创建成功后自动迁移归属到任务，
 * 届时改走 fetchOrchestratorTodos（任务维度）。
 */
export function fetchSessionOrchestratorTodos(
  sessionId: string,
): Promise<OrchestratorTodoListResponse> {
  return request.get<OrchestratorTodoListResponse>(
    `/v1/tasks/build/sessions/${sessionId}/orchestrator-todos`,
  );
}

/**
 * 验收阻断决策（ADR-061 M4）：accept=接受现状并完成；retry=让子 Agent 继续修复。
 */
export function decideVerifyBlocked(
  taskId: string,
  decision: 'accept' | 'retry',
  note?: string,
): Promise<Task> {
  return request.post<Task>(`/v1/tasks/${taskId}/verify-decision`, { decision, note });
}

/**
 * 向执行中任务的节点会话定向发言（ADR-061 M5a-steer，`@节点` 入口）。
 *
 * 与取消不同：**不改变任务状态**，只影响其后续行为。
 */
export function steerTask(taskId: string, text: string): Promise<{ delivered: boolean }> {
  return request.post<{ delivered: boolean }>(`/v1/tasks/${taskId}/steer`, { text });
}

/**
 * 任务只读洞察（ADR-061 M5c）：用量曲线 + 汇总 + 告警 + 动作级时间线。
 */
export function fetchTaskInsight(taskId: string): Promise<TaskInsight> {
  return request.get<TaskInsight>(`/v1/tasks/${taskId}/insight`);
}

/**
 * 读取节点 Agent 会话轨迹（ADR-063，seq 升序增量分页）。
 *
 * @param params.afterSeq 只返回 seq 更大的行（增量拉取 / 断线续读）
 * @param params.limit   单次条数（后端默认 200、上限 1000，越界自动夹取）
 */
export function fetchTaskAgentTrace(
  taskId: string,
  params?: { nodeId?: string; afterSeq?: number; limit?: number },
): Promise<AgentTraceListResponse> {
  return request.get<AgentTraceListResponse>(`/v1/tasks/${taskId}/agent-trace`, {
    params: {
      node_id: params?.nodeId,
      after_seq: params?.afterSeq,
      limit: params?.limit,
    },
  });
}

/**
 * `@节点` 续聊响应（ADR-064 M1 契约）：空闲 202 Accepted，`reply_to`
 * 关联本轮节点回复（中枢已落库用户消息，前端据此等待节点回复）。
 */
export interface NodeChatResponse {
  reply_to: string;
}

/**
 * `@节点` 空闲续聊对话（ADR-064 M3，修订 ADR-063 M4 建任务语义）。
 *
 * 中枢把用户消息落库到任务关联构建会话，并对该任务链的既有节点会话
 * 续发消息；返回 202 {reply_to} 供前端轮询等待节点回复。
 * 错误语义：409 任务执行中（应走 steerTask 补充发言）、404 任务不存在、
 * 422 节点离线。
 */
export function nodeChatTask(
  taskId: string,
  nodeId: string,
  text: string,
): Promise<NodeChatResponse> {
  return request.post<NodeChatResponse>(`/v1/tasks/${taskId}/node-chat`, {
    node_id: nodeId,
    text,
  });
}
