import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePolling } from '@/hooks/usePolling';

/**
 * 通用轮询 Hook 单测。
 *
 * 覆盖核心轮询流程：立即首拉、周期轮询、手动刷新、失败容错、
 * 卸载清理与页面可见性暂停/恢复（coding-standards 第 9 节）。
 */
describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('挂载后立即请求一次并返回数据', async () => {
    const fetcher = vi.fn().mockResolvedValue('data-1');
    const { result } = renderHook(() => usePolling<string>({ fetcher, interval: 1000 }));

    // 首拉经 setTimeout(0) 延迟到 effect 结束后触发
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.data).toBe('data-1');
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('按 interval 周期持续轮询', async () => {
    const fetcher = vi.fn().mockResolvedValue('data');
    renderHook(() => usePolling<string>({ fetcher, interval: 1000 }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it('请求失败时记录 error 且 data 保持为空', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => usePolling<string>({ fetcher, interval: 1000 }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe('network down');
    expect(result.current.data).toBeNull();
  });

  it('失败后恢复成功时清除 error 并写入 data', async () => {
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce('recovered');
    const { result } = renderHook(() => usePolling<string>({ fetcher, interval: 1000 }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.error).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.data).toBe('recovered');
    expect(result.current.error).toBeNull();
  });

  it('refresh 手动触发刷新并更新数据', async () => {
    const fetcher = vi
      .fn<() => Promise<string>>()
      .mockResolvedValueOnce('v1')
      .mockResolvedValueOnce('v2');
    const { result } = renderHook(() => usePolling<string>({ fetcher, interval: 60_000 }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.data).toBe('v1');

    await act(async () => {
      result.current.refresh();
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.data).toBe('v2');
  });

  it('卸载后停止轮询', async () => {
    const fetcher = vi.fn().mockResolvedValue('data');
    const { unmount } = renderHook(() => usePolling<string>({ fetcher, interval: 1000 }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('页面切后台暂停轮询，切回前台立即刷新', async () => {
    const fetcher = vi.fn().mockResolvedValue('data');
    renderHook(() => usePolling<string>({ fetcher, interval: 1000 }));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      Object.defineProperty(document, 'hidden', { value: true, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => {
      Object.defineProperty(document, 'hidden', { value: false, configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    // 切回前台的重启轮询同样经 setTimeout(0) 延迟触发，需推进定时器
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
