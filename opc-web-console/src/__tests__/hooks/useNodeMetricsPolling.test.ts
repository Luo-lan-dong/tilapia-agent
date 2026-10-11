import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useNodeMetricsPolling } from '@/hooks/useNodeMetricsPolling';
import { getAgentNodeById, getAgentNodeMetrics } from '@/api/agentNodes';
import type { AgentNode, AgentNodeMetricsResponse } from '@/types/agentNode';

vi.mock('@/api/agentNodes', () => ({
  getAgentNodeById: vi.fn(),
  getAgentNodeMetrics: vi.fn(),
}));

const mockedGetNode = vi.mocked(getAgentNodeById);
const mockedGetMetrics = vi.mocked(getAgentNodeMetrics);

const mockMessage = {
  error: vi.fn(),
} as unknown as Parameters<typeof useNodeMetricsPolling>[1];

const nodeFixture = {
  id: 'node-1',
  name: '节点一',
  status: 'idle',
} as unknown as AgentNode;

const metricsFixture = {
  metrics: { cpu: { percent: 12.5 } },
} as unknown as AgentNodeMetricsResponse;

/**
 * 节点监控轮询 Hook 单测（复用 usePolling 重构后的回归保障）。
 */
describe('useNodeMetricsPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mockedGetNode.mockResolvedValue(nodeFixture);
    mockedGetMetrics.mockResolvedValue(metricsFixture);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('挂载后加载节点详情与监控快照', async () => {
    const { result } = renderHook(() => useNodeMetricsPolling('node-1', mockMessage));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(mockedGetNode).toHaveBeenCalledWith('node-1');
    expect(mockedGetMetrics).toHaveBeenCalledWith('node-1');
    expect(result.current.node).toEqual(nodeFixture);
    expect(result.current.metricsData).toEqual(metricsFixture);
    expect(result.current.nodeLoading).toBe(false);
    expect(result.current.metricsLoading).toBe(false);
  });

  it('首次监控快照刷新后记录最近刷新时间', async () => {
    const { result } = renderHook(() => useNodeMetricsPolling('node-1', mockMessage));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.lastUpdatedAt).not.toBe('');
  });

  it('按 5 秒间隔轮询监控快照，节点详情仅加载一次', async () => {
    renderHook(() => useNodeMetricsPolling('node-1', mockMessage));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mockedGetMetrics).toHaveBeenCalledTimes(1);
    expect(mockedGetNode).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(mockedGetMetrics).toHaveBeenCalledTimes(2);
    expect(mockedGetNode).toHaveBeenCalledTimes(1);
  });

  it('监控快照拉取失败时经 message.error 提示且 data 保持为空', async () => {
    mockedGetMetrics.mockRejectedValue(new Error('metrics down'));
    const { result } = renderHook(() => useNodeMetricsPolling('node-1', mockMessage));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(mockMessage.error).toHaveBeenCalledWith('metrics down');
    expect(result.current.metricsData).toBeNull();
  });

  it('节点详情加载失败时经 message.error 提示', async () => {
    mockedGetNode.mockRejectedValue(new Error('node down'));
    const { result } = renderHook(() => useNodeMetricsPolling('node-1', mockMessage));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(mockMessage.error).toHaveBeenCalledWith('node down');
    expect(result.current.node).toBeNull();
  });

  it('nodeId 缺失时不发起任何请求', async () => {
    renderHook(() => useNodeMetricsPolling(undefined, mockMessage));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(mockedGetNode).not.toHaveBeenCalled();
    expect(mockedGetMetrics).not.toHaveBeenCalled();
  });

  it('refreshMetrics 手动触发一次监控刷新', async () => {
    const { result } = renderHook(() => useNodeMetricsPolling('node-1', mockMessage));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(mockedGetMetrics).toHaveBeenCalledTimes(1);

    await act(async () => {
      result.current.refreshMetrics();
    });
    expect(mockedGetMetrics).toHaveBeenCalledTimes(2);
  });
});
