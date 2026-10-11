import { useCallback, useEffect, useRef, useState } from 'react';

/** 可拖拽面板的默认宽度（像素） */
const DEFAULT_PANEL_WIDTH = 560;
/** 可拖拽面板的最小 / 最大宽度（像素） */
const MIN_PANEL_WIDTH = 320;
const MAX_PANEL_WIDTH = 900;

interface ResizablePanelOptions {
  /** 最小宽度（像素），缺省 320 */
  min?: number;
  /** 最大宽度（像素），缺省 900 */
  max?: number;
  /**
   * localStorage 键名：传入后宽度在拖拽结束时持久化、初始化时读回。
   * 缺省不持久化（保持既有调用方行为）。
   */
  storageKey?: string;
  /**
   * 拖拽方向：`'left'`（把手在左缘，向左拖变宽，缺省）或 `'right'`
   * （把手在右缘，向右拖变宽，用于「左树右预览」这类内部布局）。
   */
  direction?: 'left' | 'right';
}

interface ResizablePanelState {
  /** 面板当前宽度（像素） */
  width: number;
  /** 是否正在拖拽（用于渲染拖拽态样式与禁用文本选择） */
  isResizing: boolean;
  /** 拖拽把手 onMouseDown 事件处理器 */
  handleResizeStart: (event: React.MouseEvent<HTMLDivElement>) => void;
}

/**
 * 从 localStorage 读取已保存宽度（非法值返回 null）。
 */
function readStoredWidth(storageKey: string | undefined, min: number, max: number): number | null {
  if (!storageKey || typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return null;
    }
    const parsed = Number.parseInt(raw, 10);
    if (Number.isNaN(parsed)) {
      return null;
    }
    return Math.max(min, Math.min(max, parsed));
  } catch {
    // 隐私模式等场景 localStorage 不可用：静默回退默认宽度
    return null;
  }
}

/**
 * 面板水平拖拽调宽 Hook。
 *
 * 从 TaskNew 页面右侧面板抽取的通用逻辑：按住把手左右拖动调整宽度，
 * 宽度限制在 [min, max] 区间，拖拽期间监听 document 级 mousemove/mouseup，
 * 松开或离开窗口后自动解除监听。
 *
 * @param defaultWidth 默认宽度（像素），缺省 560
 * @param options 范围、持久化键与拖拽方向（缺省保持原行为）
 * @returns 面板宽度、拖拽状态与拖拽启动处理器
 */
export function useResizablePanel(
  defaultWidth: number = DEFAULT_PANEL_WIDTH,
  options: ResizablePanelOptions = {},
): ResizablePanelState {
  const min = options.min ?? MIN_PANEL_WIDTH;
  const max = options.max ?? MAX_PANEL_WIDTH;
  const storageKey = options.storageKey;
  const direction = options.direction ?? 'left';

  // 初始化即读回持久化宽度（惰性初始化，避免首帧跳变）
  const [width, setWidth] = useState(() => readStoredWidth(storageKey, min, max) ?? defaultWidth);
  const [isResizing, setIsResizing] = useState(false);
  /** 拖拽起始时鼠标的屏幕 X 坐标 */
  const resizeStartXRef = useRef(0);
  /** 拖拽起始时的面板宽度（用于计算增量） */
  const resizeStartWidthRef = useRef(0);
  /** 最新宽度引用：拖拽结束回调里读，避免闭包过期值 */
  const widthRef = useRef(width);

  // 渲染期不得写 ref（react-hooks/refs）：在 effect 中同步
  useEffect(() => {
    widthRef.current = width;
  }, [width]);

  /**
   * 开始拖拽：记录起始坐标与起始宽度。
   *
   * @param event 鼠标按下事件
   */
  const handleResizeStart = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsResizing(true);
      resizeStartXRef.current = event.clientX;
      resizeStartWidthRef.current = widthRef.current;
    },
    [],
  );

  useEffect(() => {
    if (!isResizing) {
      return undefined;
    }

    const handleMouseMove = (event: MouseEvent) => {
      // 把手在左缘：鼠标左移（delta > 0）面板变宽；把手在右缘：右移变宽
      const rawDelta = resizeStartXRef.current - event.clientX;
      const delta = direction === 'left' ? rawDelta : -rawDelta;
      const nextWidth = Math.max(min, Math.min(max, resizeStartWidthRef.current + delta));
      setWidth(nextWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      // 拖拽结束才写库：避免拖拽过程中高频写 localStorage
      if (storageKey) {
        try {
          window.localStorage.setItem(storageKey, String(widthRef.current));
        } catch {
          // 写入失败不影响交互（隐私模式等）
        }
      }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing, min, max, storageKey, direction]);

  return { width, isResizing, handleResizeStart };
}
