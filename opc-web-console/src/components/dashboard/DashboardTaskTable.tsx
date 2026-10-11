import { useMemo } from 'react';
import { Progress, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Key } from 'react';
import type { Task, TaskPriority, TaskStatus } from '@/types/task';
import { COLORS } from '@/theme/tokens';

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
  running: 'green',
  review: 'warning',
  completed: 'green',
  failed: 'error',
  requeue_pending: 'warning',
  verify_blocked: 'warning',
};

/**
 * 优先级到 Tag 颜色的映射（P0 红 / P1 橙 / P2 灰，ADR-038）
 */
const PRIORITY_COLOR_MAP: Record<TaskPriority, string> = {
  P0: 'error',
  P1: 'warning',
  P2: 'default',
};

/**
 * DashboardTaskTable 属性定义
 */
interface DashboardTaskTableProps {
  /** 任务列表数据 */
  data: Task[];
  /** 是否加载中 */
  loading: boolean;
}

/**
 * 仪表盘精简任务表格（ADR-038 设计稿 v2）。
 *
 * 仅展示任务 ID / 名称 / 优先级 / 状态 / 分配节点 / 进度六列，
 * 不含操作与派发列（共享 TaskTable 供任务队列页使用，互不影响）。
 */
export function DashboardTaskTable({ data, loading }: DashboardTaskTableProps) {
  const columns = useMemo<ColumnsType<Task>>(
    () => [
      {
        title: '任务 ID',
        dataIndex: 'id',
        key: 'id',
        width: 110,
        render: (id: string) => (
          <Text code style={{ color: COLORS.textPrimary, background: COLORS.codeBg }}>
            {id.slice(0, 8)}
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
        title: '优先级',
        dataIndex: 'priority',
        key: 'priority',
        width: 80,
        render: (priority: TaskPriority | undefined) => (
          <Tag color={PRIORITY_COLOR_MAP[priority ?? 'P2']}>{priority ?? 'P2'}</Tag>
        ),
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 90,
        render: (status: TaskStatus) => (
          <Tag color={STATUS_COLOR_MAP[status]}>{STATUS_LABEL_MAP[status]}</Tag>
        ),
      },
      {
        title: '分配节点',
        dataIndex: 'assigned_node_name',
        key: 'assigned_node_name',
        width: 130,
        render: (nodeName: string | undefined) => (
          <Text style={{ color: nodeName ? COLORS.textPrimary : COLORS.textMuted }}>
            {nodeName || '—'}
          </Text>
        ),
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
            strokeColor={
              record.status === 'failed'
                ? COLORS.danger
                : record.status === 'assigned'
                  ? COLORS.info
                  : COLORS.primary
            }
            trailColor={COLORS.borderFaint}
            showInfo={false}
          />
        ),
      },
    ],
    [],
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
      locale={{ emptyText: '暂无任务，点击右上角「新建任务」创建' }}
      style={{ background: COLORS.bgContainer, borderRadius: '8px' }}
    />
  );
}
