import { Typography } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import { COLORS } from '@/theme/tokens';

const { Text } = Typography;

interface CommandItem {
  name: string;
  desc: string;
}

/** 可用斜杠命令清单（后端 /spec 确定性执行，见 ADR-036） */
const COMMANDS: CommandItem[] = [
  { name: '/spec', desc: '强制生成规范文档三件套（spec / tasks / checklist）' },
];

interface TaskNewCommandPaletteProps {
  /** 选中命令回调（回填输入框，不直接发送） */
  onSelect: (command: string) => void;
}

/**
 * 斜杠命令提示面板（ADR-036）。
 *
 * 输入框内容以 ``/`` 开头时由 TaskNewChatPanel 条件渲染；点击命令回填
 * 输入框并保持聚焦，由用户确认后发送。
 */
export function TaskNewCommandPalette({ onSelect }: TaskNewCommandPaletteProps) {
  return (
    <div className="task-new__cmd-palette" data-testid="command-palette">
      {COMMANDS.map((cmd) => (
        <button
          key={cmd.name}
          type="button"
          className="task-new__cmd-item"
          onClick={() => onSelect(cmd.name)}
        >
          <ThunderboltOutlined style={{ color: COLORS.primary }} />
          <span className="task-new__cmd-name">{cmd.name}</span>
          <Text style={{ color: COLORS.textSecondary }} className="task-new__cmd-desc">
            {cmd.desc}
          </Text>
        </button>
      ))}
    </div>
  );
}
