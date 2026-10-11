import { useNavigate } from 'react-router-dom';
import { Button, Descriptions, Input, Space, Tag, Typography } from 'antd';
import {
  CheckOutlined,
  CloseOutlined,
  CopyOutlined,
  EditOutlined,
  LineChartOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { NodeStatusTag } from '@/components/NodeStatusTag';
import { formatDateTime } from '@/utils/format';
import type { AgentNode } from '@/types/agentNode';
import { COLORS } from '@/theme/tokens';

const { Text } = Typography;

interface NodeDetailInfoProps {
  nodeId: string | null;
  node: AgentNode;
  /** 名称编辑态与草稿 */
  editingName: boolean;
  nameDraft: string;
  savingName: boolean;
  onNameDraftChange: (value: string) => void;
  onStartEditName: () => void;
  onCancelEditName: () => void;
  onSaveName: () => void;
  /** Token 操作 */
  resetting: boolean;
  onCopyToken: () => void;
  onResetToken: () => void;
}

/**
 * 节点详情信息区（纯展示）。
 *
 * 承载节点信息 Descriptions、名称编辑、Token 复制/重置入口，
 * 从 NodeDetailDrawer 拆出以控制主文件行数（coding-standards 4.3）。
 */
export function NodeDetailInfo({
  nodeId,
  node,
  editingName,
  nameDraft,
  savingName,
  onNameDraftChange,
  onStartEditName,
  onCancelEditName,
  onSaveName,
  resetting,
  onCopyToken,
  onResetToken,
}: NodeDetailInfoProps) {
  const navigate = useNavigate();

  return (
    <Descriptions
      layout="vertical"
      column={1}
      styles={{
        label: { color: COLORS.textSecondary, fontWeight: 500 },
        content: { color: COLORS.textPrimary },
      }}
    >
      <Descriptions.Item label="节点 ID">
        <Text copyable={{ text: node.id }} style={{ color: COLORS.textPrimary }}>
          {node.id}
        </Text>
      </Descriptions.Item>
      <Descriptions.Item label="节点名称">
        {editingName ? (
          <Space>
            <Input
              value={nameDraft}
              onChange={(event) => onNameDraftChange(event.target.value)}
              maxLength={128}
              disabled={savingName}
              style={{ width: 220, background: COLORS.bgBase, color: COLORS.textPrimary }}
              onPressEnter={onSaveName}
            />
            <Button
              icon={<CheckOutlined />}
              loading={savingName}
              onClick={onSaveName}
              style={{ color: COLORS.primary }}
            >
              保存
            </Button>
            <Button
              icon={<CloseOutlined />}
              onClick={onCancelEditName}
              disabled={savingName}
              style={{ color: COLORS.textSecondary }}
            >
              取消
            </Button>
          </Space>
        ) : (
          <Space>
            <Text style={{ color: COLORS.textPrimary }}>{node.name}</Text>
            <Button
              type="text"
              size="small"
              icon={<EditOutlined />}
              onClick={onStartEditName}
              style={{ color: COLORS.primary, padding: 0 }}
            >
              编辑
            </Button>
          </Space>
        )}
      </Descriptions.Item>
      <Descriptions.Item label="Agent 类型">
        <Text style={{ color: COLORS.textPrimary }}>{node.agent_type}</Text>
      </Descriptions.Item>
      <Descriptions.Item label="状态">
        <NodeStatusTag status={node.status} />
      </Descriptions.Item>
      <Descriptions.Item label="能力标签">
        {node.capabilities && node.capabilities.length > 0 ? (
          <Space size="small" wrap>
            {node.capabilities.map((capability) => (
              <Tag
                key={capability}
                style={{
                  color: COLORS.primary,
                  background: COLORS.primaryBg,
                  borderColor: COLORS.primaryBorder,
                }}
              >
                {capability}
              </Tag>
            ))}
          </Space>
        ) : (
          '—'
        )}
      </Descriptions.Item>
      <Descriptions.Item label="负载">
        {node.status === 'offline' ? '—' : `${node.load}%`}
      </Descriptions.Item>
      <Descriptions.Item label="最后心跳">
        {formatDateTime(node.last_heartbeat_at)}
      </Descriptions.Item>
      <Descriptions.Item label="创建时间">{formatDateTime(node.created_at)}</Descriptions.Item>
      <Descriptions.Item label="监控信息">
        <Button
          icon={<LineChartOutlined />}
          onClick={() => {
            if (nodeId) {
              navigate(`/nodes/${nodeId}/metrics`);
            }
          }}
          disabled={node.status === 'offline'}
          style={{ color: COLORS.primary }}
        >
          查看监控详情
        </Button>
      </Descriptions.Item>
      <Descriptions.Item label="注册 Token">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Text style={{ color: COLORS.textPrimary, wordBreak: 'break-all' }}>
            {node.registration_token || '—'}
          </Text>
          <Space>
            <Button
              icon={<CopyOutlined />}
              onClick={onCopyToken}
              disabled={!node.registration_token}
              style={{ color: COLORS.primary }}
            >
              复制 Token
            </Button>
            <Button icon={<ReloadOutlined />} loading={resetting} onClick={onResetToken} style={{ color: COLORS.primary }}>
              重置 Token
            </Button>
          </Space>
        </Space>
      </Descriptions.Item>
    </Descriptions>
  );
}
