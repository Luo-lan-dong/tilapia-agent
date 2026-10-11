import { act } from 'react';
import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTaskAgentTrace } from '@/hooks/useTaskAgentTrace';
import { fetchTaskAgentTrace } from '@/api/tasks';
import type { AgentTraceListResponse, AgentTraceRow } from '@/types/task';

vi.mock('@/api/tasks', () => ({
  fetchTaskAgentTrace: vi.fn(),
}));

const mockedFetch = vi.mocked(fetchTaskAgentTrace);

let seqCounter = 0;

/** 构造一条轨迹行（seq 自增保证去重不吞） */
function makeRow(): AgentTraceRow {
  seqCounter += 1;
  return {
    seq: seqCounter,
    kind: 'text',
    payload: { text: `行 ${seqCounter}` },
    created_at: '2026-10-06T00:00:00Z',
  };
}

function makeResponse(rows: AgentTraceRow[]): AgentTraceListResponse {
  return { rows, has_more: false };
}

describe('useTaskAgentTrace 空结果低频重拉（2026-10-06 修⑤死锁）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedFetch.mockReset();
    seqCounter = 0;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('任务未终态且首轮回看为空：每 3s 重拉直到拿到首行，随后停止重拉', async () => {
    mockedFetch.mockResolvedValue(makeResponse([]));

    const { result } = renderHook(() => useTaskAgentTrace('task-1', false, true));

    // 首轮回看（空）
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mockedFetch).toHaveBeenCalledTimes(1);
    expect(result.current.rows).toEqual([]);

    // 低频重拉：每 3s 一次，仍空
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(mockedFetch).toHaveBeenCalledTimes(2);

    // 节点开始产出：重拉拿到首行
    mockedFetch.mockResolvedValue(makeResponse([makeRow()]));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(result.current.rows.length).toBe(1);

    // 拿到行后重拉停止（pollActive=false 也无高频轮询）：调用数不再增长
    const callsAfterFirstRow = mockedFetch.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(mockedFetch.mock.calls.length).toBe(callsAfterFirstRow);
  });

  it('任务已终态（waitActive=false）：空结果不做重拉', async () => {
    mockedFetch.mockResolvedValue(makeResponse([]));

    renderHook(() => useTaskAgentTrace('task-1', false, false));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mockedFetch).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });

  it('waitActive=true 但首轮回看即有数据：不进入重拉', async () => {
    mockedFetch.mockResolvedValue(makeResponse([makeRow()]));

    const { result } = renderHook(() => useTaskAgentTrace('task-1', false, true));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.rows.length).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });

  it('taskId 缺失：任何情况下都不拉取', async () => {
    renderHook(() => useTaskAgentTrace(undefined, false, true));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(mockedFetch).not.toHaveBeenCalled();
  });
});
