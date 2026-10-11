import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { App as AntApp } from 'antd';
import { describe, expect, it, vi } from 'vitest';
import { TaskDetailDrawer } from '@/components/TaskDetailDrawer';
import { server } from '@/test/setup';

function taskPayload(overrides: Record<string, unknown> = {}) {
  return {
    code: 0,
    message: 'ok',
    request_id: 'req-1',
    data: {
      id: 'task-1',
      title: '离线任务',
      agent_type: 'code',
      status: 'requeue_pending',
      progress: 0,
      source: 'spec_chat',
      execution_mode: 'build',
      change_brief: null,
      plan_content: null,
      parent_task_id: null,
      session_id: null,
      dispatch_hold_reason: '执行节点离线，任务已停止并等待你确认改派（确认后交调度器重新派发）',
      created_at: '2026-08-15T00:00:00Z',
      updated_at: '2026-08-15T00:00:00Z',
      ...overrides,
    },
  };
}

function renderDrawer(props: Partial<Parameters<typeof TaskDetailDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <TaskDetailDrawer open taskId="task-1" onClose={vi.fn()} {...props} />
    </AntApp>,
  );
}

describe('TaskDetailDrawer 待确认改派（ADR-059 D5）', () => {
  it('requeue_pending 展示排队原因与「确认改派」按钮', async () => {
    server.use(http.get('/api/v1/tasks/:id', () => HttpResponse.json(taskPayload())));

    renderDrawer();

    expect(await screen.findByText('待确认改派')).toBeInTheDocument();
    expect(
      screen.getByText('执行节点离线，任务已停止并等待你确认改派（确认后交调度器重新派发）'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('confirm-redispatch-button')).toBeInTheDocument();
  });

  it('非 requeue_pending 状态不显示「确认改派」按钮', async () => {
    server.use(
      http.get('/api/v1/tasks/:id', () =>
        HttpResponse.json(taskPayload({ status: 'pending', dispatch_hold_reason: null })),
      ),
    );

    renderDrawer();

    await waitFor(() => {
      expect(screen.getByText('待分配')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('confirm-redispatch-button')).not.toBeInTheDocument();
  });

  it('点击确认改派调用接口、提示成功并刷新列表', async () => {
    server.use(
      http.get('/api/v1/tasks/:id', () => HttpResponse.json(taskPayload())),
      http.post('/api/v1/tasks/:id/confirm-redispatch', () =>
        HttpResponse.json(taskPayload({ status: 'pending', dispatch_hold_reason: null })),
      ),
    );
    const onChanged = vi.fn();

    renderDrawer({ onChanged });

    await userEvent.click(await screen.findByTestId('confirm-redispatch-button'));

    await waitFor(() => {
      expect(onChanged).toHaveBeenCalled();
    });
  });

  it('确认失败时按钮保留（可重试）', async () => {
    server.use(
      http.get('/api/v1/tasks/:id', () => HttpResponse.json(taskPayload())),
      http.post('/api/v1/tasks/:id/confirm-redispatch', () =>
        HttpResponse.json(
          { code: 201002, message: '任务当前状态为 running', data: null, request_id: 'r2' },
          { status: 409 },
        ),
      ),
    );
    const onChanged = vi.fn();

    renderDrawer({ onChanged });

    await userEvent.click(await screen.findByTestId('confirm-redispatch-button'));

    await waitFor(() => {
      // 失败不通知外层刷新，按钮仍在，用户可重试
      expect(onChanged).not.toHaveBeenCalled();
    });
    expect(screen.getByTestId('confirm-redispatch-button')).toBeInTheDocument();
  });
});
