import type { ToolKey } from '@/components/TaskNewToolDefs';
import { TOOLS } from '@/components/TaskNewToolDefs';

interface TaskToolEmptyProps {
  /** 点击工具入口（打开对应工具） */
  onOpenTool: (key: ToolKey) => void;
}

/**
 * 工具面板空态：未打开任何工具时展示入口列表（参考图二「从这里开始」）。
 */
export function TaskToolEmpty({ onOpenTool }: TaskToolEmptyProps) {
  return (
    <div className="task-new__tool-empty" data-testid="tool-empty">
      <div className="task-new__tool-empty-title">从这里开始</div>
      {TOOLS.map((tool) => (
        <button
          key={tool.key}
          type="button"
          className="task-new__tool-entry"
          onClick={() => onOpenTool(tool.key)}
          data-testid={`tool-entry-${tool.key}`}
        >
          <span className="task-new__tool-entry-icon">{tool.icon}</span>
          <span className="task-new__tool-entry-text">
            <span className="task-new__tool-entry-title">{tool.title}</span>
            <span className="task-new__tool-entry-desc">{tool.description}</span>
          </span>
        </button>
      ))}
    </div>
  );
}
