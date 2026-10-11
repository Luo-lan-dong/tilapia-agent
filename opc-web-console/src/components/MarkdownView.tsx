/**
 * Markdown 展示视图（渲染 / 源码切换 + 复制）。
 *
 * 统一产物预览、三件套文档、方案全文等处的 Markdown 呈现：默认**渲染视图**，
 * 可一键切到**源码视图**并复制原文。切换状态是组件内部的非受控状态
 * （`resetKey` 变化时重置为默认视图，用于「切换文件/文档」场景）。
 */
import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { App, Button, Tooltip } from 'antd';
import { CodeOutlined, CopyOutlined, EyeOutlined } from '@ant-design/icons';
import { renderMarkdown } from '@/components/Markdown';
import './MarkdownView.css';

interface MarkdownViewProps {
  /** Markdown 源文本 */
  content: string;
  /**
   * 重置键：变化时把视图重置为「渲染」（如切换文件、切换文档 Tab）。
   * 缺省不重置（同一份文档内的切换保持用户选择）。
   */
  resetKey?: string;
  /** 是否显示右侧工具按钮（复制；有源码/渲染切换时一并显示） */
  showActions?: boolean;
  /** 是否允许切换到源码视图（默认允许） */
  allowSourceToggle?: boolean;
  /** 源码视图是否显示行号（默认显示） */
  showLineNumbers?: boolean;
  /** 是否支持复制内容（默认支持） */
  allowCopy?: boolean;
  /** 渲染视图的空态提示 */
  emptyText?: string;
  /** 外层容器样式（高度/滚动等由调用方决定） */
  style?: CSSProperties;
  className?: string;
  /** 渲染容器的 data-testid（如产物预览的 artifacts-preview） */
  testId?: string;
}

/**
 * 把源码按行渲染为带行号的视图（模块私有：避免与组件混导出触发
 * react-refresh/only-export-components）。
 */
function renderSourceWithLineNumbers(content: string): ReactNode {
  return content.split('\n').map((line, index) => (
    // 行号视图：纯文本按行渲染，便于定位
    <div className="mdv-source-line" key={`ln-${index}`}>
      <span className="mdv-source-no">{index + 1}</span>
      <span className="mdv-source-text">{line || ' '}</span>
    </div>
  ));
}

/**
 * Markdown 展示视图：默认渲染，可切源码，可复制。
 */
export function MarkdownView({
  content,
  resetKey,
  showActions = true,
  allowSourceToggle = true,
  showLineNumbers = true,
  allowCopy = true,
  emptyText,
  style,
  className,
  testId,
}: MarkdownViewProps) {
  const { message } = App.useApp();
  const [showSource, setShowSource] = useState(false);
  // 非受控视图 + resetKey 重置：切换文档/文件时回到渲染视图
  const [prevResetKey, setPrevResetKey] = useState(resetKey);
  if (resetKey !== prevResetKey) {
    setPrevResetKey(resetKey);
    setShowSource(false);
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      message.success('已复制内容');
    } catch {
      message.warning('复制失败：浏览器未授权剪贴板');
    }
  };

  const actions = showActions && (
    <div className="mdv-actions">
      {allowSourceToggle && (
        <Tooltip title={showSource ? '切换到渲染视图' : '切换到源码视图'}>
          <Button
            size="small"
            type="text"
            icon={showSource ? <EyeOutlined /> : <CodeOutlined />}
            onClick={() => setShowSource((prev) => !prev)}
            data-testid="mdv-view-toggle"
          >
            {showSource ? '渲染' : '源码'}
          </Button>
        </Tooltip>
      )}
      {allowCopy && (
        <Tooltip title="复制原文">
          <Button
            size="small"
            type="text"
            icon={<CopyOutlined />}
            onClick={() => void handleCopy()}
            aria-label="复制原文"
          />
        </Tooltip>
      )}
    </div>
  );

  return (
    <div className={`mdv${className ? ` ${className}` : ''}`} style={style}>
      {showActions && <div className="mdv-head">{actions}</div>}
      {content.trim() ? (
        showSource ? (
          <pre className="mdv-source" data-testid={testId}>
            {showLineNumbers ? renderSourceWithLineNumbers(content) : content}
          </pre>
        ) : (
          <div data-testid={testId}>{renderMarkdown(content)}</div>
        )
      ) : (
        <div className="mdv-empty">{emptyText ?? '暂无内容'}</div>
      )}
    </div>
  );
}
