import { Button, Drawer, Empty, Popconfirm, Spin, Tag } from 'antd';
import {
  DeleteOutlined,
  FileAddOutlined,
  LoginOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { TaskBuildingSessionSummary } from '@/types/taskBuilding';

interface TaskSessionDrawerProps {
  open: boolean;
  sessions: TaskBuildingSessionSummary[];
  loading: boolean;
  /** 当前工作目录下的任务 ID 集合（ADR-028）：传入时仅展示这些任务的会话 */
  filterTaskIds?: Set<string>;
  onClose: () => void;
  onRefresh: () => void;
  onNewSession: () => void;
  onContinue: (session: TaskBuildingSessionSummary) => void;
  onDelete: (session: TaskBuildingSessionSummary) => void;
}

/**
 * 历史会话抽屉。
 *
 * 展示历史会话列表，支持刷新、新建、继续和删除；
 * 指定 ``filterTaskIds`` 时仅显示当前工作目录相关任务的会话（ADR-028）。
 */
export function TaskSessionDrawer({
  open,
  sessions,
  loading,
  filterTaskIds,
  onClose,
  onRefresh,
  onNewSession,
  onContinue,
  onDelete,
}: TaskSessionDrawerProps) {
  const visibleSessions =
    filterTaskIds === undefined
      ? sessions
      : sessions.filter((session) => session.task_id !== null && filterTaskIds.has(session.task_id));
  const filtered = filterTaskIds !== undefined;

  return (
    <Drawer
      title="历史会话"
      placement="right"
      width={420}
      open={open}
      onClose={onClose}
      extra={
        <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>
          刷新
        </Button>
      }
    >
      {filtered && (
        <div style={{ marginBottom: 12 }}>
          <Tag color="green">仅显示当前工作目录相关会话</Tag>
        </div>
      )}

      <Button
        type="dashed"
        block
        icon={<FileAddOutlined />}
        onClick={onNewSession}
        style={{ marginBottom: 16 }}
      >
        新建会话
      </Button>

      <Spin spinning={loading}>
        {visibleSessions.length === 0 ? (
          <Empty
            description={
              filtered ? '当前工作目录下暂无历史会话' : '暂无历史会话'
            }
          />
        ) : (
          visibleSessions.map((session) => (
            <div key={session.id} className="task-new__session-item">
              <div className="task-new__session-info">
                <div className="task-new__session-title">{session.title}</div>
                <div className="task-new__session-meta">
                  {formatDate(session.updated_at)} ·{' '}
                  <Tag color={session.mode === 'thinking' ? 'blue' : 'green'}>
                    {session.mode === 'thinking' ? '思考' : '快速'}
                  </Tag>
                  {session.model && <Tag>{session.model}</Tag>}
                </div>
              </div>
              <div className="task-new__session-actions">
                <Button
                  icon={<LoginOutlined />}
                  onClick={() => onContinue(session)}
                  data-testid={`continue-session-${session.id}`}
                />
                <Popconfirm
                  title="删除会话"
                  description="删除后无法恢复，是否继续？"
                  onConfirm={() => onDelete(session)}
                  okText="删除"
                  cancelText="取消"
                >
                  <Button icon={<DeleteOutlined />} danger />
                </Popconfirm>
              </div>
            </div>
          ))
        )}
      </Spin>
    </Drawer>
  );
}

function formatDate(isoString: string): string {
  const date = new Date(isoString);
  return date.toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
