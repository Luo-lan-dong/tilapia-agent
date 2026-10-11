import { useEffect, useState } from 'react';
import { isDesktopShell } from '@/utils/desktopShell';

/**
 * 检测当前是否运行在桌面壳（pywebview）内。
 *
 * pywebview 的 JS 桥在页面加载后才异步注入，因此先做一次初始检测，
 * 未注入时监听 ``pywebviewready`` 事件二次确认，避免首帧漏判。
 *
 * @returns 是否处于桌面壳环境
 */
export function useDesktopShell(): boolean {
  const [isDesktop, setIsDesktop] = useState(() => isDesktopShell());

  useEffect(() => {
    if (isDesktop) {
      return undefined;
    }
    const onReady = () => setIsDesktop(isDesktopShell());
    window.addEventListener('pywebviewready', onReady);
    return () => window.removeEventListener('pywebviewready', onReady);
  }, [isDesktop]);

  return isDesktop;
}
