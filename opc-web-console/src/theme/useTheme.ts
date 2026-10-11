import { useContext } from 'react';
import { ThemeContext } from './ThemeContext';

/** 消费主题态；必须在 ThemeProvider 内使用 */
export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error('useTheme must be used within ThemeProvider');
  }
  return ctx;
}
