import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import { CheckCircleOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons';
import {
  applyResourceUpdateByKeys,
  applyResourceUpdateByType,
  checkResources,
  fetchResourceStatus,
  importResourceZip,
} from '@/api/resource';
import { formatDateTime } from '@/utils/format';
import type { ResourceItemInfo, ResourceStatus } from '@/types/resource';
import { COLORS } from '@/theme/tokens';

const { Title, Text } = Typography;

/** 状态 Tag 展示映射（对齐后端 ResourceSyncService 状态常量） */
const STATUS_META: Record<string, { color: string; label: string }> = {
  'up-to-date': { color: 'green', label: '已是最新' },
  'update-available': { color: 'orange', label: '有可用更新' },
  offline: { color: 'red', label: '云端不可达' },
  disabled: { color: 'default', label: '未启用' },
  syncing: { color: 'blue', label: '同步中' },
  failed: { color: 'red', label: '操作失败' },
  imported: { color: 'green', label: '导入成功' },
};

/** 字节数格式化展示 */
function formatSize(size: number | null): string {
  if (size === null) {
    return '—';
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * 资源管理页面（系统设置 → 资源管理，ADR-022 / ADR-025）。
 *
 * - 更新按 agent 类型分组展示与一键应用（脚本随整体包自动更新）
 * - 更新源地址由服务端统一下发（配置在安装包内，用户零配置零展示），
 *   检查更新时自动获取最新地址
 */
export function ResourceManage() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState<ResourceStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const applyStatus = useCallback((data: ResourceStatus) => {
    setStatus(data);
  }, []);

  const loadStatus = useCallback(
    async () => {
      try {
        const data = await fetchResourceStatus();
        setLoadError(null);
        applyStatus(data);
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : '加载资源状态失败');
      }
    },
    [applyStatus]
  );

  useEffect(() => {
    async function load() {
      try {
        const data = await fetchResourceStatus();
        setLoadError(null);
        applyStatus(data);
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : '加载资源状态失败');
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [applyStatus]);

  const handleCheck = async () => {
    setChecking(true);
    try {
      const data = await checkResources();
      applyStatus(data);
      message.success(data.message ?? '检查完成');
    } catch (err) {
      message.error(err instanceof Error ? err.message : '检查失败');
    } finally {
      setChecking(false);
    }
  };

  const handleApplyType = async (type: string) => {
    setApplying(type);
    try {
      const result = await applyResourceUpdateByType(type);
      message.success(result.message ?? `已更新 ${result.updated_keys.length} 项资源`);
      await loadStatus();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '应用更新失败');
    } finally {
      setApplying(null);
    }
  };

  const handleApplyUngrouped = async (keys: string[]) => {
    setApplying('_ungrouped');
    try {
      const result = await applyResourceUpdateByKeys(keys);
      message.success(result.message ?? `已更新 ${result.updated_keys.length} 项资源`);
      await loadStatus();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '应用更新失败');
    } finally {
      setApplying(null);
    }
  };

  const handleImport = async () => {
    if (!zipFile) {
      return;
    }
    setImporting(true);
    try {
      const result = await importResourceZip(zipFile);
      message.success(result.message ?? '资源包导入成功');
      setZipFile(null);
      await loadStatus();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '导入失败');
    } finally {
      setImporting(false);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '24px', textAlign: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }

  const statusMeta = (status && STATUS_META[status.status]) || {
    color: 'default',
    label: status?.status ?? '未知',
  };
  const columns = [
    { title: '资源类型', dataIndex: 'key', key: 'key' },
    {
      title: '版本（本地 → 云端）',
      key: 'version',
      render: (_: unknown, record: ResourceItemInfo) =>
        `${record.local_version || (record.local_sha256?.slice(0, 8) ?? '未落盘')} → ${record.cloud_version || (record.cloud_sha256?.slice(0, 8) ?? '—')}`,
    },
    { title: '大小', key: 'size', render: (_: unknown, record: ResourceItemInfo) => formatSize(record.size) },
    {
      title: '状态',
      key: 'state',
      render: (_: unknown, record: ResourceItemInfo) =>
        record.outdated ? (
          <Tag color="orange">待更新</Tag>
        ) : record.present ? (
          <Tag color="green">最新</Tag>
        ) : (
          <Tag color="red">未落盘</Tag>
        ),
    },
  ];

  // 按类型分组的条目（ADR-025：脚本随整体包按类型更新）
  const typedGroups = (status?.agent_types ?? []).filter((g) => g.type !== '_ungrouped');
  const ungroupedKeys = (status?.agent_types ?? []).find((g) => g.type === '_ungrouped')?.resources ?? [];
  const ungroupedItems = (status?.resources ?? []).filter((item) => ungroupedKeys.includes(item.key));

  return (
    <div style={{ padding: '24px', minHeight: '100vh', background: COLORS.bgBase }}>
      <div style={{ marginBottom: '24px' }}>
        <Title level={3} style={{ color: COLORS.textPrimary, margin: 0 }}>
          资源管理
        </Title>
        <Text style={{ color: COLORS.textSecondary }}>
          管理 Agent 安装资源的云端仓库同步，资源经 /static 提供给 Agent 节点
        </Text>
      </div>

      {loadError && (
        <Alert
          message={loadError}
          type="error"
          showIcon
          style={{ marginBottom: '16px', background: 'var(--error-soft)', border: 'none' }}
        />
      )}

      <Card style={{ background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, borderRadius: '8px' }}>
        <Space style={{ marginBottom: 16 }}>
          <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
          {status?.message && <Text type="secondary">{status.message}</Text>}
        </Space>
        <Descriptions
          column={2}
          size="small"
          items={[
            { key: 'local', label: '本地版本', children: status?.local_revision ?? '—' },
            { key: 'cloud', label: '云端版本', children: status?.cloud_revision ?? '—' },
            { key: 'checked', label: '上次检查', children: formatDateTime(status?.last_checked_at ?? undefined) },
            { key: 'synced', label: '上次同步', children: formatDateTime(status?.last_synced_at ?? undefined) },
          ]}
        />
        <Button
          icon={<ReloadOutlined />}
          loading={checking}
          onClick={handleCheck}
          style={{ marginTop: 12 }}
        >
          立即检查
        </Button>
      </Card>

      {typedGroups.map((group) => {
        const items = (status?.resources ?? []).filter((item) => group.resources.includes(item.key));
        return (
          <Card
            key={group.type}
            title={
              <Space>
                <span>{`Agent 类型：${group.type}`}</span>
                {group.has_update ? (
                  <Tag color="orange">有可用更新</Tag>
                ) : (
                  <Tag color="green">已是最新</Tag>
                )}
              </Space>
            }
            extra={
              group.has_update ? (
                <Button
                  type="primary"
                  size="small"
                  icon={<CheckCircleOutlined />}
                  loading={applying === group.type}
                  onClick={() => handleApplyType(group.type)}
                >
                  更新 {group.type}
                </Button>
              ) : undefined
            }
            style={{ marginTop: 24, background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, borderRadius: '8px' }}
          >
            <Table
              size="small"
              rowKey="key"
              columns={columns}
              dataSource={items}
              pagination={false}
            />
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">
                更新时该类型的安装脚本随整体包自动一起更新，无需单独选择
              </Text>
            </div>
          </Card>
        );
      })}

      {ungroupedItems.length > 0 && (
        <Card
          title={
            <Space>
              <span>未分组资源</span>
              <Tag>历史条目</Tag>
            </Space>
          }
          style={{ marginTop: 24, background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, borderRadius: '8px' }}
        >
          <Table
            size="small"
            rowKey="key"
            columns={columns}
            dataSource={ungroupedItems}
            pagination={false}
          />
          <Button
            size="small"
            style={{ marginTop: 8 }}
            loading={applying === '_ungrouped'}
            disabled={!ungroupedItems.some((item) => item.outdated)}
            onClick={() =>
              handleApplyUngrouped(ungroupedItems.filter((item) => item.outdated).map((item) => item.key))
            }
          >
            更新全部过时条目
          </Button>
        </Card>
      )}

      <Card
        title="离线导入"
        style={{ marginTop: 24, background: COLORS.bgContainer, border: '1px solid ' + COLORS.border, borderRadius: '8px' }}
      >
        <Space>
          <Upload
            accept=".zip,.tar.gz,.tgz"
            maxCount={1}
            showUploadList={false}
            beforeUpload={(file) => {
              setZipFile(file);
              return false;
            }}
          >
            <Button icon={<UploadOutlined />}>选择资源包 / 节点整体包</Button>
          </Upload>
          {zipFile && <Text>{zipFile.name}（{formatSize(zipFile.size)}）</Text>}
          <Button type="primary" loading={importing} disabled={!zipFile} onClick={handleImport}>
            开始导入
          </Button>
        </Space>
        <div style={{ marginTop: 8 }}>
          <Text type="secondary">
            离线环境兜底：上传与云端仓库同构的 zip 资源包，或构建产物节点整体包
            （opc-agent-&lt;类型&gt;-&lt;版本&gt;-&lt;平台&gt;.tar.gz），校验通过后直接应用
          </Text>
        </div>
      </Card>
    </div>
  );
}
