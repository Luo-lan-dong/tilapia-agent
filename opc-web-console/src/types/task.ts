import type { PaginationMeta } from './common';
import type { TaskSpecification } from './taskBuilding';

export type { TaskSpecification };

/**
 * 任务状态枚举
 *
 * review（待确认，ADR-038）：plan 模式任务方案产出后等待人工批准/驳回
 * requeue_pending（待确认改派，ADR-059 D5）：节点离线且 AI 智能调度关闭时，
 *   任务停在待确认状态等用户答复；调度器**不派发**该状态任务
 */
export type TaskStatus =
  | 'pending'
  | 'assigned'
  | 'running'
  | 'completed'
  | 'failed'
  | 'review'
  | 'requeue_pending'
  /** 验收阻断（ADR-061 M4）：必过验收未通过/无法验证，等用户在对话区决定 */
  | 'verify_blocked';

/**
 * 任务优先级（ADR-038）
 */
export type TaskPriority = 'P0' | 'P1' | 'P2';

/**
 * 任务执行模式：build 直接开发 / plan 先规划再审批
 */
export type TaskExecutionMode = 'plan' | 'build';

/**
 * 任务模型
 */
export interface Task {
  id: string;
  title: string;
  description?: string;
  agent_type: string;
  status: TaskStatus;
  /** 任务优先级 P0|P1|P2（ADR-038） */
  priority?: TaskPriority;
  assigned_node_id?: string;
  /** 分配节点名称（JOIN agent_nodes.name；未分配时为 undefined，ADR-017） */
  assigned_node_name?: string;
  /** 用户/AI 指定的目标节点 ID（ADR-059）：有值时任务排队只等该节点，达标当轮派送 */
  target_node_id?: string | null;
  /** 目标节点名称（JOIN agent_nodes.name；无指定时为 null） */
  target_node_name?: string | null;
  /** 最近一次节点拒单原因（ADR-059，如 resource_busy）；null=未被拒单 */
  last_reject_reason?: string | null;
  /** 当前排队原因（ADR-059，含缺口数值）；null=未在排队 */
  dispatch_hold_reason?: string | null;
  /** 是否用户明确要求越过准入门槛强行派送（ADR-059 D2 例外，审计用） */
  forced_dispatch?: boolean;
  progress: number;
  /** 任务来源，如 manual / spec_chat */
  source: string;
  /** 执行目标子系统标识，未派发时为 undefined */
  execution_target?: string;
  /** 任务规范三件套 */
  specification?: TaskSpecification;
  /** 执行模式，plan 先规划再审批 / build 直接开发 */
  execution_mode?: TaskExecutionMode;
  /** 派发模式 auto|manual（ADR-017） */
  dispatch_type?: 'auto' | 'manual';
  /** 手动派发窗口截止时间（UTC），窗口外由调度器接管 */
  dispatch_window_until?: string | null;
  /** 本轮修改说明，任务迭代或方案审批时由中枢拼装下发 */
  change_brief?: string | null;
  /** plan 模式产出的方案全文，供审批展示 */
  plan_content?: string | null;
  /** build 模式完成时 Agent 上报的本轮修改说明 */
  change_summary?: string | null;
  /** Agent 实时上报的待办列表 */
  todos?: TaskTodo[] | null;
  /** Agent 实时上报的执行阶段（ADR-055 封闭枚举：preparing/planning/coding/…/idle） */
  progress_stage?: string | null;
  /** Agent 实时上报的当前动作描述（ADR-055，如「正在处理 src/main.py」） */
  progress_message?: string | null;
  /** 分层验收报告（ADR-061 M4）：验收阻断时展示未通过项 */
  verify_report?: VerifyReportPayload | null;
  /** 验收阻断时刻（ADR-061 M4）：非空即处于待用户决定状态 */
  verify_blocked_at?: string | null;
  /** 迭代链父任务 ID，链上任务复用根任务的工作目录与会话 */
  parent_task_id?: string | null;
  /** 任务产物保存路径（Agent 节点本地目录语义，ADR-028） */
  output_dir?: string | null;
  /** 节点上报的实际执行模型（ADR-029 增强）：如 deepseek/deepseek-chat；降级时带原因说明 */
  executor_model_actual?: string | null;
  /** 关联的任务构建会话 ID */
  session_id?: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * 待办事项
 */
export interface TaskTodo {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  priority?: 'low' | 'medium' | 'high';
}

/**
 * 分层验收单条检查结果（ADR-061 M4）
 */
export interface VerifyCheckPayload {
  layer: string;
  name: string;
  cmd: string;
  must_pass: boolean;
  status: 'passed' | 'failed' | 'timeout' | 'error';
  exit_code?: number | null;
  duration_ms?: number;
  output_tail?: string;
}

/**
 * 分层验收报告（ADR-061 M4）：验收阻断时用于展示「哪些必过项没过」
 */
export interface VerifyReportPayload {
  /** passed=放行；failed=必过项红；unverifiable=无法验证 */
  status: 'passed' | 'failed' | 'unverifiable';
  /** 判据来源：verify_json / project_config / derived / none */
  source?: string;
  checks?: VerifyCheckPayload[];
  must_pass_failures?: { layer: string; name: string; cmd: string; status: string }[];
  /** 节点已完成的自修轮次 */
  repair_rounds?: number;
  /** 人类可读摘要 */
  summary?: string;
}

/**
 * 中枢编排器待办项（ADR-061 M5d）：任务摘要**下段**数据源。
 *
 * 与 `Task.todos`（节点上报的子 Agent 待办，上段）**物理分离**：
 * 本清单由中枢编排器经 `todo_write` 技能维护，前端只读展示。
 */
export interface OrchestratorTodo {
  id: string;
  seq: number;
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  /** 来源：orchestrator（预留 user） */
  source: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

/** 中枢编排器待办清单响应（ADR-061 M5d；ADR-067 待办先行：会话维度时 session_id 有值） */
export interface OrchestratorTodoListResponse {
  task_id?: string | null;
  session_id?: string | null;
  items: OrchestratorTodo[];
  pending_count: number;
  completed_count: number;
}

/**
 * 任务只读洞察（ADR-061 M5c）：用量曲线 + 汇总 + 告警 + 动作级时间线。
 *
 * 全部来自中枢既有落库（`tool_call_logs`），**不含原始工具输出全文**（Q12 裁决）。
 */
export interface TaskUsagePoint {
  at?: string | null;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cache_hit_rate: number;
  files_touched: number;
  cost?: number | null;
  tool?: string | null;
}

export interface TaskUsageSummary {
  steps: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cache_hit_rate: number;
  /** 基线（ADR-060 实测 95.2%） */
  cache_hit_baseline: number;
  hit_rate_below_baseline: boolean;
  files_touched: number;
}

export interface TaskInsightAlert {
  code: 'cache_hit_below_baseline' | 'repeated_activity' | 'no_file_write' | string;
  level: string;
  title: string;
  detail: string;
}

export interface TaskTimelineEntry {
  at?: string | null;
  kind: string;
  name: string;
  title: string;
  detail: string;
  status: string;
}

export interface TaskInsight {
  task_id: string;
  usage: TaskUsagePoint[];
  summary: TaskUsageSummary;
  alerts: TaskInsightAlert[];
  timeline: TaskTimelineEntry[];
}

/**
 * 节点 Agent 会话轨迹行（ADR-063 M2/M3）。
 *
 * kind 与节点侧 `EventTranslator.translate_trace` / 中枢 `task_agent_trace.kind`
 * 白名单对齐；payload 内容按 kind 解释（status: stage/message，reasoning/text:
 * text，tool: tool/phase/target/summary/exit_code）。
 */
export interface AgentTraceRow {
  seq: number;
  kind: 'status' | 'reasoning' | 'text' | 'tool' | 'todo' | 'usage' | string;
  payload: Record<string, unknown>;
  created_at: string;
  /** 冗余字段（schema 容忍）：执行节点 ID（client_id 形态） */
  node_id?: string | null;
}

/** 轨迹读取响应（seq 升序增量分页） */
export interface AgentTraceListResponse {
  rows: AgentTraceRow[];
  has_more: boolean;
}

/**
 * 任务列表查询参数
 */
export interface TaskListParams {
  status?: TaskStatus;
  agent_type?: string;
  /** 按任务标题模糊搜索（ADR-038） */
  keyword?: string;
  limit?: number;
  offset?: number;
}

/**
 * 任务列表响应（与后端 PaginatedResponse 对齐：total 在 pagination 内）
 */
export interface TaskListResponse {
  items: Task[];
  pagination: PaginationMeta;
}

/**
 * 创建任务请求
 */
export interface TaskCreateRequest {
  requirement: string;
  /** 任务规范三件套，提供时直接保存不再走 Planning Agent */
  specification?: TaskSpecification;
  /** 关联的任务构建会话 ID */
  session_id?: string;
  /** 执行模式，缺省为 build 直接开发 */
  execution_mode?: TaskExecutionMode;
  /** 迭代链父任务 ID，指定后复用父任务工作目录与会话 */
  parent_task_id?: string;
  /** 本轮修改说明，迭代任务强烈建议填写 */
  change_brief?: string;
  /** 任务产物保存路径（Agent 节点本地目录，ADR-028）；缺省由 Agent 按默认规则分配 */
  output_dir?: string;
  /** 子 Agent 执行模型选择（ADR-029）；缺省 = 中枢配置模型 */
  executor_model?: ExecutorModelSelection;
}

/** 子 Agent 执行模型选择（ADR-029 / ADR-030 / ADR-039：支持指定自定义模型行） */
export interface ExecutorModelSelection {
  /** 模型来源：auto 由中枢按任务内容选模 / hub 中枢使用中 / custom 指定自定义模型 */
  source: 'auto' | 'hub' | 'custom';
  /** custom 来源：指向已添加模型配置的 ID */
  provider_id?: string;
  /** 模型 ID；缺省时 hub 来源跟随系统配置 */
  model?: string;
}

/**
 * 创建任务响应
 */
export interface TaskCreateResponse {
  id: string;
  title: string;
  agent_type: string;
  status: TaskStatus;
}
