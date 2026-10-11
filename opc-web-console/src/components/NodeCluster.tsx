import { Card, Col, Progress, Row, Typography } from 'antd';
import {
  BgColorsOutlined,
  CodeOutlined,
  DollarOutlined,
  FileTextOutlined,
  SoundOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import type { AgentNode, NodeStatus } from '@/types/agentNode';
import { STATUS_COLOR_MAP, STATUS_LABEL_MAP } from '@/utils/nodeStatus';

/**
 * NodeCluster 属性定义
 */
interface NodeClusterProps {
  /** 节点列表数据 */
  data: AgentNode[];
  /** 是否加载中 */
  loading: boolean;
}

const { Text } = Typography;

/**
 * Agent 类型到图标的映射（ADR-038 设计稿 v2；未知类型回退代码图标）
 */
const AGENT_TYPE_ICON_MAP: Record<string, typeof CodeOutlined> = {
  code: CodeOutlined,
  office: FileTextOutlined,
  design: BgColorsOutlined,
  video: VideoCameraOutlined,
  doc: FileTextOutlined,
  ops: SoundOutlined,
  biz: DollarOutlined,
};

/**
 * 负载分色：<60% 主色、<85% 警告橙、≥85% 危险红（离线灰）。
 * 颜色为主题 CSS 变量（ADR-052），双主题自动适配。
 */
function loadBarColor(load: number, status: NodeStatus): string {
  if (status === 'offline') {
    return 'var(--text-muted)';
  }
  if (load >= 85) {
    return 'var(--error)';
  }
  if (load >= 60) {
    return 'var(--status-warn)';
  }
  return 'var(--primary)';
}

/**
 * 节点集群面板组件。
 *
 * 卡片网格布局：类型图标 + 状态点 + 名称 + 类型/状态描述 + 负载彩色进度条
 * （ADR-038 设计稿 v2 节点卡片升级；颜色均为主题 CSS 变量，ADR-052）。
 */
export function NodeCluster({ data, loading }: NodeClusterProps) {
  return (
    <Row gutter={[12, 12]}>
      {data.map((node) => {
        const TypeIcon = AGENT_TYPE_ICON_MAP[node.agent_type] ?? CodeOutlined;
        const barColor = loadBarColor(node.load, node.status);
        return (
          <Col key={node.id} span={12}>
            <Card
              loading={loading}
              styles={{
                body: { padding: '12px', background: 'var(--surface)' },
              }}
              style={{
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: '8px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                <span
                  style={{
                    width: '26px',
                    height: '26px',
                    borderRadius: '6px',
                    background: 'var(--code-bg)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  <TypeIcon style={{ color: 'var(--primary)', fontSize: 14 }} />
                </span>
                <span
                  style={{
                    width: '8px',
                    height: '8px',
                    borderRadius: '50%',
                    background: STATUS_COLOR_MAP[node.status],
                    flexShrink: 0,
                  }}
                />
                <Text
                  strong
                  style={{
                    color: 'var(--text-primary)',
                    fontSize: '13px',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {node.name}
                </Text>
              </div>
              <Text
                style={{
                  display: 'block',
                  color: 'var(--text-secondary)',
                  fontSize: '11px',
                  marginBottom: '8px',
                }}
              >
                {node.agent_type} · {STATUS_LABEL_MAP[node.status]}
                {node.reported_ip ? (
                  <Text code style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>
                    {' '}
                    · {node.reported_ip}
                  </Text>
                ) : null}
              </Text>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Text
                  style={{
                    color: 'var(--text-secondary)',
                    fontSize: '11px',
                    width: '38px',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {node.status === 'offline' ? '—' : `${node.load}%`}
                </Text>
                <Progress
                  percent={node.status === 'offline' ? 0 : node.load}
                  size="small"
                  strokeColor={barColor}
                  trailColor="var(--border-faint)"
                  showInfo={false}
                  style={{ flex: 1 }}
                />
              </div>
            </Card>
          </Col>
        );
      })}
    </Row>
  );
}
