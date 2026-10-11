import { Progress, Spin, Tag, Typography } from 'antd';
import type { OrchestratorTodo, Task, TaskInsight } from '@/types/task';
import { AgentTodoSection, OrchestratorTodoSection } from '@/components/TaskSummaryTodoSections';
import { CurrentAction, VerifyBlockedPanel } from '@/components/TaskSummaryPanels';
import { TaskInsightPanel } from '@/components/TaskInsightPanel';

const { Text, Title } = Typography;

interface TaskSummaryProps {
  /** 跟踪中的任务详情 */
  task: Task | undefined;
  /** 详情加载中 */
  loading: boolean;
  /**
   * 中枢编排器自己的待办（ADR-061 M5d，摘要**下段**）。
   *
   * 与 `task.todos`（节点上报，上段）物理分离；缺省为空数组（下段显示空态）。
   */
  orchestratorTodos?: OrchestratorTodo[];
  /**
   * 验收阻断决策回调（ADR-061 M4）：`accept` 接受现状 / `retry` 继续修复。
   * 未提供时不渲染决策按钮（例如只读场景）。
   */
  onDecideVerify?: (decision: 'accept' | 'retry') => void | Promise<void>;
  /** 任务只读洞察（ADR-061 M5c）：用量/告警/时间线 */
  insight?: TaskInsight;
  /** 向节点会话发言（ADR-061 M5a-steer）；未提供时不渲染输入框 */
  onSteer?: (text: string) => void | Promise<void>;
}

/** 任务状态中文标签 */
const STATUS_LABELS: Record<string, string> = {
  pending: '排队中',
  assigned: '已分配',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  review: '待确认',
  // ADR-061 M4：验收未通过/无法验证，等用户在对话区决定
  verify_blocked: '待验收决定',
};

/**
 * 执行中状态集合：这些状态下若 Agent 长时间没有新动作，提示可能仍在处理。
 */
const ACTIVE_STATUSES = new Set(['running', 'assigned']);

/**
 * 任务摘要面板。
 *
 * 实时展示跟踪任务的执行状态、进度条、Agent 上报的当前动作与待办列表。
 * 从属区块已按职责拆分：两层待办见 TaskSummaryTodoSections，动作/验收决策见 TaskSummaryPanels。
 *
 * ADR-061 M5d（D5.5）：面板分两段——**上段 = 子 Agent 上报的待办（节点数据源）**，
 * **下段 = 中枢编排器自己的待办**（中枢侧维护、只读展示）。
 */
export function TaskSummary({
  task,
  loading,
  orchestratorTodos = [],
  onDecideVerify,
  insight,
  onSteer,
}: TaskSummaryProps) {
  if (!task) {
    return null;
  }

  const todos = task.todos ?? [];
  const isActive = ACTIVE_STATUSES.has(task.status);
  // 进度条（2026-10-07 用户裁决 + 实测修订）：**单调不回退**——
  // task.progress 是节点事件推进的单调值；子 Agent 待办完成率会因
  // 中途重列清单归零（实测 90% → 0% → 75% → 13% 大起大落），两者取
  // max 后进度只升不降；无待办数据时就是 task.progress；completed 强制 100。
  const todoCompleted = todos.filter((todo) => todo.status === 'completed').length;
  const todoPercent = todos.length > 0 ? Math.round((todoCompleted / todos.length) * 100) : 0;
  const progressPercent =
    task.status === 'completed' ? 100 : Math.max(task.progress, todoPercent);

  return (
    <div className="task-summary">
      <div className="task-summary__header">
        <Title level={5} style={{ margin: 0, color: 'var(--text-primary)' }}>
          任务摘要
        </Title>
        {loading && <Spin size="small" />}
      </div>
      <div className="task-summary__meta">
        <Tag
          color={
            task.status === 'failed'
              ? 'red'
              : task.status === 'completed'
                ? 'green'
                : task.status === 'verify_blocked'
                  ? 'orange'
                  : 'processing'
          }
        >
          {STATUS_LABELS[task.status] ?? task.status}
        </Tag>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {task.title}
        </Text>
      </div>
      <div className="task-summary__progress">
        <Progress
          percent={progressPercent}
          size="small"
          strokeColor="var(--primary)"
          trailColor="var(--border)"
          showInfo
        />
      </div>
      {/* 「当前动作」只在执行中显示：任务终态/等待决策后节点动作残留
         （如「编写中/仍在执行」）只会误导（2026-10-07 用户实测） */}
      {isActive && <CurrentAction stage={task.progress_stage} message={task.progress_message} />}
      {/* 上段（2026-10-02 问题修复）：子 Agent 上报的待办，与下段「中枢待办」形成两层 */}
      <AgentTodoSection todos={todos} isActive={isActive} />
      {task.status === 'verify_blocked' && (
        <VerifyBlockedPanel report={task.verify_report} onDecide={onDecideVerify} />
      )}
      <OrchestratorTodoSection todos={orchestratorTodos} />
      {/* ADR-061 M5c：只读洞察（用量/告警/时间线/@节点发言）追加在既有区块之后 */}
      <TaskInsightPanel insight={insight} onSteer={onSteer} />
    </div>
  );
}
