/* 职责说明（coding-standards 4.3）：轮询数据加载逻辑已拆至 useNodeMetricsPolling，
   本文件保留 7 张指标卡片的展示布局（卡片结构同构、数据同源）。 */
import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Progress,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons';
import { NodeStatusTag } from '@/components/NodeStatusTag';
import { useNodeMetricsPolling } from '@/hooks/useNodeMetricsPolling';
import { formatDateTime } from '@/utils/format';
import { COLORS } from '@/theme/tokens';

const { Text, Title } = Typography;

/**
 * 将负载等级映射为展示颜色。
 */
function getLoadLevelColor(level?: string): string {
  switch (level) {
    case 'idle':
      return COLORS.primary;
    case 'normal':
      return COLORS.info;
    case 'high':
      return COLORS.warning;
    case 'critical':
      return COLORS.danger;
    default:
      return COLORS.textSecondary;
  }
}

/**
 * 格式化百分比数值，保留一位小数。
 */
function formatPercent(value?: number): string {
  if (value === undefined || value === null || Number.isNaN(value)) {
    return '—';
  }
  return `${value.toFixed(1)}%`;
}

/**
 * 节点监控详情二级页面。
 *
 * 通过路由 /nodes/:id/metrics 进入，展示指定节点的实时系统资源监控快照，
 * 并以固定间隔自动轮询接口保持数据刷新（轮询逻辑见 useNodeMetricsPolling）。
 */
