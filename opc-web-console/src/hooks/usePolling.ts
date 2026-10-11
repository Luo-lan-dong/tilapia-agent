import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * 轮询 Hook 配置项
 */
interface UsePollingOptions<T> {
  /** 异步数据获取函数 */
  fetcher: () => Promise<T>;
  /** 轮询间隔，单位毫秒，默认 5000ms */
  interval?: number;
  /** 页面不可见时是否暂停轮询，默认 true */
  pauseWhenHidden?: boolean;
}

/**
 * 轮询 Hook 返回值
 */
interface UsePollingResult<T> {
  /** 最新获取的数据 */
  data: T | null;
  /** 是否处于首次加载或刷新中 */
  loading: boolean;
  /** 最近一次请求的错误 */
  error: Error | null;
  /** 手动触发刷新 */
  refresh: () => void;
}

/** 默认轮询间隔：5 秒 */
const DEFAULT_INTERVAL_MS = 5000;

/**
 * 通用数据轮询 Hook。
 *
 * 组件挂载时立即请求一次，并按指定间隔持续轮询；
 * 组件卸载或配置变更时自动清理定时器；
 * 页面切到后台时自动暂停轮询，切回前台时立即刷新一次。
 */
export function usePolling<T>({
  fetcher,
  interval = DEFAULT_INTERVAL_MS,
  pauseWhenHidden = true,
}: UsePollingOptions<T>): UsePollingResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasInitialLoadedRef = useRef<boolean>(false);
  // fetcher 存 ref（修复 2026-09-24）：调用方常以内联箭头传入，引用每次
  // 渲染变化。若直接作为 execute/effect 依赖，父组件任意 setState（如并行的
  // metrics 轮询）都会 abort 重启轮询——慢查询（交付物聚合 >5s）永远在完成前
  // 被取消，loading 卡死、界面永久空白。ref 模式下 execute 引用稳定，
  // 轮询仅在 interval 变化时重启。
  const fetcherRef = useRef(fetcher);
  // 渲染期同步最新 fetcher（React 官方推荐的 ref 同步模式）：
  // execute 始终调用最新闭包，同时保持自身引用稳定
  fetcherRef.current = fetcher;

  const execute = useCallback(
    async (signal?: AbortSignal, isManualRefresh = false) => {
      // 只有首次加载或手动刷新时才展示 loading，避免轮询时反复闪烁
      if (!hasInitialLoadedRef.current || isManualRefresh) {
        setLoading(true);
      }
      try {
        const result = await fetcherRef.current();
        if (signal?.aborted) {
          return;
        }
        setData(result);
        setError(null);
        hasInitialLoadedRef.current = true;
      } catch (err) {
        if (signal?.aborted) {
          return;
        }
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    []
  );

  const refresh = useCallback(() => {
    void execute(undefined, true);
  }, [execute]);

  /**
   * 启动轮询：创建 AbortController + 立即执行一次 + 开启定时器
   */
  const startPolling = useCallback(() => {
    // 清理旧的
    if (controllerRef.current) {
      controllerRef.current.abort();
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }

    const controller = new AbortController();
    controllerRef.current = controller;

    // 立即执行一次，但延迟到当前 effect 结束后再触发，避免在 effect 内同步 setState
    timeoutRef.current = setTimeout(() => {
      void execute(controller.signal);
    }, 0);

    // 开启定时轮询
    timerRef.current = setInterval(() => {
      void execute(controller.signal);
    }, interval);
  }, [execute, interval]);

  /**
   * 停止轮询：取消请求 + 清除定时器
   */
  const stopPolling = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    if (controllerRef.current) {
      controllerRef.current.abort();
      controllerRef.current = null;
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // 主轮询 effect：依赖变化时重启轮询
  useEffect(() => {
    startPolling();
    return stopPolling;
  }, [startPolling, stopPolling]);

  // 页面可见性 effect：切后台暂停，切前台立即刷新
  useEffect(() => {
    if (!pauseWhenHidden) {
      return;
    }

    const handleVisibilityChange = () => {
      if (document.hidden) {
        // 页面不可见：暂停轮询，节省资源
        stopPolling();
      } else {
        // 页面重新可见：立即刷新一次，然后恢复轮询
        startPolling();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [pauseWhenHidden, startPolling, stopPolling]);

  return { data, loading, error, refresh };
}
