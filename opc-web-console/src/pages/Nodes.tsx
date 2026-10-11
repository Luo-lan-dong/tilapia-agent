import { useMemo, useState } from 'react';
import { Alert, App, Button, Space, Table, Typography } from 'antd';
import { CopyOutlined, EyeOutlined, PlusOutlined } from '@ant-design/icons';
import { getAgentNodeById } from '@/api/agentNodes';
import { InstallCommandModal } from '@/components/InstallCommandModal';
import { NodeDetailDrawer } from '@/components/NodeDetailDrawer';
import { NodeStatusTag } from '@/components/NodeStatusTag';
import { useAgentNodeList } from '@/hooks/useAgentNodeList';
import { copyToClipboard } from '@/utils/clipboard';
import { formatDateTime } from '@/utils/format';
import type { AgentNode } from '@/types/agentNode';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';

const { Title, Text } = Typography;

/**
 * 节点管理页面。
 *
 * 展示所有已注册 Agent 节点的实时状态、负载与最后心跳时间，
 * 支持查看详情、复制注册 Token 以及获取一键安装命令。
 */
export function Nodes() {
  const { message } = App.useApp();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [installModalOpen, setInstallModalOpen] = useState(false);

  const nodeList = useAgentNodeList({ limit: 100 });

  const tableData = useMemo(() => nodeList.data, [nodeList.data]);

  const handleViewDetail = (node: AgentNode) => {
    setSelectedNodeId(node.id);
    setDrawerOpen(true);
  };

  const handleCopyToken = async (node: AgentNode) => {
    try {
      const detail = await getAgentNodeById(node.id);
      if (detail.registration_token) {
        await copyToClipboard(detail.registration_token, '注册 Token', message);
      } else {
        message.warning('该节点暂无注册 Token');
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '获取 Token 失败';
      message.error(errorMessage);
    }
  };

  const handleCloseDrawer = () => {
    setDrawerOpen(false);
    setSelectedNodeId(null);
  };

  const columns = [
    {
      title: '节点名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string) => <Text style={{ color: COLORS.textPrimary }}>{name}</Text>,
    },
    {
      title: 'Agent 类型',
      dataIndex: 'agent_type',
      key: 'agent_type',
      render: (agentType: string) => <Text style={{ color: COLORS.textSecondary }}>{agentType}</Text>,
    },
    {
      title: 'IP 地址',
      dataIndex: 'reported_ip',
      key: 'reported_ip',
      render: (ip?: string | null) => (
        <Text code style={{ color: ip ? COLORS.textSecondary : COLORS.textMuted }}>
          {ip || '—'}
        </Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: AgentNode['status']) => <NodeStatusTag status={status} />,
    },
    {
      title: '负载',
      dataIndex: 'load',
      key: 'load',
      render: (load: number, record: AgentNode) => (
        <Text style={{ color: record.status === 'offline' ? COLORS.textMuted : COLORS.textPrimary }}>
          {record.status === 'offline' ? '—' : `${load}%`}
        </Text>
      ),
    },
    {
      title: '最后心跳',
      dataIndex: 'last_heartbeat_at',
      key: 'last_heartbeat_at',
      render: (value?: string) => (
        <Text style={{ color: COLORS.textSecondary }}>{formatDateTime(value)}</Text>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: AgentNode) => (
        <Space size="middle">
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => handleViewDetail(record)}
            style={{ padding: 0, color: COLORS.primary }}
          >
            查看详情
          </Button>
          <Button
            type="link"
            icon={<CopyOutlined />}
            onClick={() => handleCopyToken(record)}
            style={{ padding: 0, color: COLORS.primary }}
          >
            复制 Token
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: '24px', minHeight: '100vh', background: COLORS.bgBase }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '24px',
        }}
      >
        <div>
          <Title level={3} style={{ color: COLORS.textPrimary, margin: 0 }}>
            节点管理
          </Title>
          <Text style={{ color: COLORS.textSecondary }}>查看 Agent 节点状态并获取安装命令</Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setInstallModalOpen(true)}
          style={{ ...primaryButtonStyle }}
        >
          安装新节点
        </Button>
      </div>

      {nodeList.error && (
        <Alert
          message={nodeList.error.message}
          type="error"
          showIcon
          style={{ marginBottom: '16px', background: 'var(--error-soft)', border: 'none' }}
        />
      )}

      <Table
        dataSource={tableData}
        columns={columns}
        rowKey="id"
        loading={nodeList.loading}
        pagination={false}
        style={{
          background: COLORS.bgContainer,
          border: '1px solid ' + COLORS.border,
          borderRadius: '8px',
        }}
      />

      <NodeDetailDrawer
        nodeId={selectedNodeId}
        open={drawerOpen}
        onClose={handleCloseDrawer}
      />

      <InstallCommandModal
        open={installModalOpen}
        onClose={() => setInstallModalOpen(false)}
      />
    </div>
  );
}
