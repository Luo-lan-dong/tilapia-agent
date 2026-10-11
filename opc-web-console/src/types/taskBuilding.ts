/**
 * 任务构建对话模式。
 */
export type TaskBuildMode = 'fast' | 'thinking';

/**
 * 聊天消息角色。
 */
export type ChatMessageRole = 'user' | 'assistant' | 'tool';

/**
 * 工具调用描述（OpenAI 原格式，ADR-040 轨迹回流）。
 */
export interface ChatMessageToolCall {
  id: string;
  type?: string;
  function: {
    name: string;
    arguments: string;
  };
}

/**
 * 消息图片附件元数据（ADR-039）。
 *
 * preview_url 仅本地新发消息可用（blob 预览）；历史消息通过
 * sessionId + id 构造后端预览地址展示。
 */
export interface ChatMessageAttachment {
  id: string;
  file_name: string;
  mime: string;
  size: number;
  /** 本地待发送预览地址（blob:），仅前端内存态 */
  preview_url?: string;
}

/**
 * 上传中的附件条目（输入框内显示实时进度，ADR-039）。
 */
export interface UploadingAttachment {
  /** 本地临时 key（上传完成转正后以服务端附件 id 为准） */
  key: string;
  file_name: string;
  mime: string;
  size: number;
  /** 本地预览地址（仅图片，blob:） */
  preview_url?: string;
  /** 上传进度 0-100 */
  percent: number;
}

/**
 * 单条聊天消息。
 */
export interface ChatMessage {
  role: ChatMessageRole;
  content: string;
  /** 深度思考过程内容，仅 assistant 消息可能包含 */
  thinking?: string;
  /** 技能执行步骤（ADR-033 执行链路可视化），仅 assistant 消息可能包含 */
  steps?: SkillStep[];
  /** 工具调用清单（ADR-040 轨迹回流），仅 assistant 消息可能包含 */
  tool_calls?: ChatMessageToolCall[];
  /** 对应的工具调用 ID（ADR-040 轨迹回流），仅 role=tool 消息携带 */
  tool_call_id?: string;
  /** 图片附件（ADR-039），仅 user 消息可能包含 */
  attachments?: ChatMessageAttachment[];
  /** 插话排队标记（ADR-045）：steer 已入队但尚未被边界提升，仅前端展示 */
  queued?: boolean;
  /** `@节点` 来源标注（ADR-063 M4/D6）：该消息定向发给指定节点 */
  mentionNode?: string;
  /**
   * `@节点` 来源标注（snake_case 直传形态，ADR-064 落库契约）：
   * 历史恢复时后端 messages 原样返回该字段，与 mentionNode 同义。
   */
  mention_node?: string;
  /**
   * 节点回复关联（ADR-064 M3）：node-chat 202 返回的 reply_to 写回本地
   * 用户消息（camelCase）；后端落库直传形态为 reply_to。
   */
  replyTo?: string;
  /** 节点回复关联（snake_case 直传形态，ADR-064 落库契约，历史恢复用） */
  reply_to?: string;
  /** 发送失败标记（ADR-064 M3）：node-chat/发送非 2xx 时保留气泡并显式报错 */
  failed?: boolean;
  /**
   * 消息来源标记（ADR-066 唤醒可视化）：后端落库的系统消息带
   * `system_wake`——任务终态自动唤醒轮的触发消息，前端据此识别并
   * 从会话库同步到对话区。
   */
  source?: string;
}

/**
 * 技能执行步骤（对话中逐步展示 AI 调用的技能，ADR-033）。
 */
export interface SkillStep {
  step_id: string;
  seq: number;
  /** 技能唯一标识，如 find_idle_nodes */
  skill_name: string;
  /** 技能展示名称 */
  skill_display_name?: string;
  /** 参数摘要（JSON 字符串） */
  args_summary?: string;
  status: 'running' | 'success' | 'failed';
  /** 结果摘要（技能执行结束后填充） */
  result_summary?: string;
  duration_ms?: number;
}

/**
 * 任务规范三件套。
 */
export interface TaskSpecification {
  /** 需求规范文档 */
  spec: string;
  /** 实现任务清单 */
  tasks: string;
  /** 验收检查清单 */
  checklist: string;
}

/**
 * 任务构建对话请求。
 */
export interface TaskBuildChatRequest {
  messages: ChatMessage[];
  current_specification?: TaskSpecification;
  mode?: TaskBuildMode;
  session_id?: string;
  /** 任务链父任务 ID（ADR-028）：AI 创建任务时复用迭代链与工作目录 */
  parent_task_id?: string;
  /** 任务工作目录：AI 创建任务时锁定同目录 */
  output_dir?: string;
}

