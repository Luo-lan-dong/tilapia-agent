import { createContext } from 'react';
import type { ThemeName } from './theme';

export interface ThemeContextValue {
  /** 当前主题态 */
  theme: ThemeName;
  /** 切换主题（同时持久化到 localStorage） */
  setTheme: (next: ThemeName) => void;
}

/** 主题 Context（与 Provider/hook 分文件导出，满足 react-refresh 约束） */
export const ThemeContext = createContext<ThemeContextValue | null>(null);
