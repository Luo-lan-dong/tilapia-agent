/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：LLM 模型管理面板的
   列表展示、新增/编辑表单与启用切换共享同一份模型列表状态，拆分需跨文件提升状态
   反而增加复杂度，故整体豁免。 */
import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Button,
  Checkbox,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import {
  addLLMModel,
  activateLLMModel,
  deleteLLMModel,
  fetchLLMModels,
  updateLLMModel,
} from '@/api/llmProvider';
import type { LLMModelCreateRequest, LLMModelItem } from '@/types/llmProvider';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';

const { Title, Text } = Typography;

/**
 * 格式化窗口大小为可读文本（K/M token）。
 */
function formatWindow(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 === 0 ? 0 : 1)}M`;
  }
  return `${Math.round(tokens / 1024)}K`;
}

/**
 * 模型管理面板（ADR-030）。
 *
 * 列表展示已添加的 OpenAI 兼容自定义模型（模型 / 服务商 / 操作），
 * 支持「添加模型」（服务商名称 + 模型 ID + Base URL + API Key）、
 * 一键设为使用中（热生效）与删除（使用中的模型拒绝删除）。
 */
export function LLMModelsPanel() {
  const { message } = App.useApp();
  const [form] = Form.useForm<LLMModelCreateRequest>();
  const [items, setItems] = useState<LLMModelItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [editing, setEditing] = useState<LLMModelItem | null>(null);
  const [editForm] = Form.useForm<{ context_window: number; supports_vision: boolean }>();

  const loadModels = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchLLMModels();
      setItems(data);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '加载模型列表失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    let cancelled = false;
    fetchLLMModels()
      .then((data) => {
        if (!cancelled) {
          setItems(data);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          message.error(err instanceof Error ? err.message : '加载模型列表失败');
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
    // 仅挂载时加载一次：竞态由 cancelled 标志兜底
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAdd = useCallback(
    async (values: LLMModelCreateRequest) => {
      setSubmitting(true);
      try {
        await addLLMModel({
          provider: values.provider.trim(),
          model: values.model.trim(),
          base_url: values.base_url.trim(),
          api_key: values.api_key.trim(),
          // ADR-039/041：视觉能力与上下文窗口随创建提交
          supports_vision: values.supports_vision ?? false,
          context_window: values.context_window ?? 65536,
        });
        message.success('模型已添加');
        setModalOpen(false);
        form.resetFields();
        await loadModels();
      } catch (err) {
        message.error(err instanceof Error ? err.message : '添加失败');
      } finally {
        setSubmitting(false);
      }
    },
    [form, loadModels, message],
  );

  const handleEditSave = useCallback(
    async (values: { context_window: number; supports_vision: boolean }) => {
      if (!editing) {
        return;
      }
      setSubmitting(true);
      try {
        await updateLLMModel(editing.id, {
          context_window: values.context_window,
          supports_vision: values.supports_vision,
        });
        message.success('模型配置已更新');
        setEditing(null);
        await loadModels();
      } catch (err) {
        message.error(err instanceof Error ? err.message : '更新失败');
      } finally {
        setSubmitting(false);
      }
    },
    [editing, loadModels, message],
  );

  const handleActivate = useCallback(
    async (item: LLMModelItem) => {
      try {
        await activateLLMModel(item.id);
        message.success(`已切换为使用中：${item.model}`);
        await loadModels();
      } catch (err) {
        message.error(err instanceof Error ? err.message : '切换失败');
      }
    },
    [loadModels, message],
  );

  const handleDelete = useCallback(
    async (item: LLMModelItem) => {
      try {
        await deleteLLMModel(item.id);
        message.success(
          item.is_active ? '模型已删除，已自动切换主模型' : '模型已删除',
        );
        await loadModels();
      } catch (err) {
        message.error(err instanceof Error ? err.message : '删除失败');
      }
    },
    [loadModels, message],
  );

  const columns = [
    {
      title: '模型',
      dataIndex: 'model',
      key: 'model',
      render: (model: string, record: LLMModelItem) => (
        <span>
          <span style={{ color: COLORS.textPrimary }}>{model}</span>
          {record.is_active && (
            <Tag color="green" style={{ marginInlineStart: 8 }}>
              使用中
            </Tag>
          )}
          {record.supports_vision && (
            <Tag color="blue" style={{ marginInlineStart: record.is_active ? 4 : 8 }}>
              支持图片
            </Tag>
          )}
        </span>
      ),
    },
    {
      title: '服务商',
      dataIndex: 'provider',
      key: 'provider',
      render: (provider: string) => (
        <span style={{ color: COLORS.textSecondary }}>{provider}</span>
      ),
    },
    {
      title: '上下文窗口',
      dataIndex: 'context_window',
      key: 'context_window',
      width: 100,
      render: (tokens: number) => (
        <Tooltip title={`${tokens.toLocaleString()} tokens`}>
          <span style={{ color: COLORS.textSecondary }}>{formatWindow(tokens)}</span>
        </Tooltip>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 220,
      render: (_: unknown, record: LLMModelItem) => (
        <span>
          {!record.is_active && (
            <Button
              size="small"
              type="link"
              onClick={() => void handleActivate(record)}
              data-testid={`activate-model-${record.id}`}
            >
              设为主模型
            </Button>
          )}
          <Button
            size="small"
            type="link"
            data-testid={`edit-model-${record.id}`}
            onClick={() => {
              setEditing(record);
              editForm.setFieldsValue({
                context_window: record.context_window,
                supports_vision: record.supports_vision,
              });
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title="确认删除该模型？"
            description={
              record.is_active
                ? '删除后将自动切换到列表中最早的其他模型（如无则回到未配置状态）。'
                : undefined
            }
            okText="删除"
            cancelText="取消"
            onConfirm={() => void handleDelete(record)}
          >
            <Button size="small" type="link" danger>
              删除
            </Button>
          </Popconfirm>
        </span>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <div>
          <Title level={5} style={{ color: COLORS.textPrimary, margin: 0 }}>
            模型管理
          </Title>
          <Text style={{ color: COLORS.textSecondary }}>
            配置 API key 添加更多可用模型（OpenAI 兼容协议），「使用中」模型即刻生效。
          </Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setModalOpen(true)}
          data-testid="add-model-button"
          style={primaryButtonStyle}
        >
          添加模型
        </Button>
      </div>

      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={false}
        locale={{
          emptyText: <Empty description="暂无模型，点击「添加模型」接入 OpenAI 兼容端点" />,
        }}
        data-testid="llm-models-table"
      />

      <Modal
        title="添加模型"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={submitting}
        okText="添加"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={handleAdd}>
          <Form.Item
            label="服务商名称"
            name="provider"
            rules={[{ required: true, message: '请输入服务商名称，如 DeepSeek / 硅基流动' }]}
          >
            <Input placeholder="DeepSeek / 硅基流动 / OpenRouter …" maxLength={32} />
          </Form.Item>
          <Form.Item
            label="模型 ID"
            name="model"
            rules={[{ required: true, message: '请输入模型 ID' }]}
          >
            <Input placeholder="deepseek-chat" maxLength={128} />
          </Form.Item>
          <Form.Item
            label="Base URL"
            name="base_url"
            rules={[{ required: true, message: '请输入 OpenAI 兼容 Base URL' }]}
          >
            <Input placeholder="https://api.siliconflow.cn/v1" maxLength={256} />
          </Form.Item>
          <Form.Item
            label="API Key"
            name="api_key"
            rules={[{ required: true, message: '请输入 API Key' }]}
          >
            <Input.Password placeholder="sk-..." maxLength={512} />
          </Form.Item>
          <Form.Item name="supports_vision" valuePropName="checked" style={{ marginBottom: 8 }}>
            <Checkbox data-testid="model-vision-checkbox">支持图片输入（视觉模型）</Checkbox>
          </Form.Item>
          <Form.Item
            label="上下文窗口（token）"
            name="context_window"
            initialValue={65536}
            rules={[{ required: true, message: '请输入模型上下文窗口大小' }]}
            style={{ marginBottom: 0 }}
          >
            <InputNumber min={1024} max={10_000_000} step={1024} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`编辑模型：${editing?.model ?? ''}`}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => editForm.submit()}
        confirmLoading={submitting}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={editForm} layout="vertical" onFinish={handleEditSave}>
          <Form.Item
            label="上下文窗口（token）"
            name="context_window"
            rules={[{ required: true, message: '请输入模型上下文窗口大小' }]}
          >
            <InputNumber min={1024} max={10_000_000} step={1024} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="supports_vision" valuePropName="checked" style={{ marginBottom: 0 }}>
            <Checkbox>支持图片输入（视觉模型）</Checkbox>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
