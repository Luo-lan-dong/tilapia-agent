import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { TaskTable } from '@/components/TaskTable';
import type { Task } from '@/types/task';

/** 构造测试任务数据 */
function makeTask(overrides: Partial<Task>): Task {
  return {
    id: 'task-default',
    title: '示例任务',
    agent_type: 'programming',
    status: 'pending',
    progress: 0,
    source: 'spec_chat',
    created_at: '2026-08-15T00:00:00Z',
    updated_at: '2026-08-15T00:00:00Z',
    ...overrides,
  };
}

/** 路由探针：展示当前 pathname + search，用于断言「继续修改」跳转参数 */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderTable(tasks: Task[], onViewDetail = vi.fn()) {
  return render(
    <MemoryRouter initialEntries={['/tasks']}>
      <LocationProbe />
      <TaskTable data={tasks} loading={false} onViewDetail={onViewDetail} />
    </MemoryRouter>,
  );
}

describe('TaskTable 操作列', () => {
  it('点击详情按钮触发回调', async () => {
    const onViewDetail = vi.fn();
    renderTable([makeTask({ id: 'task-pending-1' })], onViewDetail);

    await userEvent.click(screen.getByTestId('view-detail-task-pending-1'));
    expect(onViewDetail).toHaveBeenCalledWith('task-pending-1');
  });

  it('已完成与失败的任务显示「继续修改」，其他状态不显示', () => {
    renderTable([
      makeTask({ id: 'task-pending-1', status: 'pending' }),
      makeTask({ id: 'task-running-1', status: 'running' }),
      makeTask({ id: 'task-done-1', status: 'completed' }),
      makeTask({ id: 'task-failed-1', status: 'failed' }),
    ]);

    expect(screen.queryByTestId('continue-task-task-pending-1')).not.toBeInTheDocument();
    expect(screen.queryByTestId('continue-task-task-running-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('continue-task-task-done-1')).toBeInTheDocument();
    expect(screen.getByTestId('continue-task-task-failed-1')).toBeInTheDocument();
  });

  it('点击「继续修改」跳转任务创建页并携带 parent 参数', async () => {
    renderTable([makeTask({ id: 'task-done-1', status: 'completed' })]);

    await userEvent.click(screen.getByTestId('continue-task-task-done-1'));
    expect(screen.getByTestId('location')).toHaveTextContent('/tasks/new?parent=task-done-1');
  });

  it('未传 onViewDetail 时不渲染详情按钮', () => {
    render(
      <MemoryRouter>
        <TaskTable data={[makeTask({ id: 'task-pending-1' })]} loading={false} />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('view-detail-task-pending-1')).not.toBeInTheDocument();
  });
});

describe('TaskTable 排队可见性（ADR-059 D3）', () => {
  it('pending 且有排队原因时显示「排队中」与等待的目标节点', () => {
    renderTable([
      makeTask({
        id: 'task-queued-1',
        status: 'pending',
        target_node_name: '笔记本',
        dispatch_hold_reason: '指定节点「笔记本」暂不可自主：可用内存 0.7GB ≤ 1.0GB',
      }),
    ]);

    // 不再只显示「—」，让用户知道在等哪台、为什么等
    expect(screen.getByText('排队中')).toBeInTheDocument();
    expect(screen.getByText(/等待 笔记本/)).toBeInTheDocument();
  });

  it('排队原因作为 tooltip 提供缺口数值', async () => {
    renderTable([
      makeTask({
        id: 'task-queued-2',
        status: 'pending',
        dispatch_hold_reason: '无可自主派发的节点：code（综合负载 83.5 ≥ 80.0）',
      }),
    ]);

    // 悬停「排队中」标签后展示完整缺口原因（antd Tooltip 走 portal）
    await userEvent.hover(screen.getByText('排队中'));
    expect(
      await screen.findByText('无可自主派发的节点：code（综合负载 83.5 ≥ 80.0）'),
    ).toBeInTheDocument();
  });

  it('无排队原因的 pending 任务仍显示分配节点占位符', () => {
    renderTable([makeTask({ id: 'task-plain-1', status: 'pending' })]);

    expect(screen.queryByText('排队中')).not.toBeInTheDocument();
    // 分配节点与派发两列都会渲染占位符，此处只要求占位符存在
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('running 任务即使带排队原因也优先展示已分配节点', () => {
    renderTable([
      makeTask({
        id: 'task-running-2',
        status: 'running',
        assigned_node_name: 'code',
        dispatch_hold_reason: '历史排队原因',
      }),
    ]);

    expect(screen.queryByText('排队中')).not.toBeInTheDocument();
    expect(screen.getByText('code')).toBeInTheDocument();
  });
});
