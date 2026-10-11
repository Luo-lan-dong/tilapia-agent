import { Component, type ReactNode } from 'react';
import { Button, Result } from 'antd';
import { reportError } from '@/utils/errorReporter';

/**
 * ErrorBoundary 属性定义。
 */
interface ErrorBoundaryProps {
  /** 子组件树 */
  children: ReactNode;
}

/**
 * ErrorBoundary 状态定义。
 */
interface ErrorBoundaryState {
  /** 是否已捕获到错误 */
  hasError: boolean;
}

/**
 * 全局错误边界组件。
 *
 * 捕获子组件树中未处理的 JavaScript 错误，上报到后端日志服务，
 * 并展示降级 UI 避免整个页面白屏。
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  /**
   * 捕获到错误时更新状态，触发 fallback UI 渲染。
   */
  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  /**
   * 捕获到错误后上报到后端日志服务。
   *
   * @param error - 抛出的错误对象。
   */
  componentDidCatch(error: Error): void {
    reportError({
      message: error.message,
      stack: error.stack,
      url: window.location.href,
      timestamp: new Date().toISOString(),
    });
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <Result
          status="error"
          title="页面出错了"
          subTitle="抱歉，页面发生了未知错误，请刷新页面重试。"
          extra={
            <Button type="primary" onClick={() => window.location.reload()}>
              刷新页面
            </Button>
          }
        />
      );
    }

    return this.props.children;
  }
}
