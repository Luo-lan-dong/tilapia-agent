import { act } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { NodeTraceBlock } from '@/components/NodeTraceBlock';
import type { AgentTraceRow } from '@/types/task';

function makeRow(seq: number, kind: AgentTraceRow['kind'], payload: Record<string, unknown>): AgentTraceRow {
  return { seq, kind, payload, created_at: '2026-10-02T00:00:00Z', node_id: 'node-1' };
}

const SAMPLE_ROWS: AgentTraceRow[] = [
  makeRow(1, 'status', { stage: '启动', message: '任务已派发到节点' }),
  makeRow(2, 'reasoning', { text: '先看项目结构再动手' }),
  makeRow(3, 'tool', { tool: 'bash', phase: 'called', call_id: 'c1', target: 'npm test' }),
  makeRow(4, 'tool', {
    tool: 'bash',
    phase: 'success',
    call_id: 'c1',
    summary: '3 passed',
    exit_code: 0,
  }),
  makeRow(5, 'text', { text: '开发完成' }),
];

describe('NodeTraceBlock（ADR-063 M3）', () => {
  it('默认折叠：块头显示来源标注与当前动作，轨迹内容不渲染', () => {
    render(
      <NodeTraceBlock nodeId="node-1" nodeName="编程节点" taskName="登录页" progress={40} rows={SAMPLE_ROWS} />,
    );

    const header = screen.getByTestId('node-trace-header');
    expect(header).toHaveTextContent('节点');
    expect(header).toHaveTextContent('编程节点');
    expect(header).toHaveTextContent('登录页');
    expect(header).toHaveTextContent('40%');
    expect(header).toHaveTextContent('5 行');
    // 默认折叠：行内容不可见
    expect(screen.queryByTestId('node-trace-row-1')).not.toBeInTheDocument();
  });

  it('展开后按 seq 渲染思考/工具/正文行（只读，无输入框）', async () => {
    const user = userEvent.setup();
    render(<NodeTraceBlock nodeId="node-1" rows={SAMPLE_ROWS} />);

    await user.click(screen.getByTestId('node-trace-header'));

    expect(await screen.findByTestId('node-trace-row-2')).toHaveTextContent('先看项目结构再动手');
    expect(screen.getByTestId('node-trace-row-3')).toHaveTextContent('bash');
    expect(screen.getByTestId('node-trace-row-3')).toHaveTextContent('调用');
    expect(screen.getByTestId('node-trace-row-4')).toHaveTextContent('完成');
    expect(screen.getByTestId('node-trace-row-4')).toHaveTextContent('退出码 0');
    expect(screen.getByTestId('node-trace-row-5')).toHaveTextContent('开发完成');
    // D2 只读：折叠块内不提供任何输入
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('展开变化回调父层（据此启停实时轮询）', async () => {
    const user = userEvent.setup();
    const onExpandChange = vi.fn();
    render(<NodeTraceBlock nodeId="node-1" rows={SAMPLE_ROWS} onExpandChange={onExpandChange} />);

    await user.click(screen.getByTestId('node-trace-header'));
    expect(onExpandChange).toHaveBeenLastCalledWith(true);

    await user.click(screen.getByTestId('node-trace-header'));
    expect(onExpandChange).toHaveBeenLastCalledWith(false);
  });

  it('上滚暂停跟随并提示有新输出，点击提示恢复跟随', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<NodeTraceBlock nodeId="node-1" rows={SAMPLE_ROWS} />);

    await user.click(screen.getByTestId('node-trace-header'));
    await screen.findByTestId('node-trace-row-5');

    // 模拟上滚离开底部（jsdom 无布局，直接注入几何值后派发 scroll）
    const rowsEl = document.querySelector('.node-trace__rows') as HTMLElement;
    Object.defineProperty(rowsEl, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(rowsEl, 'clientHeight', { value: 200, configurable: true });
    act(() => {
      rowsEl.scrollTop = 0;
      rowsEl.dispatchEvent(new Event('scroll', { bubbles: true }));
    });

    // 暂停时刻与当前行数一致 → 暂无新输出提示
    expect(screen.queryByTestId('node-trace-new-pill')).not.toBeInTheDocument();

    // 暂停后有新行到达 → 显示「有新输出」
    rerender(
      <NodeTraceBlock
        nodeId="node-1"
        rows={[...SAMPLE_ROWS, makeRow(6, 'text', { text: '补充说明' })]}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('node-trace-new-pill')).toBeInTheDocument());

    await user.click(screen.getByTestId('node-trace-new-pill'));
    expect(screen.queryByTestId('node-trace-new-pill')).not.toBeInTheDocument();
  });

  it('超过 200 行时展示「加载更早」，点击后补齐显示窗口', async () => {
    const user = userEvent.setup();
    const manyRows = Array.from({ length: 260 }, (_, index) =>
      makeRow(index + 1, 'status', { stage: '执行', message: `动作 ${index + 1}` }),
    );
    render(<NodeTraceBlock nodeId="node-1" rows={manyRows} />);

    await user.click(screen.getByTestId('node-trace-header'));

    const button = await screen.findByTestId('node-trace-load-earlier');
    expect(button).toHaveTextContent('还有 60 行');
    // 默认只渲染最近 200 行
    expect(screen.queryByTestId('node-trace-row-1')).not.toBeInTheDocument();
    expect(screen.getByTestId('node-trace-row-260')).toBeInTheDocument();

    await user.click(button);
    expect(screen.getByTestId('node-trace-row-1')).toBeInTheDocument();
    expect(screen.queryByTestId('node-trace-load-earlier')).not.toBeInTheDocument();
  });
});
