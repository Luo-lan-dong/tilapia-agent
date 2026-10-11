/* 职责说明（coding-standards 4.3）：统计卡拆至 dashboard/MetricsCards，
   左栏三 Tab 内容拆至 dashboard/DashboardTaskTable、AlertList、DeliverableList。 */
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Dropdown,
  Input,
  Pagination,
  Row,
  Space,
  Tabs,
  Typography,
} from 'antd';
import {
  CheckOutlined,
  DownOutlined,
  FilterOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { NodeCluster } from '@/components/NodeCluster';
import { TaskDetailDrawer } from '@/components/TaskDetailDrawer';
import { AlertList } from '@/components/dashboard/AlertList';
import { DashboardTaskTable } from '@/components/dashboard/DashboardTaskTable';
import { DeliverableList } from '@/components/dashboard/DeliverableList';
import { MetricsCards } from '@/components/dashboard/MetricsCards';
import { useAgentNodeList } from '@/hooks/useAgentNodeList';
import { useAlertList } from '@/hooks/useAlertList';
import { useClusterMetrics } from '@/hooks/useClusterMetrics';
import { useDeliverableList } from '@/hooks/useDeliverableList';
import { useTaskList } from '@/hooks/useTaskList';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';
import type { TaskStatus } from '@/types/task';

const { Title, Text } = Typography;

/** 任务列表分页大小 */
const PAGE_SIZE = 8;
/** 搜索防抖间隔（毫秒），设计说明 §7.7 */
const SEARCH_DEBOUNCE_MS = 300;

/** 状态筛选项（ADR-038 含 review） */
const STATUS_FILTER_OPTIONS: { key: TaskStatus | ''; label: string }[] = [
  { key: '', label: '全部状态' },
  { key: 'pending', label: '待分配' },
  { key: 'assigned', label: '已分配' },
  { key: 'running', label: '执行中' },
  { key: 'review', label: '待确认' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' },
];

/**
 * 首页仪表盘页面（ADR-038 设计稿 v2）。
 *
 * 8 统计卡 + 左栏三 Tab（任务队列/异常告警/交付物聚合）+ 右栏节点集群；
 * 搜索 300ms 防抖走后端 keyword 查询；通过 useClusterMetrics / useAlertList /
 * useDeliverableList / useTaskList / useAgentNodeList 轮询数据。
 */
export function Dashboard() {
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState('tasks');
  const [searchInput, setSearchInput] = useState('');
  const [keyword, setKeyword] = useState('');
  const [statusFilter, setStatusFilter] = useState<TaskStatus | ''>('');
  const [page, setPage] = useState(1);
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  // 搜索防抖：输入停止 300ms 后才更新生效关键字
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setKeyword(searchInput.trim());
      setPage(1);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  const metrics = useClusterMetrics();
  const alertList = useAlertList();
  const deliverables = useDeliverableList();
  const taskList = useTaskList({
    keyword: keyword || undefined,
    status: statusFilter || undefined,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  });
  const nodeList = useAgentNodeList({ limit: 50 });

  const onlineNodes = useMemo(
    () => nodeList.data.filter((node) => node.status !== 'offline').length,
    [nodeList.data],
  );

  const refreshAll = () => {
    metrics.refresh();
    alertList.refresh();
    deliverables.refresh();
    taskList.refresh();
    nodeList.refresh();
  };

  const openTaskDetail = (taskId: string) => {
    setDetailTaskId(taskId);
    setDetailOpen(true);
  };

  /** 告警 Tab 标签（带红色角标） */
  const alertTabLabel = (
    <span>
      异常告警{' '}
      {alertList.total > 0 && <Badge count={alertList.total} size="small" color={COLORS.danger} />}
    </span>
  );

  return (
    <div style={{ padding: '24px', minHeight: '100vh', background: COLORS.bgBase }}>
      <Row justify="space-between" align="middle" style={{ marginBottom: 24 }} gutter={[0, 12]}>
        <Col xs={24} md="auto">
          <Title level={3} style={{ color: COLORS.textPrimary, margin: 0 }}>
            中央调度中枢
          </Title>
          <Text style={{ color: COLORS.textSecondary }}>
            tilapia AI 员工集群实时指挥中心 · 自动刷新
          </Text>
        </Col>
        <Col xs={24} md="auto">
          <Space>
            <Button icon={<ReloadOutlined />} ghost type="primary" onClick={refreshAll}>
              刷新
            </Button>
            <Button icon={<SettingOutlined />} ghost type="primary" onClick={() => navigate('/settings')}>
              设置
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => navigate('/tasks/new')}
              style={{ ...primaryButtonStyle }}
            >
              新建任务
            </Button>
          </Space>
        </Col>
      </Row>

      {(metrics.error || alertList.error || deliverables.error || taskList.error || nodeList.error) && (
        <Alert
          message={
            metrics.error?.message ||
            alertList.error?.message ||
            deliverables.error?.message ||
            taskList.error?.message ||
            nodeList.error?.message
          }
          type="error"
          showIcon
          style={{ marginBottom: 16, background: COLORS.alertDangerBg, border: 'none' }}
        />
      )}

      <MetricsCards
        metrics={metrics.data}
        fallbackNodeTotal={nodeList.total}
        fallbackOnlineNodes={onlineNodes}
      />

      <Row gutter={[16, 16]} align="stretch">
        <Col xs={24} lg={14}>
          <Card
            style={{
              background: COLORS.bgContainer,
              border: '1px solid ' + COLORS.border,
              borderRadius: '8px',
            }}
            styles={{
              body: { paddingTop: 8 },
            }}
          >
            <Tabs
              activeKey={activeTab}
              onChange={setActiveTab}
              tabBarExtraContent={
                activeTab === 'tasks' ? (
                  <Space>
                    <Input
                      allowClear
                      value={searchInput}
                      onChange={(e) => setSearchInput(e.target.value)}
                      placeholder="搜索任务名称"
                      prefix={<SearchOutlined style={{ color: COLORS.textMuted }} />}
                      style={{ width: 200 }}
                    />
                    <Dropdown
                      menu={{
                        items: STATUS_FILTER_OPTIONS.map((opt) => ({
                          key: opt.key,
                          label: opt.label,
                        })),
                        onClick: ({ key }) => {
                          setStatusFilter(key as TaskStatus | '');
                          setPage(1);
                        },
                        selectable: true,
                        selectedKeys: [statusFilter],
                      }}
                    >
                      <Button icon={<FilterOutlined />} ghost type="primary">
                        {STATUS_FILTER_OPTIONS.find((opt) => opt.key === statusFilter)?.label ??
                          '全部状态'}{' '}
                        <DownOutlined />
                      </Button>
                    </Dropdown>
                  </Space>
                ) : activeTab === 'alerts' && alertList.total > 0 ? (
                  <Button
                    icon={<CheckOutlined />}
                    ghost
                    type="primary"
                    loading={alertList.markingRead}
                    onClick={() => void alertList.markAllRead()}
                  >
                    一键已读
                  </Button>
                ) : undefined
              }
              items={[
                {
                  key: 'tasks',
                  label: '任务队列',
                  children: (
                    <>
                      <DashboardTaskTable data={taskList.data} loading={taskList.loading} />
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'flex-end',
                          marginTop: 12,
                        }}
                      >
                        <Pagination
                          size="small"
                          current={page}
                          pageSize={PAGE_SIZE}
                          total={taskList.total}
                          onChange={(next) => setPage(next)}
                          showSizeChanger={false}
                          showTotal={(total) => `共 ${total} 条`}
                        />
                      </div>
                    </>
                  ),
                },
                {
                  key: 'alerts',
                  label: alertTabLabel,
                  children: (
                    <AlertList
                      items={alertList.items}
                      loading={alertList.loading}
                      onSelectTask={openTaskDetail}
                    />
                  ),
                },
                {
                  key: 'deliverables',
                  label: '交付物聚合',
                  children: (
                    <DeliverableList
                      items={deliverables.items}
                      loading={deliverables.loading}
                      onViewTask={openTaskDetail}
                    />
                  ),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card
            title="节点集群"
            style={{
              background: COLORS.bgContainer,
              border: '1px solid ' + COLORS.border,
              borderRadius: '8px',
              height: '100%',
            }}
            styles={{
              header: { color: COLORS.textPrimary, borderBottom: '1px solid ' + COLORS.border },
            }}
          >
            <NodeCluster data={nodeList.data} loading={nodeList.loading} />
          </Card>
        </Col>
      </Row>

      <TaskDetailDrawer
        open={detailOpen}
        taskId={detailTaskId}
        onClose={() => setDetailOpen(false)}
        onChanged={() => {
          taskList.refresh();
          alertList.refresh();
        }}
      />
    </div>
  );
}
