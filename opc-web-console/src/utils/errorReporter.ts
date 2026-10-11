/** 前端错误日志上报工具。 */

/**
 * 错误上报数据结构。
 */
export interface ErrorReport {
  /** 错误消息 */
  message: string;
  /** 错误堆栈 */
  stack?: string;
  /** 触发错误的脚本文件 */
  filename?: string;
  /** 行号 */
  lineno?: number;
  /** 列号 */
  colno?: number;
  /** 发生错误时的页面 URL */
  url?: string;
  /** 上报时间（ISO 8601） */
  timestamp: string;
}

/**
 * 获取后端 API 基础地址，与 request.ts 保持一致。
 */
function getBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api';
}

/**
 * 上报错误到后端。
 *
 * 使用 fetch keepalive 确保页面卸载时仍能发送请求，
 * 上报失败时静默处理，不影响用户使用。
 */
export function reportError(error: ErrorReport): void {
  try {
    const url = `${getBaseUrl()}/v1/client-logs`;
    const body = JSON.stringify(error);

    void fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => {
      // 静默处理上报失败，不影响用户使用
    });
  } catch {
    // 静默处理构造请求时的异常
  }
}

/**
 * 初始化全局错误监听。
 *
 * 监听 window.onerror 和 unhandledrejection 事件，
 * 将未捕获的错误上报到后端。应在应用渲染前调用一次。
 */
export function initErrorReporter(): void {
  window.addEventListener('error', (event) => {
    const errorObj = event.error as Error | undefined;
    reportError({
      message: event.message,
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
      stack: errorObj?.stack,
      url: window.location.href,
      timestamp: new Date().toISOString(),
    });
  });

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    const errorLike = reason instanceof Error ? reason : undefined;
    reportError({
      message: errorLike?.message ?? String(reason),
      stack: errorLike?.stack,
      url: window.location.href,
      timestamp: new Date().toISOString(),
    });
  });
}
