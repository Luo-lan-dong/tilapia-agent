import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { TaskInsightPanel } from '@/components/TaskInsightPanel';
import type { TaskInsight } from '@/types/task';

/** 构造洞察数据（默认：有用量、无告警、两条时间线） */
function makeInsight(overrides: Partial<TaskInsight> = {}): TaskInsight {
  return {
    task_id: 'task-1',
    usage: [],
    summary: {
      steps: 4,
      input_tokens: 400,
      output_tokens: 40,
      cache_read_tokens: 1600,
      cache_write_tokens: 0,
      cache_hit_rate: 0.8,
      cache_hit_baseline: 0.952,
      hit_rate_below_baseline: true,
      files_touched: 2,
    },
    alerts: [],
    timeline: [
      {
        kind: 'agent_task_event',
        name: 'tool_activity',
        title: '执行动作：edit',
        detail: '正在处理 src/main.py',
        status: 'success',
      },
      {
        kind: 'steering',
        name: 'task_steer_sent',
        title: '中枢发言',
        detail: '请先跑测试',
        status: 'success',
      },
    ],
    ...overrides,
  };
}

describe('TaskInsightPanel（ADR-061 M5c）', () => {
  it('无洞察数据时不渲染（不占位、不影响上段）', () => {
    const { container } = render(<TaskInsightPanel insight={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('展示用量汇总与基线对比（低于基线时给出提示）', () => {
    render(<TaskInsightPanel insight={makeInsight()} />);

    const usage = screen.getByTestId('task-insight-usage');
    expect(usage).toHaveTextContent('4 轮');
    expect(usage).toHaveTextContent('440 tokens');
    expect(usage).toHaveTextContent('缓存命中 80%');
  });

  it('展示告警与最近动作时间线（倒序，最新在前）', () => {
    render(
      <TaskInsightPanel
        insight={makeInsight({
          alerts: [
            {
              code: 'no_file_write',
              level: 'warning',
              title: '长时间没有文件产出',
              detail: '最近 4 个统计点均无文件变更',
            },
          ],
        })}
      />,
    );

    const alerts = screen.getByTestId('task-insight-alerts');
    expect(alerts).toHaveTextContent('长时间没有文件产出');
    expect(alerts).toHaveTextContent('最近 4 个统计点均无文件变更');

    const timeline = screen.getByTestId('task-insight-timeline');
    expect(timeline).toHaveTextContent('中枢发言');
    expect(timeline).toHaveTextContent('执行动作：edit');
    // 最新在前
    expect(timeline.textContent?.indexOf('中枢发言')).toBeLessThan(
      timeline.textContent?.indexOf('执行动作：edit') ?? 0,
    );
  });

  it('无用量统计点时给出明确空态（而非 0 轮）', () => {
    render(
      <TaskInsightPanel
        insight={makeInsight({
          summary: {
            steps: 0,
            input_tokens: 0,
            output_tokens: 0,
            cache_read_tokens: 0,
            cache_write_tokens: 0,
            cache_hit_rate: 0,
            cache_hit_baseline: 0.952,
            hit_rate_below_baseline: false,
            files_touched: 0,
          },
        })}
      />,
    );

    expect(screen.getByText('暂无用量的统计点')).toBeInTheDocument();
  });

  it('@节点发言：输入并提交后回调收到内容并清空输入框', async () => {
    const sent: string[] = [];
    render(
      <TaskInsightPanel
        insight={makeInsight()}
        onSteer={(text) => {
          sent.push(text);
        }}
      />,
    );

    const input = screen.getByLabelText('向节点发言');
    await userEvent.type(input, '请不要动数据库迁移');
    await userEvent.click(screen.getByRole('button', { name: /发言/ }));

    await waitFor(() => expect(sent).toEqual(['请不要动数据库迁移']));
    expect((input as HTMLInputElement).value).toBe('');
  });

  it('未提供发言回调时不渲染输入框（只读场景）', () => {
    render(<TaskInsightPanel insight={makeInsight()} />);
    expect(screen.queryByTestId('task-insight-steer')).not.toBeInTheDocument();
  });
});
