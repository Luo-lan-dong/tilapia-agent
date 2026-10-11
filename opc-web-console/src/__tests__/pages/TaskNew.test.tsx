import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { App as AntApp } from 'antd';
import { describe, expect, it, vi } from 'vitest';
import { TaskNew } from '@/pages/TaskNew';

function renderTaskNew(initialEntries?: string[]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AntApp>
        <TaskNew />
      </AntApp>
    </MemoryRouter>,
  );
}

describe('TaskNew', () => {
  it('默认显示思考模式和当前模型', async () => {
    renderTaskNew();
    await waitFor(() => {
      expect(screen.getByTestId('current-model')).toHaveTextContent('deepseek-chat');
    });
    // 2026-09-13 布局改版：模式切换合并为输入底栏下拉，按钮文案展示「回答模式 · 执行模式」
    expect(screen.getByTestId('mode-switch')).toHaveTextContent('思考');
  });

  it('用户输入并发送后展示流式 AI 回复并启用创建任务', async () => {
    // bc37dc0 起右侧面板在视口宽度过窄（innerWidth-200 < 920）时默认折叠，
    // jsdom 默认宽 1024 会触发折叠导致工具面板不在 DOM，此处模拟宽视口
    const innerWidthSpy = vi
      .spyOn(window, 'innerWidth', 'get')
      .mockReturnValue(1600);
    try {
      renderTaskNew();

      // 2026-09-13 工具面板改版：「创建任务」按钮随文档工具视图渲染，需先打开规范文档
      await userEvent.click(screen.getByTestId('tool-entry-spec'));

      const input = screen.getByPlaceholderText('描述你的任务需求，例如：开发一个用户登录接口');
      await userEvent.type(input, '开发一个登录接口');
      await userEvent.click(screen.getByRole('button', { name: 'send 发送' }));

      await waitFor(() => {
        expect(screen.getByText('开发一个登录接口')).toBeInTheDocument();
      });

      await waitFor(
        () => {
          expect(screen.getByText(/了解了/)).toBeInTheDocument();
        },
        { timeout: 3000 },
      );

      await waitFor(
        () => {
          expect(screen.getByRole('button', { name: '创建任务' })).not.toBeDisabled();
        },
        { timeout: 3000 },
      );
    } finally {
      innerWidthSpy.mockRestore();
    }
  });

  it('思考模式下展示可收起的思考内容', async () => {
    renderTaskNew();

    const input = screen.getByPlaceholderText('描述你的任务需求，例如：开发一个用户登录接口');
    await userEvent.type(input, '开发一个登录接口');
    await userEvent.click(screen.getByRole('button', { name: 'send 发送' }));

    await waitFor(
      () => {
        expect(screen.getByText('思考内容')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    await waitFor(
      () => {
        expect(screen.getByText(/我需要分析这个登录接口需求/)).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  it('新建会话后仍显示当前模型', async () => {
    renderTaskNew();

    await waitFor(() => {
      expect(screen.getByTestId('current-model')).toHaveTextContent('deepseek-chat');
    });

    await userEvent.click(screen.getByTestId('history-button'));
    await waitFor(() => {
      expect(screen.getByText('历史会话示例')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole('button', { name: /新建会话/ }));

    expect(screen.getByTestId('current-model')).toHaveTextContent('deepseek-chat');
  });

  it('打开历史会话抽屉并加载列表', async () => {
    renderTaskNew();

    await userEvent.click(screen.getByTestId('history-button'));

    await waitFor(
      () => {
        expect(screen.getByText('历史会话示例')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  it('URL 带 ?session= 时自动恢复历史会话（刷新/切页回来）', async () => {
    renderTaskNew(['/tasks/new?session=session-1']);

    await waitFor(
      () => {
        expect(screen.getByText('历史需求')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });

  it('任务迭代模式下点击新建会话应清除迭代状态', async () => {
    renderTaskNew(['/tasks/new?parent=task-123']);

    // 文案随 bc37dc0 响应式布局改造更新，迭代状态以 parent-context 上下文条标识
    await waitFor(() => {
      expect(screen.getByTestId('parent-context')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId('history-button'));
    await waitFor(() => {
      expect(screen.getByText('历史会话示例')).toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole('button', { name: /新建会话/ }));

    await waitFor(() => {
      expect(screen.queryByTestId('parent-context')).not.toBeInTheDocument();
    });
  });
});