/**
 * 任务构建对话响应。
 */
export interface TaskBuildChatResponse {
  /** AI 对用户的回复 */
  ai_reply: string;
  /** 当前三件套草案 */
  specification: TaskSpecification;
  /** 信息是否足够，可否创建任务 */
  is_ready_to_create: boolean;
  /** 当前使用的模型标识 */
  model: string;
  /** 当前使用的对话模式 */
  mode: TaskBuildMode;
}

/**
 * SSE 流式事件类型。
 */
export type TaskBuildStreamEventType =
  | 'meta'
  | 'thinking'
  | 'chunk'
  | 'step_started'
  | 'step_finished'
  | 'specification'
  | 'specification_delta'
  | 'status'
  | 'warning'
  | 'context_usage'
  | 'context_compacting'
  | 'context_compacted'
  | 'steering_applied'
  | 'command_approval_request'
  | 'command_executing'
  | 'error'
  | 'done';

/**
 * 终端命令审批请求（ADR-053）：manual / ai_auto(high|unknown) 模式下
 * run_command 技能发出，前端渲染确认卡，用户批准/拒绝后调
 * POST /api/v1/terminal/approvals/{id}。
 */
export interface TerminalApprovalRequest {
  approval_id: string;
  command: string;
  risk_level: string;
}

/**
 * SSE 流式事件统一结构。
 */
export interface TaskBuildStreamEvent {
  event: TaskBuildStreamEventType;
  data: Record<string, unknown>;
}

/**
 * SSE 首事件：模型和模式元信息。
 */
export interface TaskBuildStreamMeta {
  model: string;
  mode: TaskBuildMode;
}

/**
 * SSE thinking 事件：AI 思考过程增量。
 */
export interface TaskBuildStreamThinking {
  delta: string;
}

/**
 * SSE chunk 事件：AI 回复文本增量。
 */
export interface TaskBuildStreamChunk {
  delta: string;
}

/**
 * SSE status 事件：是否可创建任务状态变更。
 */
export interface TaskBuildStreamStatus {
  is_ready_to_create: boolean;
}

/**
 * SSE specification 事件：三件套完整更新。
 */
export interface TaskBuildStreamSpecification {
  spec: string;
  tasks: string;
  checklist: string;
}

/**
 * SSE context_usage 事件：上下文占用实时展示（ADR-041）。
 */
export interface TaskBuildStreamContextUsage {
  used_tokens: number;
  window_tokens: number;
  percent: number | null;
}

/**
 * SSE context_compacted 事件：历史对话压缩完成（ADR-041 增强）。
 */
export interface TaskBuildStreamCompacted {
  /** 是否实际执行了压缩（判定命中但无新内容可摘要时为 false） */
  compacted: boolean;
  /** 压缩点覆盖的消息条数 */
  message_count: number | null;
}

/**
 * 任务构建会话（完整数据，详情接口返回）。
 */
export interface TaskBuildingSession {
  id: string;
  title: string;
  messages: ChatMessage[];
  specification: TaskSpecification | null;
  mode: TaskBuildMode;
  model: string | null;
  /** 本会话已创建的任务 ID，恢复会话时继续跟踪方案审批 */
  task_id: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * 任务构建会话列表项（轻量，列表接口返回）。
 *
 * 列表不携带 messages / specification 等大字段——点开某条会话时经
 * 详情接口（fetchTaskBuildingSessionById）惰性拉取完整数据。
 */
export interface TaskBuildingSessionSummary {
  id: string;
  title: string;
  mode: TaskBuildMode;
  model: string | null;
  task_id: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * 创建任务构建会话请求。
 */
export interface TaskBuildingSessionCreateRequest {
  title: string;
  messages?: ChatMessage[];
  specification?: TaskSpecification;
  mode?: TaskBuildMode;
  model?: string;
  /** 本会话已创建的任务 ID */
  task_id?: string;
}

/**
 * 更新任务构建会话请求。
 */
export interface TaskBuildingSessionUpdateRequest {
  title?: string;
  messages?: ChatMessage[];
  specification?: TaskSpecification;
  mode?: TaskBuildMode;
  model?: string;
  /** 本会话已创建的任务 ID */
  task_id?: string;
}

/**
 * 分页元信息。
 */
export interface PaginationInfo {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

/**
 * 任务构建会话列表分页响应。
 */
export interface TaskBuildingSessionListResponse {
  items: TaskBuildingSessionSummary[];
  pagination: PaginationInfo;
}
