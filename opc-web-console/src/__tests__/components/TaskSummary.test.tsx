import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { TaskSummary } from '@/components/TaskSummary';
import type { Task } from '@/types/task';

/** 构造测试任务数据（默认：执行中、有当前动作） */
function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-1',
    title: '实现登录功能',
    agent_type: 'code',
    status: 'running',
    progress: 42,
    source: 'manual',
    created_at: '2026-09-27T00:00:00Z',
    updated_at: '2026-09-27T00:00:00Z',
    ...overrides,
  };
}

describe('TaskSummary 当前动作（ADR-055）', () => {
  it('有待办 + 有当前动作：两者都渲染，阶段映射为中文标签', () => {
    render(
      <TaskSummary
        loading={false}
        task={makeTask({
          progress_stage: 'editing',
          progress_message: '正在处理 src/main.py',
          todos: [
            { content: '搭骨架', status: 'completed', priority: 'high' },
            { content: '实现登录接口', status: 'in_progress', priority: 'high' },
          ],
        })}
      />,
    );

    const action = screen.getByTestId('task-summary-action');
    expect(action).toBeInTheDocument();
    expect(action).toHaveTextContent('当前动作');
    expect(action).toHaveTextContent('修改文件');
    expect(action).toHaveTextContent('正在处理 src/main.py');
    expect(screen.getByText('实现登录接口')).toBeInTheDocument();
    // 上段明确标注为子 Agent 待办，并带完成计数
    const agentSection = screen.getByTestId('task-summary-agent-todos');
    expect(agentSection).toHaveTextContent('子 Agent 待办');
    expect(agentSection).toHaveTextContent('1/2');
    expect(screen.queryByText('暂无待办明细')).not.toBeInTheDocument();
  });

  it('无待办 + 有当前动作：仍展示当前动作，不再只显示「暂无待办明细」', () => {
    render(
      <TaskSummary
        loading={false}
        task={makeTask({
          progress_stage: 'running_command',
          progress_message: '正在执行 npm test',
          todos: [],
        })}
      />,
    );

    const action = screen.getByTestId('task-summary-action');
    expect(action).toHaveTextContent('执行命令');
    expect(action).toHaveTextContent('正在执行 npm test');
    // 空态文案变为「子 Agent 暂未上报待办明细」，避免用户以为任务没进展
    expect(screen.getByText('子 Agent 暂未上报待办明细')).toBeInTheDocument();
  });

  it('无待办 + 无当前动作：显示等待提示与终态空态文案', () => {
    const { rerender } = render(
      <TaskSummary loading={false} task={makeTask({ todos: [] })} />,
    );
    expect(screen.getByTestId('task-summary-action')).toHaveTextContent(
      '等待 Agent 上报执行动作…',
    );
    expect(screen.getByText('子 Agent 暂未上报待办明细')).toBeInTheDocument();

    rerender(<TaskSummary loading={false} task={makeTask({ todos: [], status: 'completed' })} />);
    expect(screen.getByText('子 Agent 未上报待办明细')).toBeInTheDocument();
  });

  it('未知 stage 容错：原样展示，不崩溃', () => {
    render(
      <TaskSummary
        loading={false}
        task={makeTask({ progress_stage: 'brand_new_stage', progress_message: '未知阶段动作' })}
      />,
    );

    const action = screen.getByTestId('task-summary-action');
    expect(action).toHaveTextContent('brand_new_stage');
    expect(action).toHaveTextContent('未知阶段动作');
  });

  it('task 为空时不渲染任何内容', () => {
    const { container } = render(<TaskSummary loading={false} task={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('TaskSummary 中枢待办下段（ADR-061 M5d / D5.5）', () => {
  it('上段 5 块保持原样，下段渲染中枢待办且带完成计数', () => {
    render(
      <TaskSummary
        loading={false}
        task={makeTask({
          progress_stage: 'editing',
          progress_message: '正在处理 src/main.py',
          todos: [{ content: '节点上报的待办', status: 'pending' }],
        })}
        orchestratorTodos={[
          {
            id: 'todo-1',
            seq: 1,
            content: '中枢：先核对验收判据',
            status: 'completed',
            source: 'orchestrator',
            created_at: '2026-09-28T00:00:00Z',
            updated_at: '2026-09-28T00:00:00Z',
            completed_at: '2026-09-28T01:00:00Z',
          },
          {
            id: 'todo-2',
            seq: 2,
            content: '中枢：安排回归测试',
            status: 'in_progress',
            source: 'orchestrator',
            created_at: '2026-09-28T00:00:00Z',
            updated_at: '2026-09-28T01:00:00Z',
          },
        ]}
      />,
    );

    // 上段（节点上报）仍渲染，未被下段挤掉或串位
    expect(screen.getByTestId('task-summary-action')).toHaveTextContent('正在处理 src/main.py');
    expect(screen.getByText('节点上报的待办')).toBeInTheDocument();
    const agentSection = screen.getByTestId('task-summary-agent-todos');
    expect(agentSection).toHaveTextContent('子 Agent 待办');
    expect(agentSection).toHaveTextContent('0/1');

    // 下段（中枢编排器自己的待办）
    const section = screen.getByTestId('task-summary-orchestrator');
    expect(section).toHaveTextContent('中枢待办');
    expect(section).toHaveTextContent('1/2');
    expect(section).toHaveTextContent('中枢：先核对验收判据');
    expect(section).toHaveTextContent('中枢：安排回归测试');
    expect(section).toHaveTextContent('进行中');
  });

  it('中枢无待办时下段显示明确空态（不隐藏区块）', () => {
    render(<TaskSummary loading={false} task={makeTask()} />);

    expect(screen.getByTestId('task-summary-orchestrator')).toBeInTheDocument();
    expect(screen.getByTestId('task-summary-orchestrator-empty')).toHaveTextContent(
      '中枢编排器暂未登记待办',
    );
  });
});

describe('TaskSummary 验收阻断决策（ADR-061 M4）', () => {
  it('阻断态：展示未通过必过项与两个决策按钮，并回传决策', async () => {
    const decisions: string[] = [];
    const onDecideVerify = (decision: 'accept' | 'retry') => {
      decisions.push(decision);
    };

    render(
      <TaskSummary
        loading={false}
        task={makeTask({
          status: 'verify_blocked',
          progress: 100,
          verify_report: {
            status: 'failed',
            source: 'verify_json',
            repair_rounds: 5,
            must_pass_failures: [
              { layer: 'L1', name: '单元测试', cmd: 'pytest -q', status: 'failed' },
            ],
          },
        })}
        onDecideVerify={onDecideVerify}
      />,
    );

    expect(screen.getByText('待验收决定')).toBeInTheDocument();
    const panel = screen.getByTestId('task-summary-verify');
    expect(panel).toHaveTextContent('已自动自修 5 轮');
    expect(panel).toHaveTextContent('单元测试');
    expect(panel).toHaveTextContent('pytest -q');

    await userEvent.click(screen.getByRole('button', { name: '继续修复' }));
    await userEvent.click(screen.getByRole('button', { name: '接受现状' }));
    expect(decisions).toEqual(['retry', 'accept']);
  });

  it('无法验证：提示「无法验证」而非普通失败；未传回调时不渲染按钮', () => {
    render(
      <TaskSummary
        loading={false}
        task={makeTask({
          status: 'verify_blocked',
          verify_report: { status: 'unverifiable', source: 'none', must_pass_failures: [] },
        })}
      />,
    );

    const panel = screen.getByTestId('task-summary-verify');
    expect(panel).toHaveTextContent('无法验证');
    expect(screen.queryByRole('button', { name: '继续修复' })).not.toBeInTheDocument();
  });
});
