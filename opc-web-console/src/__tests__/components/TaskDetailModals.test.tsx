import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { App as AntApp } from 'antd';
import { describe, expect, it, vi } from 'vitest';
import { TaskDetailModals } from '@/components/TaskDetailModals';
import { server } from '@/test/setup';

/** 构造准入诊断响应 */
function eligibilityPayload(overrides: Record<string, unknown> = {}) {
  return {
    code: 0,
    message: 'ok',
    request_id: 'req-1',
    data: {
      task_id: 'task-1',
      target_node_id: null,
      forced_dispatch: false,
      eligible_count: 0,
      candidates: [
        {
          node_id: 'node-a',
          name: '笔记本',
          online: true,
          available_memory_gb: 0.7,
          load_score: 95.2,
          slots_free: 2,
          eligible: false,
          blocking: '可用内存 0.7GB ≤ 1.0GB',
          blocking_reasons: ['可用内存 0.7GB ≤ 1.0GB', '综合负载 95.2 ≥ 80.0'],
        },
      ],
      hint: '无节点处于健康水位：可指定节点排队等待，或明确要求强行派送',
      ...overrides,
    },
  };
}

function mockNodes() {
  server.use(
    http.get('/api/v1/agent-nodes', () =>
      HttpResponse.json({
        code: 0,
        message: 'ok',
        request_id: 'req-2',
        data: {
          items: [{ id: 'node-a', name: '笔记本', agent_type: 'code', status: 'idle', load: 31.6 }],
          pagination: { page: 1, page_size: 100, total: 1, total_pages: 1 },
        },
      }),
    ),
  );
}

function renderModals(props: Partial<Parameters<typeof TaskDetailModals>[0]> = {}) {
  return render(
    <AntApp>
      <TaskDetailModals
        taskId="task-1"
        agentType="code"
        rejectOpen={false}
        onCloseReject={vi.fn()}
        feedback=""
        onFeedbackChange={vi.fn()}
        rejecting={false}
        onReject={vi.fn()}
        manualOpen
        onCloseManual={vi.fn()}
        dispatching={false}
        onDispatch={vi.fn()}
        dispatchTarget=""
        onDispatchTargetChange={vi.fn()}
        {...props}
      />
    </AntApp>,
  );
}

describe('TaskDetailModals 手动派发（ADR-059）', () => {
  it('展示准入提示与所选节点的全部缺口（不只首要那条）', async () => {
    mockNodes();
    server.use(http.get('/api/v1/tasks/:id/eligibility', () => HttpResponse.json(eligibilityPayload())));

    renderModals({ dispatchTarget: 'node-a' });

    // 提示语来自后端 hint（可解释的等待）
    expect(
      await screen.findByText('无节点处于健康水位：可指定节点排队等待，或明确要求强行派送'),
    ).toBeInTheDocument();
    // 两个缺口并列展示，避免「解决内存后才发现负载也不达标」
    const reasons = await screen.findByTestId('dispatch-blocking-reasons');
    expect(reasons).toHaveTextContent('可用内存 0.7GB ≤ 1.0GB');
    expect(reasons).toHaveTextContent('综合负载 95.2 ≥ 80.0');
    expect(screen.getByText('不可自主')).toBeInTheDocument();
  });

  it('不勾选强行派送时以 force=false 提交（严格排队语义）', async () => {
    mockNodes();
    server.use(http.get('/api/v1/tasks/:id/eligibility', () => HttpResponse.json(eligibilityPayload())));
    const onDispatch = vi.fn();

    renderModals({ dispatchTarget: 'node-a', onDispatch });

    await userEvent.click(screen.getByRole('button', { name: '确认派发' }));

    expect(onDispatch).toHaveBeenCalledWith('node-a', false);
  });

  it('勾选强行派送后以 force=true 提交且按钮语义变化', async () => {
    mockNodes();
    server.use(http.get('/api/v1/tasks/:id/eligibility', () => HttpResponse.json(eligibilityPayload())));
    const onDispatch = vi.fn();

    renderModals({ dispatchTarget: 'node-a', onDispatch });

    await userEvent.click(screen.getByTestId('manual-dispatch-force-checkbox'));
    const okButton = await screen.findByRole('button', { name: '强行派送' });
    await userEvent.click(okButton);

    expect(onDispatch).toHaveBeenCalledWith('node-a', true);
  });

  it('准入诊断请求失败时不阻断派发（降级为只显示节点选择）', async () => {
    mockNodes();
    server.use(
      http.get('/api/v1/tasks/:id/eligibility', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })),
    );

    renderModals({ dispatchTarget: 'node-a' });

    await waitFor(() => {
      expect(screen.queryByTestId('dispatch-eligibility-hint')).not.toBeInTheDocument();
    });
    expect(screen.getByTestId('manual-dispatch-force-checkbox')).toBeInTheDocument();
  });
});
