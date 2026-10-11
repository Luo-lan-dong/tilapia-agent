import { renderHook } from '@testing-library/react';
import { App as AntApp } from 'antd';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { useTaskPlanActions } from '@/hooks/useTaskPlanActions';
import { server } from '@/test/setup';
import type { Task } from '@/types/task';

function wrapper({ children }: { children: ReactNode }) {
  return <AntApp>{children}</AntApp>;
}

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-1',
    title: '示例任务',
    agent_type: 'code',
    status: 'pending',
    progress: 0,
    source: 'spec_chat',
    created_at: '2026-08-15T00:00:00Z',
    updated_at: '2026-08-15T00:00:00Z',
    ...overrides,
  };
}

/** 记录最近一次 POST /dispatch 的请求体，用于断言 force 是否被正确传递 */
function captureDispatch(body: Record<string, unknown>) {
  const captured: { body: unknown } = { body: null };
  server.use(
    http.post('/api/v1/tasks/:id/dispatch', async ({ request }) => {
      captured.body = await request.json();
      return HttpResponse.json({ code: 0, message: 'ok', request_id: 'r', data: body });
    }),
  );
  return captured;
}

function renderActions(detail: Task | null) {
  const onPatchDetail = vi.fn();
  const onChanged = vi.fn();
  const onCloseDispatchModal = vi.fn();
  const { result } = renderHook(
    () =>
      useTaskPlanActions({
        detail,
        onPatchDetail,
        onChanged,
        onCloseRejectModal: vi.fn(),
        onCloseDispatchModal,
      }),
    { wrapper },
  );
  return { result, onPatchDetail, onChanged, onCloseDispatchModal };
}

/** 取出 hook 传给 onPatchDetail 的更新函数并应用到给定任务上 */
function applyPatch(onPatchDetail: ReturnType<typeof vi.fn>, base: Task): Task | null {
  const updater = onPatchDetail.mock.calls[0][0] as (prev: Task | null) => Task | null;
  return updater(base);
}

const DISPATCHED_BODY = {
  task_id: 'task-1',
  target: 'code',
  status: 'assigned',
  dispatch_type: 'manual',
  queued_on_node: null,
  hold_reason: null,
};

const QUEUED_BODY = {
  task_id: 'task-1',
  target: 'code',
  status: 'pending',
  dispatch_type: 'auto',
  queued_on_node: '笔记本',
  hold_reason: '指定节点「笔记本」暂不可自主：可用内存 0.7GB ≤ 1.0GB（可等待或要求强行派送）',
};

describe('useTaskPlanActions 手动派发（ADR-059）', () => {
  it('节点达标并派送成功时置 assigned 且不排队', async () => {
    captureDispatch(DISPATCHED_BODY);
    const { result, onPatchDetail, onCloseDispatchModal } = renderActions(makeTask());

    await result.current.handleManualDispatch('node-a', false);

    const patched = applyPatch(onPatchDetail, makeTask());
    expect(patched?.status).toBe('assigned');
    expect(patched?.assigned_node_id).toBe('node-a');
    expect(patched?.dispatch_type).toBe('manual');
    expect(patched?.dispatch_hold_reason).toBeNull();
    expect(onCloseDispatchModal).toHaveBeenCalled();
  });

  it('节点不达标而排队时**不得谎报已指派**：保持 pending 并记录排队原因', async () => {
    captureDispatch(QUEUED_BODY);
    const { result, onPatchDetail } = renderActions(makeTask());

    await result.current.handleManualDispatch('node-a', false);

    // 关键回归：修复前无论后端返回什么都会写成 assigned + assigned_node_id
    const patched = applyPatch(onPatchDetail, makeTask());
    expect(patched?.status).toBe('pending');
    expect(patched?.assigned_node_id).toBeNull();
    // 指定节点被记住（调度器每 10 秒复检它）
    expect(patched?.target_node_id).toBe('node-a');
    expect(patched?.dispatch_hold_reason).toContain('暂不可自主');
  });

  it('不勾选强行派送时请求体 force=false（保证严格排队语义）', async () => {
    const captured = captureDispatch(QUEUED_BODY);
    const { result } = renderActions(makeTask());

    await result.current.handleManualDispatch('node-a', false);

    expect(captured.body).toMatchObject({ node_id: 'node-a', force: false });
  });

  it('勾选强行派送时请求体 force=true 并记录 forced_dispatch', async () => {
    const captured = captureDispatch(DISPATCHED_BODY);
    const { result, onPatchDetail } = renderActions(makeTask());

    await result.current.handleManualDispatch('node-a', true);

    expect(captured.body).toMatchObject({ node_id: 'node-a', force: true });
    expect(applyPatch(onPatchDetail, makeTask())?.forced_dispatch).toBe(true);
  });

  it('请求失败时提示错误且不改动任务状态', async () => {
    server.use(
      http.post('/api/v1/tasks/:id/dispatch', () =>
        HttpResponse.json(
          { code: 201002, message: '任务已处于终态', data: null, request_id: 'r4' },
          { status: 409 },
        ),
      ),
    );
    const { result, onPatchDetail, onCloseDispatchModal } = renderActions(makeTask());

    await result.current.handleManualDispatch('node-a', false);

    expect(onPatchDetail).not.toHaveBeenCalled();
    // 失败时弹窗保持打开，便于用户改选节点
    expect(onCloseDispatchModal).not.toHaveBeenCalled();
  });
});
