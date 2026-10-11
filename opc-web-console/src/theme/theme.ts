/**
 * 主题态存储与应用（ADR-052）。
 *
 * 主题选择持久化于 localStorage（键 opc:theme），并通过
 * html[data-theme] 属性驱动 index.css 中语义 CSS 变量的双态切换。
 */
export type ThemeName = 'dark' | 'light';

/** localStorage 键名（与 index.html 防闪烁脚本约定一致） */
export const THEME_STORAGE_KEY = 'opc:theme';

/** 读取初始主题：localStorage 优先，不可用或值非法时回退暗色 */
export function getInitialTheme(): ThemeName {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') {
      return stored;
    }
  } catch {
    // 隐私模式等场景静默降级
  }
  return 'dark';
}

/** 将主题应用到 html[data-theme]，驱动 CSS 变量双态 */
export function applyTheme(name: ThemeName): void {
  document.documentElement.dataset.theme = name;
}