export function NodeMetrics() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { id: nodeId } = useParams<{ id: string }>();

  const { node, metricsData, nodeLoading, metricsLoading, lastUpdatedAt, refreshMetrics } =
    useNodeMetricsPolling(nodeId, message);

  const metrics = metricsData?.metrics;
  const loadScore = metrics?.load_score;

  const cpuColor = useMemo(() => {
    const percent = metrics?.cpu?.percent ?? 0;
    if (percent >= 90) {
      return COLORS.danger;
    }
    if (percent >= 70) {
      return COLORS.warning;
    }
    return COLORS.primary;
  }, [metrics?.cpu?.percent]);

  const pageLoading = nodeLoading && metricsLoading && !metricsData;

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
        <Space align="center" size="middle">
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/nodes')}
            style={{ color: COLORS.textPrimary, background: COLORS.bgContainer, borderColor: COLORS.borderStrong }}
          >
            返回节点列表
          </Button>
          <Title level={3} style={{ color: COLORS.textPrimary, margin: 0 }}>
            {node?.name ?? '节点监控详情'}
          </Title>
          {node && <NodeStatusTag status={node.status} />}
        </Space>
        <Space align="center" size="middle">
          <Text style={{ color: COLORS.textSecondary }}>
            最后刷新：{metricsLoading ? '刷新中...' : lastUpdatedAt || '—'}
          </Text>
          <Button
            icon={<ReloadOutlined />}
            loading={metricsLoading}
            onClick={refreshMetrics}
            style={{ color: COLORS.primary, background: COLORS.bgContainer, borderColor: COLORS.borderStrong }}
          >
            立即刷新
          </Button>
        </Space>
      </div>

      {pageLoading ? (
        <Spin tip="加载监控数据中...">
          <div style={{ minHeight: 48, margin: '48px 0' }} />
        </Spin>
      ) : (
        <Row gutter={[16, 16]} align="stretch">
          <Col xs={24} lg={8}>
            <Card title="综合负载" style={{ background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, height: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>负载得分</Text>
                  <Text style={{ color: COLORS.textPrimary, fontSize: 18, fontWeight: 600 }}>
                    {loadScore?.score ?? '—'}
                  </Text>
                </div>
                <Progress
                  percent={Math.round(loadScore?.score ?? 0)}
                  showInfo={false}
                  strokeColor={getLoadLevelColor(loadScore?.level)}
                  trailColor={COLORS.border}
                />
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>等级</Text>
                  <Tag color={getLoadLevelColor(loadScore?.level)} style={{ textTransform: 'capitalize' }}>
                    {loadScore?.level ?? '—'}
                  </Tag>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>最后心跳</Text>
                  <Text style={{ color: COLORS.textPrimary }}>
                    {formatDateTime(metricsData?.last_heartbeat_at)}
                  </Text>
                </div>
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={16}>
            <Card title="系统信息" style={{ background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, height: '100%' }}>
              <Descriptions
                column={{ xs: 1, sm: 2, md: 3 }}
                layout="horizontal"
                styles={{
                  label: { color: COLORS.textSecondary },
                  content: { color: COLORS.textPrimary },
                }}
              >
                <Descriptions.Item label="节点 ID">{nodeId}</Descriptions.Item>
                <Descriptions.Item label="Client ID">{node?.client_id ?? '—'}</Descriptions.Item>
                <Descriptions.Item label="Agent 类型">{node?.agent_type ?? '—'}</Descriptions.Item>
                <Descriptions.Item label="平台">{metrics?.platform ?? '—'}</Descriptions.Item>
                <Descriptions.Item label="在线状态">{metricsData?.is_online ? '在线' : '离线'}</Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>

          <Col xs={24} lg={12}>
            <Card title="CPU" style={{ background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, height: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }} size="large">
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Text style={{ color: COLORS.textSecondary }}>总体使用率</Text>
                    <Text style={{ color: COLORS.textPrimary }}>{formatPercent(metrics?.cpu?.percent)}</Text>
                  </div>
                  <Progress
                    percent={Math.round(metrics?.cpu?.percent ?? 0)}
                    showInfo={false}
                    strokeColor={cpuColor}
                    trailColor={COLORS.border}
                  />
                </div>

                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Text style={{ color: COLORS.textSecondary }}>
                      核心数：物理 {metrics?.cpu?.count_physical ?? '—'} / 逻辑 {metrics?.cpu?.count_logical ?? '—'}
                    </Text>
                  </div>
                  <Row gutter={[8, 8]}>
                    {metrics?.cpu?.per_core?.map((percent, index) => (
                      <Col span={12} key={index}>
                        <div
                          style={{
                            background: COLORS.surface,
                            borderRadius: 6,
                            padding: '8px 12px',
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                            <Text style={{ color: COLORS.textSecondary, fontSize: 12 }}>核心 {index}</Text>
                            <Text style={{ color: COLORS.textPrimary, fontSize: 12 }}>{percent.toFixed(1)}%</Text>
                          </div>
                          <Progress
                            percent={Math.round(percent)}
                            showInfo={false}
                            strokeColor={percent >= 90 ? COLORS.danger : percent >= 70 ? COLORS.warning : COLORS.primary}
                            trailColor={COLORS.border}
                            size="small"
                          />
                        </div>
                      </Col>
                    ))}
                  </Row>
                </div>
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={12}>
            <Card title="内存" style={{ background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, height: '100%' }}><Space direction="vertical" style={{ width: '100%' }} size="large">
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Text style={{ color: COLORS.textSecondary }}>使用率</Text>
                    <Text style={{ color: COLORS.textPrimary }}>{formatPercent(metrics?.memory?.percent)}</Text>
                  </div>
                  <Progress
                    percent={Math.round(metrics?.memory?.percent ?? 0)}
                    showInfo={false}
                    strokeColor="var(--primary)"
                    trailColor={COLORS.border}
                  />
                </div>
                <Descriptions
                  column={2}
                  layout="horizontal"
                  styles={{
                    label: { color: COLORS.textSecondary },
                    content: { color: COLORS.textPrimary },
                  }}
                >
                  <Descriptions.Item label="总容量">{metrics?.memory?.total_gb ?? '—'} GB</Descriptions.Item>
                  <Descriptions.Item label="已用">{metrics?.memory?.used_gb ?? '—'} GB</Descriptions.Item>
                  <Descriptions.Item label="可用">{metrics?.memory?.available_gb ?? '—'} GB</Descriptions.Item>
                  <Descriptions.Item label="Swap 使用率">{formatPercent(metrics?.memory?.swap_percent)}</Descriptions.Item>
                </Descriptions>
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={8}>
            <Card title="磁盘" style={{ background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, height: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>使用率</Text>
                  <Text style={{ color: COLORS.textPrimary }}>{formatPercent(metrics?.disk?.percent)}</Text>
                </div>
                <Progress
                  percent={Math.round(metrics?.disk?.percent ?? 0)}
                  showInfo={false}
                  strokeColor="var(--primary)"
                  trailColor={COLORS.border}
                />
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>已用 / 总量</Text>
                  <Text style={{ color: COLORS.textPrimary }}>
                    {metrics?.disk?.used_gb ?? '—'} / {metrics?.disk?.total_gb ?? '—'} GB
                  </Text>
                </div>
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={8}>
            <Card title="网络" style={{ background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, height: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>上行速率</Text>
                  <Text style={{ color: COLORS.textPrimary }}>{metrics?.network?.send_speed_mb ?? '—'} MB/s</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>下行速率</Text>
                  <Text style={{ color: COLORS.textPrimary }}>{metrics?.network?.recv_speed_mb ?? '—'} MB/s</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>累计发送</Text>
                  <Text style={{ color: COLORS.textPrimary }}>{metrics?.network?.mb_sent ?? '—'} MB</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>累计接收</Text>
                  <Text style={{ color: COLORS.textPrimary }}>{metrics?.network?.mb_recv ?? '—'} MB</Text>
                </div>
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={8}>
            <Card title="系统平均负载" style={{ background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, height: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>1 分钟</Text>
                  <Text style={{ color: COLORS.textPrimary }}>{metrics?.load_average?.['1m'] ?? '—'}</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>5 分钟</Text>
                  <Text style={{ color: COLORS.textPrimary }}>{metrics?.load_average?.['5m'] ?? '—'}</Text>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <Text style={{ color: COLORS.textSecondary }}>15 分钟</Text>
                  <Text style={{ color: COLORS.textPrimary }}>{metrics?.load_average?.['15m'] ?? '—'}</Text>
                </div>
              </Space>
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );
}
