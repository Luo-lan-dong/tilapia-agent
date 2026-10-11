import { Col, Row } from 'antd';
import { StatCard } from '@/components/StatCard';
import type { ClusterMetrics } from '@/types/dashboard';

/**
 * token 数人性化展示：1.2M / 3.4K / 890
 */
function formatTokenCount(count: number): string {
  if (count >= 1_000_000) {
    return `${(count / 1_000_000).toFixed(1)}M`;
  }
  if (count >= 1_000) {
    return `${(count / 1_000).toFixed(1)}K`;
  }
  return String(count);
}

/**
 * MetricsCards 属性定义
 */
interface MetricsCardsProps {
  /** 集群统计指标 */
  metrics: ClusterMetrics | null;
  /** 节点列表总数（metrics 未就绪时兜底展示） */
  fallbackNodeTotal: number;
  /** 在线节点数（由节点列表推导的兜底值） */
  fallbackOnlineNodes: number;
}

/**
 * 仪表盘 8 统计卡（两排，ADR-038 设计稿 v2）。

第一排：总任务数 / 进行中 / 已完成 / 在线节点；
第二排：集群负载 / 异常事件 / Token 消耗 / 排队任务。
 */
export function MetricsCards({
  metrics,
  fallbackNodeTotal,
  fallbackOnlineNodes,
}: MetricsCardsProps) {
  const onlineNodes = metrics ? metrics.online_nodes : fallbackOnlineNodes;
  const totalNodes = metrics ? metrics.total_nodes : fallbackNodeTotal;

  return (
    <>
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            label="总任务数"
            value={metrics?.total_tasks ?? 0}
            trend="全部任务"
            trendType="neutral"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            label="进行中"
            value={metrics?.running_tasks ?? 0}
            trend="执行中任务"
            trendType="up"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            label="已完成"
            value={metrics?.completed_tasks ?? 0}
            trend="已完成任务"
            trendType="up"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            label="在线节点"
            value={`${onlineNodes}/${totalNodes}`}
            trend={`可用率 ${totalNodes > 0 ? Math.round((onlineNodes / totalNodes) * 100) : 0}%`}
            trendType="up"
          />
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            label="集群负载"
            value={metrics?.cluster_load_percent != null ? `${metrics.cluster_load_percent}%` : '—'}
            trend="在线节点 CPU 均值"
            trendType="neutral"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            label="异常事件"
            value={metrics?.alert_count ?? 0}
            trend={`${metrics?.alert_count ?? 0} 待处理`}
            trendType={metrics && metrics.alert_count > 0 ? 'down' : 'neutral'}
            danger={Boolean(metrics && metrics.alert_count > 0)}
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            label="Token 消耗"
            value={formatTokenCount(metrics?.token_consumed_today ?? 0)}
            trend="今日累计（中枢）"
            trendType="up"
          />
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <StatCard
            label="排队任务"
            value={metrics?.queued_tasks ?? 0}
            trend="等待分配节点"
            trendType="down"
          />
        </Col>
      </Row>
    </>
  );
}
