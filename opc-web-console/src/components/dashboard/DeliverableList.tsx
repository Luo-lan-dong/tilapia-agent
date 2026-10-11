import { Button, Empty, Typography } from 'antd';
import {
  DownloadOutlined,
  EyeOutlined,
  FileTextOutlined,
  FileZipOutlined,
  PictureOutlined,
  PlayCircleOutlined,
  CodeOutlined,
} from '@ant-design/icons';
import type { DeliverableFileType, DeliverableItem } from '@/types/dashboard';
import { COLORS } from '@/theme/tokens';
import { downloadArtifactsUrl } from '@/api/tasks';

const { Text } = Typography;

/**
 * 文件类型到图标的映射
 */
const FILE_TYPE_ICON_MAP: Record<DeliverableFileType, typeof FileTextOutlined> = {
  image: PictureOutlined,
  video: PlayCircleOutlined,
  zip: FileZipOutlined,
  doc: FileTextOutlined,
  code: CodeOutlined,
  other: FileTextOutlined,
};

/**
 * 字节数人性化展示
 */
function formatSize(size: number): string {
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (size >= 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${size} B`;
}

/**
 * DeliverableList 属性定义
 */
interface DeliverableListProps {
  /** 交付物列表数据 */
  items: DeliverableItem[];
  /** 是否加载中 */
  loading: boolean;
  /** 点击「查看」打开任务详情 */
  onViewTask?: (taskId: string) => void;
}

/**
 * 交付物聚合列表（ADR-038 仪表盘 v2 交付物 Tab 内容）。

每条展示文件类型图标 + 文件名 + 节点·修改时间，支持下载（复用
ADR-028 产物打包下载）与查看（打开任务详情抽屉）。
 */
export function DeliverableList({ items, loading, onViewTask }: DeliverableListProps) {
  if (!loading && items.length === 0) {
    return (
      <div style={{ padding: 32 }}>
        <Empty
          description={<Text type="secondary">暂无交付物，任务完成后产物将聚合展示在这里</Text>}
        />
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {items.map((item) => {
        const Icon = FILE_TYPE_ICON_MAP[item.file_type] ?? FileTextOutlined;
        const meta = [item.node_name, formatSize(item.size)].filter(Boolean).join(' · ');
        return (
          <div
            key={`${item.task_id}:${item.path}`}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              background: COLORS.surface,
              border: `1px solid ${COLORS.borderFaint}`,
              borderRadius: 8,
              padding: '8px 12px',
            }}
          >
            <Icon style={{ color: COLORS.primary, fontSize: 18, flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <Text
                style={{ color: COLORS.textPrimary, fontSize: 13 }}
                ellipsis={{ tooltip: item.path }}
              >
                {item.file_name}
              </Text>
              <Text
                style={{ display: 'block', color: COLORS.textMuted, fontSize: 11 }}
                ellipsis={{ tooltip: `${item.task_title}（${meta}）` }}
              >
                {item.task_title} · {meta}
              </Text>
            </div>
            <Button
              type="text"
              size="small"
              icon={<DownloadOutlined />}
              title="下载任务产物"
              onClick={() => window.open(downloadArtifactsUrl(item.task_id), '_blank')}
            />
            <Button
              type="text"
              size="small"
              icon={<EyeOutlined />}
              title="查看任务详情"
              disabled={!onViewTask}
              onClick={() => onViewTask?.(item.task_id)}
            />
          </div>
        );
      })}
    </div>
  );
}
