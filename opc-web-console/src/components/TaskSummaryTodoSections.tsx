/* 任务摘要「两层待办」区块（ADR-061 M5d）：
   上段 = 子 Agent 上报待办（节点数据源 task.todos，只读）；
   下段 = 中枢编排器待办（orchestratorTodos，只读）。
   两者数据源与语义不同，集中在此文件承载渲染，保持 TaskSummary.tsx 单一职责。 */
import { CheckCircleOutlined, ClockCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import { Empty, List, Tag, Typography } from 'antd';
import type { OrchestratorTodo, TaskTodo } from '@/types/task';

const { Text } = Typography;

/**
 * 单条待办事项。
 */
function TodoItem({ todo }: { todo: TaskTodo }) {
  const icon =
    todo.status === 'completed' ? (
      <CheckCircleOutlined style={{ color: 'var(--primary)' }} />
    ) : todo.status === 'in_progress' ? (
      <LoadingOutlined style={{ color: 'var(--primary)' }} />
    ) : (
      <ClockCircleOutlined style={{ color: 'var(--text-muted)' }} />
    );

  return (
    <div className={`task-summary__todo task-summary__todo--${todo.status}`}>
      {icon}
      <Text className="task-summary__todo-text">{todo.content}</Text>
      {todo.status === 'in_progress' && (
        <Tag color="processing" className="task-summary__todo-tag">
          进行中
        </Tag>
      )}
    </div>
  );
}

/**
 * 上段：子 Agent 上报的待办（节点数据源 task.todos）。
 */
export function AgentTodoSection({ todos, isActive }: { todos: TaskTodo[]; isActive: boolean }) {
  const hasTodos = todos.length > 0;
  const completed = todos.filter((todo) => todo.status === 'completed').length;

  return (
    <div className="task-summary__agent-todos" data-testid="task-summary-agent-todos">
      <div className="task-summary__agent-todos-head">
        <Text className="task-summary__agent-todos-label">子 Agent 待办</Text>
        {hasTodos && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {completed}/{todos.length}
          </Text>
        )}
      </div>
      {hasTodos ? (
        <List
          size="small"
          dataSource={todos}
          renderItem={(todo) => (
            <List.Item style={{ padding: '4px 0', border: 'none' }}>
              <TodoItem todo={todo} />
            </List.Item>
          )}
        />
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text type="secondary" style={{ fontSize: 12 }}>
              {isActive ? '子 Agent 暂未上报待办明细' : '子 Agent 未上报待办明细'}
            </Text>
          }
        />
      )}
    </div>
  );
}

/**
 * 下段：中枢编排器待办（ADR-061 M5d）。
 *
 * 与上段（节点上报）刻意分隔：不同标题、不同数据源，中枢无待办时给出明确空态。
 */
export function OrchestratorTodoSection({ todos }: { todos: OrchestratorTodo[] }) {
  const completed = todos.filter((todo) => todo.status === 'completed').length;

  return (
    <div className="task-summary__orchestrator" data-testid="task-summary-orchestrator">
      <div className="task-summary__orchestrator-head">
        <Text className="task-summary__orchestrator-label">中枢待办</Text>
        {todos.length > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {completed}/{todos.length}
          </Text>
        )}
      </div>
      {todos.length > 0 ? (
        <List
          size="small"
          dataSource={todos}
          renderItem={(todo) => (
            <List.Item style={{ padding: '4px 0', border: 'none' }}>
              <TodoItem todo={{ content: todo.content, status: todo.status }} />
            </List.Item>
          )}
        />
      ) : (
        <Text type="secondary" style={{ fontSize: 12 }} data-testid="task-summary-orchestrator-empty">
          中枢编排器暂未登记待办
        </Text>
      )}
    </div>
  );
}
