/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：任务表格的列定义、
   状态渲染与行操作（查看 / 删除）同属任务列表展示职责，拆分后列定义与操作回调
   仍需强耦合，故整体豁免。 */
import { type Key, useCallback, useMemo } from 'react';
import { App, Button, Popconfirm, Progress, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import type { ColumnsType } from 'antd/es/table';
import type { Task, TaskStatus } from '@/types/task';
import { deleteTask } from '@/api/tasks';
import { COLORS } from '@/theme/tokens';

/**
 * TaskTable 属性定义
 */
interface TaskTableProps {
  /** 任务列表数据 */
  data: Task[];
  /** 是否加载中 */
  loading: boolean;
  /** 点击「详情」查看任务详情的回调，未提供时不渲染详情按钮 */
  onViewDetail?: (taskId: string) => void;
  /** 删除成功后的回调（外层刷新列表），未提供时不渲染删除按钮 */
  onChanged?: () => void;
}

const { Text } = Typography;

/**
 * 任务状态到展示文案的映射
 */
const STATUS_LABEL_MAP: Record<TaskStatus, string> = {
  pending: '待分配',
  assigned: '已分配',
  running: '执行中',
  review: '待确认',
  completed: '已完成',
  failed: '失败',
  requeue_pending: '待确认改派',
  verify_blocked: '待验收决定',
};

/**
 * 任务状态到 Tag 颜色的映射
 */
const STATUS_COLOR_MAP: Record<TaskStatus, string> = {
  pending: 'default',
  assigned: 'blue',
  running: 'cyan',
  review: 'warning',
  completed: 'success',
  failed: 'error',
  requeue_pending: 'warning',
  verify_blocked: 'warning',
};

/**
 * 截取任务 ID 前 8 位用于展示。
 */
function formatTaskId(id: string): string {
  return id.slice(0, 8);
}

/**
 * 任务列表表格组件。
 *
 * 展示任务 ID、任务名称、Agent 类型、状态、分配节点（名称）、进度、派发模式；
 * 状态列使用 Tag 组件，进度列使用 Progress 组件。
 * 「派发」列始终显示 auto / manual 标签（不再受 source 限制，见 ADR-017）。
 */
export function TaskTable({ data, loading, onViewDetail, onChanged }: TaskTableProps) {
  const navigate = useNavigate();
  const { message } = App.useApp();

  /** 删除任务（终态/待分配任务；running/assigned 由后端拒绝并提示）。 */
  const handleDelete = useCallback(
    async (taskId: string) => {
      try {
        await deleteTask(taskId);
        message.success('任务已删除');
        onChanged?.();
      } catch (err) {
        message.error(err instanceof Error ? err.message : '删除失败');
      }
    },
    [message, onChanged],
  );

  const columns = useMemo<ColumnsType<Task>>(
    () => [
      {
        title: '任务 ID',
        dataIndex: 'id',
        key: 'id',
        width: 120,
        render: (id: string) => (
          <Text code style={{ color: COLORS.textPrimary, background: COLORS.codeBg }}>
            {formatTaskId(id)}
          </Text>
        ),
      },
      {
        title: '任务名称',
        dataIndex: 'title',
        key: 'title',
        ellipsis: true,
        render: (title: string) => (
          <Text ellipsis={{ tooltip: title }} style={{ color: COLORS.textPrimary }}>
            {title}
          </Text>
        ),
      },
      {
        title: 'Agent 类型',
        dataIndex: 'agent_type',
        key: 'agent_type',
        width: 120,
        render: (agentType: string) => <Text style={{ color: COLORS.textSecondary }}>{agentType}</Text>,
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 100,
        render: (status: TaskStatus) => (
          <Tag color={STATUS_COLOR_MAP[status]}>{STATUS_LABEL_MAP[status]}</Tag>
        ),
      },
      {
        title: '分配节点',
        dataIndex: 'assigned_node_name',
        key: 'assigned_node_name',
        width: 180,
        render: (nodeName: string | undefined, record: Task) => {
          // 排队可见（ADR-059 D3/D5）：pending 有排队原因、或待确认改派时，
          // 说明「在等哪台、缺什么 / 在等谁确认」，不再只显示一个「—」让用户猜
          const isRequeueConfirm = record.status === 'requeue_pending';
          if ((record.status === 'pending' || isRequeueConfirm) && record.dispatch_hold_reason) {
            const waitingFor = record.target_node_name ?? '可用节点';
            return (
              <Tooltip title={record.dispatch_hold_reason}>
                <Text style={{ color: COLORS.textSecondary }}>
                  <Tag color="orange" style={{ marginInlineEnd: 4 }}>
                    {isRequeueConfirm ? '待确认' : '排队中'}
                  </Tag>
                  {isRequeueConfirm ? '等待确认改派' : `等待 ${waitingFor}`}
                </Text>
              </Tooltip>
            );
          }
          return (
            <Text
              title={record.assigned_node_id}
              style={{ color: nodeName ? COLORS.textPrimary : COLORS.textMuted }}
            >
              {nodeName || '—'}
            </Text>
          );
        },
      },
      {
        title: '进度',
        dataIndex: 'progress',
        key: 'progress',
        width: 120,
        render: (progress: number, record: Task) => (
          <Progress
            percent={progress}
            size="small"
            strokeColor={record.status === 'failed' ? COLORS.danger : COLORS.primary}
            trailColor={COLORS.borderFaint}
            showInfo={false}
          />
        ),
      },
      {
        title: '派发',
        dataIndex: 'dispatch_type',
        key: 'dispatch_type',
        width: 90,
        render: (dispatchType: 'auto' | 'manual' | undefined) => {
          if (!dispatchType) {
            return <Text style={{ color: COLORS.textMuted }}>—</Text>;
          }
          return (
            <Tag color={dispatchType === 'manual' ? 'green' : 'blue'}>
              {dispatchType === 'manual' ? '手动' : '自动'}
            </Tag>
          );
        },
      },
      {
        title: '操作',
        key: 'actions',
        width: 210,
        render: (_: unknown, record: Task) => {
          // 已完成/失败的任务支持创建迭代任务，继续修改既有代码
          const canContinue = record.status === 'completed' || record.status === 'failed';
          // 执行中/已分配任务禁止删除（后端同样校验，ADR-028 期增强）
          const canDelete = record.status !== 'running' && record.status !== 'assigned';
          return (
            <Space size={4}>
              {onViewDetail && (
                <Button size="small" type="link" style={{ padding: 0 }}
                  onClick={() => onViewDetail(record.id)} data-testid={`view-detail-${record.id}`}>
                  详情
                </Button>
              )}
              {canContinue && (
                <Button size="small" type="link" style={{ padding: 0 }}
                  onClick={() => navigate(`/tasks/new?parent=${record.id}`)}
                  data-testid={`continue-task-${record.id}`}>
                  继续修改
                </Button>
              )}
              {canDelete && onChanged && (
                <Popconfirm
                  title="删除任务"
                  description="删除后无法恢复（历史会话保留），是否继续？"
                  okText="删除"
                  cancelText="取消"
                  onConfirm={() => void handleDelete(record.id)}
                >
                  <Button size="small" type="link" danger style={{ padding: 0 }}
                    data-testid={`delete-task-${record.id}`}>
                    删除
                  </Button>
                </Popconfirm>
              )}
            </Space>
          );
        },
      },
    ],
    [onViewDetail, navigate, onChanged, handleDelete]
  );

  const rowKey = (record: Task): Key => record.id;

  return (
    <Table
      columns={columns}
      dataSource={data}
      loading={loading}
      rowKey={rowKey}
      pagination={false}
      size="middle"
      // 固定横向最小宽度（其余列宽合计）：长标题列弹性收缩省略，
      // 桌面端不再横向滚动；窄屏仍可滑动（任务名称悬停 Tooltip 显示全名）
      scroll={{ x: 1100 }}
      style={{ background: COLORS.bgContainer, borderRadius: '8px' }}
    />
  );
}
