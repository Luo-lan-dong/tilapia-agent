import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App as AntApp } from 'antd';
import { describe, expect, it, vi } from 'vitest';
import { TaskNewInputBar } from '@/components/TaskNewInputBar';

vi.mock('@/api/llmProvider', () => ({
  fetchLLMModels: vi.fn().mockResolvedValue([]),
  activateLLMModel: vi.fn().mockResolvedValue(undefined),
}));

function renderInputBar(overrides: Partial<Parameters<typeof TaskNewInputBar>[0]> = {}) {
  const props = {
    model: 'deepseek-chat',
    mode: 'thinking' as const,
    onModeChange: vi.fn(),
    executionMode: 'build' as const,
    onExecutionModeChange: vi.fn(),
    inputLength: 0,
    sending: false,
    canSend: true,
    onSend: vi.fn(),
    ...overrides,
  };
  return render(
    <AntApp>
      <TaskNewInputBar {...props} />
    </AntApp>,
  );
}

describe('TaskNewInputBar', () => {
  it('显示当前模型', () => {
    renderInputBar();
    expect(screen.getByTestId('current-model')).toHaveTextContent('deepseek-chat');
  });

  it('显示未连接当模型为空', () => {
    renderInputBar({ model: '' });
    expect(screen.getByTestId('current-model')).toHaveTextContent('未连接');
  });

  it('底栏按钮展示当前回答模式与执行模式组合', () => {
    renderInputBar();
    expect(screen.getByTestId('mode-switch')).toHaveTextContent('思考');
    expect(screen.getByTestId('mode-switch')).toHaveTextContent('直接开发');
  });

  it('模式下拉选择快速时触发回答模式回调', async () => {
    const onModeChange = vi.fn();
    renderInputBar({ onModeChange });

    await userEvent.click(screen.getByTestId('mode-switch'));
    await userEvent.click(screen.getByText('快速'));
    expect(onModeChange).toHaveBeenCalledWith('fast');
  });

  it('模式下拉选择先规划时触发执行模式回调', async () => {
    const onExecutionModeChange = vi.fn();
    renderInputBar({ onExecutionModeChange });

    await userEvent.click(screen.getByTestId('mode-switch'));
    await userEvent.click(screen.getByText('先规划'));
    expect(onExecutionModeChange).toHaveBeenCalledWith('plan');
  });

  it('canSend 为假时发送按钮禁用', () => {
    renderInputBar({ canSend: false });
    expect(screen.getByTestId('send-button')).toBeDisabled();
  });

  it('点击发送按钮触发回调', async () => {
    const onSend = vi.fn();
    renderInputBar({ onSend });

    await userEvent.click(screen.getByTestId('send-button'));
    expect(onSend).toHaveBeenCalled();
  });

  it('发送等待态展示回答中按钮并禁用', () => {
    renderInputBar({ sending: true });
    expect(screen.getByTestId('send-button-busy')).toBeDisabled();
    expect(screen.getByTestId('send-button-busy')).toHaveTextContent('回答中');
    expect(screen.queryByTestId('send-button')).not.toBeInTheDocument();
  });

  it('展示输入字数计数', () => {
    renderInputBar({ inputLength: 12 });
    expect(screen.getByTestId('input-char-count')).toHaveTextContent('12/2000');
  });
});
