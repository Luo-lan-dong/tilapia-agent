/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：宝塔 MCP 服务器
   管理面板的列表、新增/编辑（含 mcp_info 导入）、测试连接与工具清单展示共享
   同一份服务器列表状态，拆分需跨文件提升状态反而增加复杂度，故整体豁免。 */
import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Button,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import {
  addBtMcpServer,
  deleteBtMcpServer,
  fetchBtMcpServers,
  fetchBtMcpTools,
  testBtMcpServer,
  updateBtMcpServer,
} from '@/api/btMcp';
import type { BtMcpServerItem, BtMcpToolInfo } from '@/types/btMcp';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';

const { Title, Text } = Typography;

/** 风险等级 Tag 颜色映射 */
const RISK_COLOR: Record<string, string> = {
  low: 'green',
  medium: 'orange',
  high: 'red',
};

const RISK_LABEL: Record<string, string> = {
  low: '只读',
  medium: '有副作用',
  high: '高危',
};

interface ServerFormValues {
  name: string;
  local_url?: string;
  public_url?: string;
  address_mode: 'local' | 'public';
  api_token?: string;
  ca_cert?: string;
  remark?: string;
  enabled: boolean;
  mcp_info_json?: string;
}

/**
 * 宝塔 MCP 服务器管理面板（ADR-049）。
 *
 * 展示已配置的宝塔 MCP 服务器连接（凭据脱敏），支持粘贴插件生成的
 * mcp_info JSON 一键导入、连接测试（服务端信息 + 工具统计）、工具
 * 清单查看与删除。权限管控在服务器端插件，此处不重复管控。
 */
