import { useDesktopShell } from '@/hooks/useDesktopShell';
import {
  closeDesktopWindow,
  minimizeDesktopWindow,
  toggleMaximizeDesktopWindow,
} from '@/utils/desktopShell';
import './WindowControls.css';

/**
 * 桌面壳窗口控制按钮（仅桌面版渲染）：右上角最小化/最大化还原/关闭。
 *
 * 桌面壳窗口无系统标题栏，三个按钮经 pywebview js_api 调用原生
 * 窗口控制；浏览器访问时（非桌面壳）不渲染。按钮为原生 button，
 * Header 的拖动 mousedown 已排除 button 目标，点击不会误触发拖动。
 */
export function WindowControls() {
  const isDesktop = useDesktopShell();
  if (!isDesktop) {
    return null;
  }

  return (
    <div className="window-controls">
      <button
        type="button"
        className="window-controls__btn"
        aria-label="最小化"
        title="最小化"
        onClick={minimizeDesktopWindow}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M1 6h10" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      </button>
      <button
        type="button"
        className="window-controls__btn"
        aria-label="最大化/还原"
        title="最大化/还原"
        onClick={toggleMaximizeDesktopWindow}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <rect x="1.5" y="1.5" width="9" height="9" rx="1" fill="none" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      </button>
      <button
        type="button"
        className="window-controls__btn window-controls__btn--close"
        aria-label="关闭"
        title="关闭"
        onClick={closeDesktopWindow}
      >
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M1.5 1.5l9 9M10.5 1.5l-9 9" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      </button>
    </div>
  );
}
