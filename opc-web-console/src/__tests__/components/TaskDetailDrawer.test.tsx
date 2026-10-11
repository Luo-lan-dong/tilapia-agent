import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { App as AntApp } from 'antd';
import { describe, expect, it, vi } from 'vitest';
import { TaskDetailDrawer } from '@/components/TaskDetailDrawer';
import { server } from '@/test/setup';

function renderDrawer(props: Partial<Parameters<typeof TaskDetailDrawer>[0]> = {}) {
  return render(
    <AntApp>
      <TaskDetailDrawer open taskId="task-plan-1" onClose={vi.fn()} {...props} />
    </AntApp>,
  );
}

describe('TaskDetailDrawer', () => {
  it('加载任务详情并展示方案全文与审批按钮', async () => {
    renderDrawer();

    await waitFor(() => {
      expect(screen.getByTestId('plan-content')).toHaveTextContent('# 实施方案');
    });
    expect(screen.getByText('先规划')).toBeInTheDocument();
    expect(screen.getByTestId('approve-plan-button')).toBeInTheDocument();
    expect(screen.getByTestId('reject-plan-button')).toBeInTheDocument();
  });

  it('build 模式任务不显示审批按钮', async () => {
    server.use(
      http.get('/api/v1/tasks/:id', () => {
        return HttpResponse.json({
          code: 0,
          message: 'ok',
          data: {
            id: 'task-build-1',
            title: '直接开发任务',
            agent_type: 'programming',
            status: 'completed',
            progress: 100,
            source: 'spec_chat',
            execution_mode: 'build',
            change_brief: null,
            plan_content: null,
            parent_task_id: null,
            session_id: 'ses_task_demo',
            created_at: '2026-08-15T00:00:00Z',
            updated_at: '2026-08-15T00:00:00Z',
          },
        });
      }),
    );
    renderDrawer();

    await waitFor(() => {
      expect(screen.getByText('直接开发任务')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('approve-plan-button')).not.toBeInTheDocument();
    expect(screen.queryByTestId('reject-plan-button')).not.toBeInTheDocument();
  });

  it('确认批准方案后回调刷新并隐藏审批按钮', async () => {
    const onChanged = vi.fn();
    renderDrawer({ onChanged });

    await waitFor(() => {
      expect(screen.getByTestId('approve-plan-button')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId('approve-plan-button'));
    // antd 会给两字中文按钮插入空格（渲染为「批 准」），用锚定正则精确匹配 Popconfirm 确认键
    await userEvent.click(screen.getByRole('button', { name: /^批\s*准$/ }));

    await waitFor(() => {
      expect(onChanged).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.queryByTestId('approve-plan-button')).not.toBeInTheDocument();
    });
  });

  it('驳回方案需要填写反馈，提交后回调刷新', async () => {
    const onChanged = vi.fn();
    renderDrawer({ onChanged });

    await waitFor(() => {
      expect(screen.getByTestId('reject-plan-button')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId('reject-plan-button'));

    const okButton = screen.getByRole('button', { name: '提交驳回' });
    expect(okButton).toBeDisabled();

    await userEvent.type(screen.getByRole('textbox'), '请补充重试逻辑');
    await userEvent.click(okButton);

    await waitFor(() => {
      expect(onChanged).toHaveBeenCalled();
    });
  });
});