export function BtMcpPanel() {
  const { message } = App.useApp();
  const [form] = Form.useForm<ServerFormValues>();
  const [items, setItems] = useState<BtMcpServerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [editing, setEditing] = useState<BtMcpServerItem | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [toolsServer, setToolsServer] = useState<BtMcpServerItem | null>(null);
  const [tools, setTools] = useState<BtMcpToolInfo[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);

  const loadServers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchBtMcpServers();
      setItems(data.items);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载服务器列表失败');
    } finally {
      setLoading(false);
    }
    // message 为 antd 稳定引用，忽略依赖告警
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // 仅挂载时加载一次（异步回调里 setState，避免 effect 内同步 setState）
    let cancelled = false;
    fetchBtMcpServers()
      .then((data) => {
        if (!cancelled) {
          setItems(data.items);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          message.error(err instanceof Error ? err.message : '加载服务器列表失败');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openCreate = () => {
    setEditing(null);
    setTestResult(null);
    form.resetFields();
    form.setFieldsValue({ address_mode: 'local', enabled: true });
    setModalOpen(true);
  };

  const openEdit = (server: BtMcpServerItem) => {
    setEditing(server);
    setTestResult(null);
    form.resetFields();
    form.setFieldsValue({
      name: server.name,
      local_url: server.local_url,
      public_url: server.public_url ?? undefined,
      address_mode: server.address_mode,
      remark: server.remark ?? undefined,
      enabled: server.enabled,
    });
    setModalOpen(true);
  };

  const parseMcpInfo = (raw: string): Record<string, unknown> | null => {
    const text = raw.trim();
    if (!text) {
      return null;
    }
    try {
      return JSON.parse(text) as Record<string, unknown>;
    } catch {
      message.error('mcp_info 不是合法 JSON，请检查粘贴内容');
      return null;
    }
  };

  const handleSubmit = async () => {
    const values = await form.validateFields();
    const mcpInfo = parseMcpInfo(values.mcp_info_json ?? '');
    if (values.mcp_info_json?.trim() && !mcpInfo) {
      return;
    }
    setSubmitting(true);
    try {
      if (editing) {
        await updateBtMcpServer(editing.id, {
          name: values.name,
          local_url: values.local_url ?? null,
          public_url: values.public_url ?? null,
          address_mode: values.address_mode,
          remark: values.remark ?? null,
          enabled: values.enabled,
          ...(values.api_token !== undefined ? { api_token: values.api_token } : {}),
          ...(values.ca_cert !== undefined ? { ca_cert: values.ca_cert } : {}),
          ...(mcpInfo ? { mcp_info: mcpInfo } : {}),
        });
        message.success('服务器连接已更新');
      } else {
        await addBtMcpServer({
          name: values.name,
          local_url: values.local_url ?? null,
          public_url: values.public_url ?? null,
          address_mode: values.address_mode,
          api_token: values.api_token ?? null,
          ca_cert: values.ca_cert ?? null,
          remark: values.remark ?? null,
          enabled: values.enabled,
          ...(mcpInfo ? { mcp_info: mcpInfo } : {}),
        });
        message.success('服务器连接已添加，建议点击「测试连接」验证');
      }
      setModalOpen(false);
      loadServers();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleTest = async (server: BtMcpServerItem) => {
    setTestingId(server.id);
    setTestResult(null);
    try {
      const result = await testBtMcpServer(server.id);
      const summary = result.tool_summary
        ? Object.entries(result.tool_summary)
            .map(([cat, n]) => `${cat} ${n}`)
            .join('，')
        : '';
      setTestResult(
        `${result.success ? '✓' : '✗'} ${server.name}：${result.message}${
          summary ? `（${summary}）` : ''
        }`
      );
    } catch (err) {
      setTestResult(`✗ ${server.name}：${err instanceof Error ? err.message : '测试失败'}`);
    } finally {
      setTestingId(null);
    }
  };

  const handleToggle = async (server: BtMcpServerItem, enabled: boolean) => {
    try {
      await updateBtMcpServer(server.id, { enabled });
      loadServers();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '更新失败');
    }
  };

  const showTools = async (server: BtMcpServerItem) => {
    setToolsServer(server);
    setToolsLoading(true);
    setTools([]);
    try {
      setTools(await fetchBtMcpTools(server.id));
    } catch (err) {
      message.error(err instanceof Error ? err.message : '拉取工具清单失败');
    } finally {
      setToolsLoading(false);
    }
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (name: string, record: BtMcpServerItem) => (
        <Space size={4}>
          <span style={{ color: COLORS.textPrimary }}>{name}</span>
          {record.enabled ? null : <Tag color="default">已停用</Tag>}
        </Space>
      ),
    },
    {
      title: '连接地址',
      dataIndex: 'address_mode',
      render: (_: unknown, record: BtMcpServerItem) => (
        <Text style={{ color: COLORS.textSecondary, fontSize: 12 }}>
          {record.address_mode === 'public' ? record.public_url : record.local_url}
        </Text>
      ),
    },
    {
      title: '凭据',
      dataIndex: 'has_token',
      render: (_: unknown, record: BtMcpServerItem) => (
        <Space size={4}>
          <Tag color={record.has_token ? 'blue' : 'red'}>{record.has_token ? '令牌已配' : '缺令牌'}</Tag>
          <Tag color={record.has_ca_cert ? 'blue' : 'default'}>
            {record.has_ca_cert ? '证书已配' : '无证书'}
          </Tag>
        </Space>
      ),
    },
    {
      title: '操作',
      render: (_: unknown, record: BtMcpServerItem) => (
        <Space size={4} wrap>
          <Button size="small" type="link" onClick={() => handleTest(record)} loading={testingId === record.id}>
            测试连接
          </Button>
          <Button size="small" type="link" onClick={() => showTools(record)}>
            工具清单
          </Button>
          <Button size="small" type="link" onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Popconfirm
            title="删除该服务器连接？"
            onConfirm={async () => {
              await deleteBtMcpServer(record.id);
              message.success('已删除');
              loadServers();
            }}
          >
            <Button size="small" type="link" danger>
              删除
            </Button>
          </Popconfirm>
          <Switch
            size="small"
            checked={record.enabled}
            onChange={(checked) => handleToggle(record, checked)}
          />
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <Title level={5} style={{ color: COLORS.textPrimary, margin: 0 }}>
            宝塔 MCP 服务器
          </Title>
          <Text style={{ color: COLORS.textSecondary }}>
            配置目标服务器的宝塔 MCP 连接（AI 部署一条龙用）；权限与工具开关在服务器端插件管理
          </Text>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate} style={primaryButtonStyle}>
          添加服务器
        </Button>
      </div>

      {testResult && (
        <Text
          style={{ display: 'block', marginBottom: 12, fontSize: 12, color: COLORS.textSecondary }}
          data-testid="bt-mcp-test-result"
        >
          {testResult}
        </Text>
      )}

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={false}
        locale={{ emptyText: <Empty description="尚未添加宝塔 MCP 服务器" /> }}
      />

      <Modal
        title={editing ? '编辑服务器连接' : '添加宝塔 MCP 服务器'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSubmit}
        confirmLoading={submitting}
        okText={editing ? '保存' : '添加'}
        width={640}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="mcp_info_json"
            label="粘贴接入 JSON（可选，宝塔插件「接入与体验」页生成的 mcp_info）"
            extra="粘贴后自动填充令牌/双地址/证书链；也可下方手动填写"
          >
            <Input.TextArea rows={3} placeholder='{"api_token": "btmcp_...", "local_host": "https://..."}' />
          </Form.Item>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：生产服务器-101.13" maxLength={100} />
          </Form.Item>
          <Form.Item name="local_url" label="内网 MCP 地址" extra="与中枢同网段时使用（推荐）">
            <Input placeholder="https://192.168.1.13:8765/bt-mcp-xxxx/mcp" />
          </Form.Item>
          <Form.Item name="public_url" label="公网 MCP 地址（可选）" extra="跨网段访问时使用，证书 SAN 需覆盖该地址">
            <Input placeholder="https://xxx.xxx.xxx.xxx:8765/bt-mcp-xxxx/mcp" />
          </Form.Item>
          <Form.Item name="address_mode" label="中枢使用地址" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'local', label: '内网地址（同网段，更快更稳）' },
                { value: 'public', label: '公网地址（跨网段/异地）' },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="api_token"
            label={editing ? '授权令牌（留空保留现值，填空串清除）' : '授权令牌'}
            extra="宝塔面板插件【安全授权】页获取"
          >
            <Input.Password placeholder="btmcp_..." autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="ca_cert"
            label={editing ? 'CA 证书链 PEM（留空保留现值）' : 'CA 证书链 PEM（自签证书场景，可含多张）'}
            extra="mcp_info 导入时自动填充；无自签证书可留空"
          >
            <Input.TextArea rows={3} placeholder="-----BEGIN CERTIFICATE-----" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input placeholder="可选" maxLength={500} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`工具清单：${toolsServer?.name ?? ''}`}
        open={toolsServer !== null}
        onCancel={() => setToolsServer(null)}
        footer={null}
        width={720}
      >
        <Table
          rowKey="name"
          size="small"
          loading={toolsLoading}
          dataSource={tools}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: '工具', dataIndex: 'name', width: 180 },
            {
              title: '分类',
              dataIndex: 'category',
              width: 90,
              render: (c: string) => <Tag>{c}</Tag>,
            },
            {
              title: '风险',
              dataIndex: 'risk',
              width: 90,
              render: (r: string) => (
                <Tag color={RISK_COLOR[r] ?? 'default'}>{RISK_LABEL[r] ?? r}</Tag>
              ),
            },
            {
              title: '说明',
              dataIndex: 'description',
              render: (d: string) => (
                <Text style={{ color: COLORS.textSecondary, fontSize: 12 }}>{d}</Text>
              ),
            },
          ]}
        />
      </Modal>
    </div>
  );
}
