/**
 * 终端抽屉全局状态（ADR-053；多标签扩展 2026-09-28）。
 *
 * 终端抽屉挂在 AppLayout（全局可用），但打开入口有两处：顶栏终端图标
 * 与 TaskNew 页 AI 执行命令时自动展开（SSE command_executing）。用
 * useSyncExternalStore 模块级 store 共享，避免跨层级 props 透传。
 *
 * 多标签：tabs 为 PTY 会话键列表（`{sessionId}::t{n}`），首签为会话
 * 主终端；服务端按键各自维护独立 PTY，关闭标签仅断开前端连接。
 */

import { useSyncExternalStore } from 'react';

export interface TerminalUiState {
  open: boolean;
  /** 当前对话会话 ID（PTY 会话键前缀；无会话页面为 null → 全局终端） */
  sessionId: string | null;
  /** 会话工作目录（TaskNew 选择的目录；PTY 初始 cwd） */
  workDir: string | null;
  /** 终端标签（PTY 会话键）列表，首个为会话主终端 */
  tabs: string[];
  /** 当前激活标签 */
  activeTab: string | null;
  /** 重连计数器：递增触发当前标签终端重挂载（免关抽屉闪烁） */
  reconnectNonce: number;
}

const BASE_TAB_SUFFIX = '::t';

/** 会话主终端的 PTY 键 */
function baseTab(sessionId: string | null): string {
  return sessionId || 'global';
}

let state: TerminalUiState = {
  open: false,
  sessionId: null,
  workDir: null,
  tabs: [],
  activeTab: null,
  reconnectNonce: 0,
};
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

function patch(next: Partial<TerminalUiState>): void {
  state = { ...state, ...next };
  emit();
}

export const terminalStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  getSnapshot(): TerminalUiState {
    return state;
  },
  setOpen(open: boolean): void {
    if (!open) {
      patch({ open: false });
      return;
    }
    // 打开时确保至少有一个标签（会话切换后重建标签组）
    const base = baseTab(state.sessionId);
    const tabs = state.tabs.filter((tab) => tab.startsWith(base));
    const nextTabs = tabs.length > 0 ? tabs : [base];
    patch({
      open: true,
      tabs: nextTabs,
      activeTab: state.activeTab && nextTabs.includes(state.activeTab) ? state.activeTab : nextTabs[0],
    });
  },
  /** 同步对话会话上下文（TaskNew 切换会话时调用） */
  setSession(sessionId: string | null, workDir: string | null): void {
    if (sessionId === state.sessionId && workDir === state.workDir) {
      return;
    }
    if (sessionId !== state.sessionId) {
      // 会话切换：标签组按新会话重建；抽屉开着时立即给出新会话首签
      const base = baseTab(sessionId);
      patch({
        sessionId,
        workDir,
        tabs: state.open ? [base] : [],
        activeTab: state.open ? base : null,
      });
      return;
    }
    // 仅工作目录变化：保留标签组（终端因 workDir prop 变化自动重连）
    patch({ sessionId, workDir });
  },
  /** 重连当前标签：递增 nonce 使 TerminalPane 重挂载（重放服务端缓冲） */
  reconnectActive(): void {
    patch({ reconnectNonce: state.reconnectNonce + 1 });
  },
  /** 新建标签：`{base}::t{n}`，n 取未占用最小序号；返回新标签键 */
  addTab(): string {
    const base = baseTab(state.sessionId);
    const used = new Set(
      state.tabs
        .filter((tab) => tab.startsWith(base))
        .map((tab) => Number(tab.slice(base.length + BASE_TAB_SUFFIX.length)))
        .filter((num) => Number.isInteger(num)),
    );
    let index = 1;
    while (used.has(index)) {
      index += 1;
    }
    const tab = `${base}${BASE_TAB_SUFFIX}${index}`;
    patch({ tabs: [...state.tabs, tab], activeTab: tab });
    return tab;
  },
  /** 关闭标签；关掉激活标签时切到相邻标签，全部关闭则收起抽屉 */
  closeTab(tab: string): void {
    const index = state.tabs.indexOf(tab);
    if (index < 0) {
      return;
    }
    const tabs = state.tabs.filter((item) => item !== tab);
    if (tabs.length === 0) {
      patch({ open: false, tabs: [], activeTab: null });
      return;
    }
    const activeTab =
      state.activeTab === tab ? tabs[Math.min(index, tabs.length - 1)] : state.activeTab;
    patch({ tabs, activeTab });
  },
  setActiveTab(tab: string): void {
    patch({ activeTab: tab });
  },
};

/** 订阅终端 UI 状态（组件内使用） */
export function useTerminalState(): TerminalUiState {
  return useSyncExternalStore(terminalStore.subscribe, terminalStore.getSnapshot);
}
