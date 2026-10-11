import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { applyTheme, getInitialTheme, THEME_STORAGE_KEY, type ThemeName } from './theme';
import { ThemeContext } from './ThemeContext';

/**
 * 主题 Provider：持有主题态，变化时同步 html[data-theme]（CSS 变量双态）
 * 与 localStorage 持久化。App.tsx 的 ConfigProvider 据此切换 antd 算法与 token。
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((next: ThemeName) => {
    setThemeState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // localStorage 不可用时内存态依然生效
    }
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
