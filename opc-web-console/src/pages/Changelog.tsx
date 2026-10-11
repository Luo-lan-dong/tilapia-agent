import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Modal,
  Space,
  Spin,
  Tabs,
  Tag,
  Timeline,
  Typography,
} from 'antd';
import { LinkOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons';
import { checkHubUpdate, fetchReleases } from '@/api/release';
import { fetchSystemVersions } from '@/api/systemVersion';
import type {
  HubUpdateCheckResult,
  ProductGroup,
  ReleaseItem,
} from '@/types/release';

const { Text } = Typography;

/**
 * 更新日志页面（ADR-026：版本发布管理与中枢更新）。
 *
 * 数据完全云端化：从管理后台 hub_releases 拉取，按产品线分组展示
 * （中枢 / Code Agent / …）；中枢 Tab 提供「检查中枢更新」入口
 * （检查+提示+下载引导，安装由用户手动完成）。
 */
export function Changelog() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [groups, setGroups] = useState<ProductGroup[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [currentVersion, setCurrentVersion] = useState<string>('--');
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<HubUpdateCheckResult | null>(null);

  const loadCurrentVersion = useCallback(async () => {
    // 本地 system_versions 仅存当前运行版本号（ADR-018 修订）
    try {
      const versions = await fetchSystemVersions();
      setCurrentVersion(versions.find((v) => v.is_current)?.version ?? '--');
    } catch {
      setCurrentVersion('--');
    }
  }, []);

  const loadReleases = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchReleases();
      setGroups(data.products);
      setError(data.error);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载更新日志失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // 异步 IIFE 中并发加载（react-hooks/set-state-in-effect 要求 setState
    // 出现在异步回调而非 effect 同步调用链上）
    void (async () => {
      await Promise.all([loadReleases(), loadCurrentVersion()]);
    })();
  }, [loadReleases, loadCurrentVersion]);

  const handleCheckUpdate = useCallback(async () => {
    setChecking(true);
    try {
      const result = await checkHubUpdate();
      if (result.status === 'update-available') {
        setCheckResult(result);
      } else if (result.status === 'up-to-date') {
        message.success(result.message ?? '当前已是最新版本');
      } else {
        message.warning(result.message ?? '检查失败');
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '检查更新失败');
    } finally {
      setChecking(false);
    }
  }, [message]);

  const renderTimeline = (releases: ReleaseItem[]) => {
    if (releases.length === 0) {
      return <Empty description="暂无版本记录" />;
    }
    return (
      <Timeline
        items={releases.map((item) => ({
          color: 'blue',
          children: (
            <div key={`${item.product}-${item.version}`}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Tag color="blue">{item.version}</Tag>
                <Text type="secondary">{item.release_date}</Text>
                {item.download_url && (
                  <a href={item.download_url} target="_blank" rel="noreferrer">
                    <LinkOutlined /> 下载安装包
                  </a>
                )}
              </div>
              <div style={{ marginTop: 4, fontWeight: 600 }}>{item.title}</div>
              <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{item.content}</div>
            </div>
          ),
        }))}
      />
    );
  };

  const buildTabItems = () =>
    groups.map((group) => ({
      key: group.product,
      label: group.label,
      children:
        group.product === 'hub' ? (
          <div>
            <Space style={{ marginBottom: 16 }} wrap>
              <Text type="secondary">当前版本：</Text>
              <Tag color="green">{currentVersion}</Tag>
              <Button
                type="primary"
                ghost
                size="small"
                icon={<SyncOutlined spin={checking} />}
                loading={checking}
                onClick={handleCheckUpdate}
              >
                检查中枢更新
              </Button>
            </Space>
            {renderTimeline(group.releases)}
          </div>
        ) : (
          renderTimeline(group.releases)
        ),
    }));

  return (
    <Card title="更新日志">
      {loading ? (
        <Spin tip="加载中...">
          <div style={{ minHeight: 120 }} />
        </Spin>
      ) : error ? (
        <Alert
          type="error"
          message="版本服务不可达"
          description={error}
          showIcon
          action={
            <Button size="small" icon={<ReloadOutlined />} onClick={loadReleases}>
              重试
            </Button>
          }
        />
      ) : groups.length === 0 ? (
        <Empty description="暂无版本记录" />
      ) : (
        <Tabs items={buildTabItems()} />
      )}

      <Modal
        open={checkResult !== null}
        title={
          checkResult?.latest_version
            ? `发现新版本 ${checkResult.latest_version}`
            : '检查更新'
        }
        onCancel={() => setCheckResult(null)}
        footer={
          checkResult?.download_url ? (
            <Space>
              <Button onClick={() => setCheckResult(null)}>稍后更新</Button>
              <Button
                type="primary"
                href={checkResult.download_url}
                target="_blank"
                icon={<LinkOutlined />}
              >
                下载安装包
              </Button>
            </Space>
          ) : null
        }
      >
        {checkResult && (
          <div>
            <Space wrap style={{ marginBottom: 8 }}>
              <Text type="secondary">当前版本：</Text>
              <Tag>{checkResult.current_version ?? '--'}</Tag>
              <Text type="secondary">最新版本：</Text>
              <Tag color="blue">{checkResult.latest_version ?? '--'}</Tag>
            </Space>
            {checkResult.title && <div style={{ fontWeight: 600 }}>{checkResult.title}</div>}
            <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{checkResult.content}</div>
            <Alert
              style={{ marginTop: 12 }}
              type="info"
              showIcon
              message="下载安装包后请手动运行安装（覆盖安装会保留数据目录）"
            />
          </div>
        )}
      </Modal>
    </Card>
  );
}
