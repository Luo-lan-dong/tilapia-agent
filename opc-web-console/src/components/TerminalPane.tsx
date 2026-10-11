/* 单终端实例（ADR-053；多标签 2026-09-28）：xterm + WS 双向流。
   xterm 主题色从 CSS 变量读取（防回潮：组件内禁止颜色字面量）。 */
import { useEffect, useRef } from 'react';
import { Terminal, type ITheme } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

/** 读主题 CSS 变量（缺省用命名色，防回潮检查不拦命名色） */
function cssVar(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** 从当前主题 CSS 变量构建 xterm 配色 */
function buildXtermTheme(): ITheme {
  return {
    background: cssVar('--bg-base', 'black'),
    foreground: cssVar('--text-primary', 'white'),
    cursor: cssVar('--primary', 'green'),
    selectionBackground: cssVar('--text-secondary', 'gray'),
  };
}

interface TerminalPaneProps {
  /** PTY 会话键（`{sessionId}::t{n}`） */
  tabKey: string;
  /** 会话工作目录（PTY 初始 cwd，仅首次创建生效） */
  workDir: string | null;
}

/**
 * 单个终端标签面板：挂载时连接 /api/v1/terminal/ws（键=tabKey），
 * 卸载断开（服务端 PTY 与环形缓冲保留，重挂载 replay 回放）。
 */
export function TerminalPane({ tabKey, workDir }: TerminalPaneProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);

  // 主题切换跟随：html[data-theme] 属性变化时按新 CSS 变量重刷 xterm 配色
  // （用 MutationObserver 而非 React 状态——父层 Provider 的 effect 晚于子组件，
  //  状态驱动会在属性落盘前读到旧变量值）
  useEffect(() => {
    const applyTheme = () => {
      if (termRef.current) {
        termRef.current.options.theme = buildXtermTheme();
      }
    };
    const observer = new MutationObserver(applyTheme);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!hostRef.current) {
      return;
    }
    const term = new Terminal({
      fontFamily: 'Consolas, "Courier New", monospace',
      fontSize: 12,
      cursorBlink: true,
      theme: buildXtermTheme(),
    });
    termRef.current = term;
    // ConPTY 启动时会发 DA/DSR 终端能力查询；xterm 内置自动响应
    // （如 `\x1b[?1;2c`）会经服务端写进 PowerShell 输入行被当作命令
    // 执行（屏幕出现 [?1;2c 乱码 + ParserError）。此处拦截查询并标记
    // 已处理，阻止内置响应（PSReadLine 不依赖这些响应）。
    term.parser.registerCsiHandler({ final: 'c' }, () => true);
    term.parser.registerCsiHandler({ final: 'n' }, () => true);
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    term.writeln('\x1b[90m正在连接终端…\x1b[0m');

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const wd = workDir ? `&work_dir=${encodeURIComponent(workDir)}` : '';
    const ws = new WebSocket(
      `${proto}://${window.location.host}/api/v1/terminal/ws?session_id=${encodeURIComponent(tabKey)}${wd}`,
    );
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as { type: string; data?: string; detail?: string };
        if (msg.type === 'output') {
          term.write(msg.data ?? '');
        } else if (msg.type === 'error') {
          term.writeln(`\r\n\x1b[31m${msg.detail ?? '终端错误'}\x1b[0m`);
        }
      } catch {
        /* 忽略非法消息 */
      }
    };
    ws.onclose = () => term.writeln('\r\n\x1b[90m[连接已断开]\x1b[0m');
    ws.onopen = () => {
      // PowerShell 就绪约需 1s：连接后延迟补发 resize 触发 PSReadLine
      // 首屏渲染提示符（否则需手动敲回车才显示，易误判为卡住）。
      window.setTimeout(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
        }
      }, 800);
    };
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }));
      }
    });
    const doFit = () => {
      fit.fit();
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      }
    };
    window.addEventListener('resize', doFit);
    const observer = new ResizeObserver(doFit);
    observer.observe(hostRef.current);
    const fitTimer = window.setTimeout(doFit, 300);

    return () => {
      window.clearTimeout(fitTimer);
      window.removeEventListener('resize', doFit);
      observer.disconnect();
      ws.close();
      term.dispose();
      termRef.current = null;
    };
  }, [tabKey, workDir]);

  return <div ref={hostRef} className="terminal-drawer__host" data-testid="terminal-host" />;
}
