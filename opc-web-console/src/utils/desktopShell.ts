/**
 * 桌面壳（pywebview）环境检测与原生对话框桥接（ADR-028）。
 *
 * 浏览器出于安全限制拿不到本机文件夹绝对路径；仅桌面版中枢
 * （launcher/pywebview）通过 js_api 暴露原生「选择文件夹」对话框。
 */

interface PywebviewApi {
  select_folder?: () => Promise<string | null>;
  begin_move_window?: () => Promise<null>;
  toggle_maximize_window?: () => Promise<null>;
  minimize_window?: () => Promise<null>;
  close_window?: () => Promise<null>;
}

interface PywebviewGlobal {
  api?: PywebviewApi;
}

declare global {
  interface Window {
    pywebview?: PywebviewGlobal;
  }
}

/** 当前是否运行在桌面壳内（pywebview 已注入 JS 桥）。 */
export function isDesktopShell(): boolean {
  return typeof window !== 'undefined' && Boolean(window.pywebview?.api?.select_folder);
}

/**
 * 打开原生「选择文件夹」对话框，返回所选绝对路径；取消返回 null。
 *
 * 仅桌面壳内可用；浏览器调用将抛错（调用方应先用 isDesktopShell 判定
 * 或捕获后降级为手动输入）。
 */
export async function pickWorkspaceFolder(): Promise<string | null> {
  const api = window.pywebview?.api;
  if (!api?.select_folder) {
    throw new Error('当前环境不支持文件夹选择，请使用桌面版中枢或直接输入路径');
  }
  return api.select_folder();
}

/**
 * 开始拖动桌面壳主窗口（桌面壳无系统标题栏，mousedown 时由系统接管移动）。
 * 仅桌面壳内可用，调用方静默忽略失败。
 */
export function beginMoveDesktopWindow(): void {
  window.pywebview?.api?.begin_move_window?.().catch(() => {
    // 桥未就绪或非桌面壳：静默忽略
  });
}

/**
 * 最大化/还原桌面壳主窗口（按窗口当前真实状态切换，双击顶部区域同效）。
 */
export function toggleMaximizeDesktopWindow(): void {
  window.pywebview?.api?.toggle_maximize_window?.().catch(() => {
    // 桥未就绪或非桌面壳：静默忽略
  });
}

/**
 * 最小化桌面壳主窗口（右上角窗口控制按钮调用）。
 */
export function minimizeDesktopWindow(): void {
  window.pywebview?.api?.minimize_window?.().catch(() => {
    // 桥未就绪或非桌面壳：静默忽略
  });
}

/**
 * 关闭桌面壳主窗口（后端组件栈随窗口关闭统一清理退出）。
 */
export function closeDesktopWindow(): void {
  window.pywebview?.api?.close_window?.().catch(() => {
    // 桥未就绪或非桌面壳：静默忽略
  });
}
