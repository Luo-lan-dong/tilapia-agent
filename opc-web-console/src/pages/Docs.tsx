import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import { PlusOutlined, UploadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  createStandardDoc,
  deleteStandardDoc,
  fetchStandardDocDetail,
  fetchStandardDocs,
  updateStandardDoc,
} from '@/api/standardDoc';
import type { StandardDoc, StandardDocDetail } from '@/types/standardDoc';
import { COLORS } from '@/theme/tokens';

const { Title, Text, Paragraph } = Typography;

interface DocFormValues {
  name: string;
  category?: string;
  description?: string;
  apply_when?: string;
  content: string;
}

/**
 * 规则文档管理页（ADR-048）。
 *
 * 维护中枢规则文档库：预置模板（coding-standards / decision-documents）
 * 与用户上传的自定义规范；创建任务时可将文档绑定到任务，派发时随
 * payload 下发给节点，节点端按 AGENTS.md 索引按需加载。
 */
export function Docs() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(true);
  const [docs, setDocs] = useState<StandardDoc[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<StandardDoc | null>(null);
  const [form] = Form.useForm<DocFormValues>();
  const [preview, setPreview] = useState<StandardDocDetail | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);

  const loadDocs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchStandardDocs();
      setDocs(data.items);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '文档列表加载失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    // 异步 IIFE 中加载（react-hooks/set-state-in-effect 要求 setState
    // 出现在异步回调而非 effect 同步调用链上）
    void (async () => {
      await loadDocs();
    })();
  }, [loadDocs]);

  const openCreate = useCallback(() => {
    setEditing(null);
    form.resetFields();
    setEditorOpen(true);
  }, [form]);

  const openEdit = useCallback(
    (doc: StandardDoc) => {
      setEditing(doc);
      void (async () => {
        try {
          const detail = await fetchStandardDocDetail(doc.id);
          form.setFieldsValue({
            name: detail.name,
            category: detail.category ?? undefined,
            description: detail.description ?? undefined,
            apply_when: detail.apply_when ?? undefined,
            content: detail.content,
          });
          setEditorOpen(true);
        } catch (err) {
          message.error(err instanceof Error ? err.message : '文档详情加载失败');
        }
      })();
    },
    [form, message],
  );

  const handleSave = useCallback(async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      if (editing) {
        await updateStandardDoc(editing.id, {
          category: values.category ?? null,
          description: values.description ?? null,
          apply_when: values.apply_when ?? null,
          content: values.content,
        });
        message.success('文档已更新（历史任务仍使用创建时快照）');
      } else {
        await createStandardDoc(values);
        message.success('文档已创建');
      }
      setEditorOpen(false);
      await loadDocs();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  }, [editing, form, loadDocs, message]);

  const handleDelete = useCallback(
    async (doc: StandardDoc) => {
      try {
        await deleteStandardDoc(doc.id);
        message.success('文档已删除');
        await loadDocs();
      } catch (err) {
        message.error(err instanceof Error ? err.message : '删除失败');
      }
    },
    [loadDocs, message],
  );

  const openPreview = useCallback(
    (doc: StandardDoc) => {
      setPreviewOpen(true);
      setPreviewLoading(true);
      void (async () => {
        try {
          setPreview(await fetchStandardDocDetail(doc.id));
        } catch (err) {
          message.error(err instanceof Error ? err.message : '文档详情加载失败');
        } finally {
          setPreviewLoading(false);
        }
      })();
    },
    [message],
  );

  const handleUploadFile = useCallback(
    (file: File) => {
      void (async () => {
        try {
          const text = await file.text();
          const baseName = file.name.replace(/\.md$/i, '');
          setEditing(null);
          form.resetFields();
          form.setFieldsValue({ name: baseName, content: text });
          setEditorOpen(true);
        } catch {
          message.error('文件读取失败');
        }
      })();
      // 阻止 antd Upload 默认上传行为
      return false;
    },
    [form, message],
  );

  const columns: ColumnsType<StandardDoc> = [
    {
      title: '文档名',
      dataIndex: 'name',
      width: 260,
      ellipsis: true,
      render: (name: string, doc) => (
        <Space>
          <Button type="link" size="small" style={{ padding: 0 }} onClick={() => openPreview(doc)}>
            {name}
          </Button>
          {doc.is_builtin && <Tag color="blue">预置</Tag>}
        </Space>
      ),
    },
    {
      title: '分类',
      dataIndex: 'category',
      width: 130,
      render: (category: string | null) => category ?? '-',
    },
    {
      title: '用途说明',
      dataIndex: 'description',
      ellipsis: true,
      render: (description: string | null) => description ?? '-',
    },
    {
      title: '何时必须加载',
      dataIndex: 'apply_when',
      ellipsis: true,
      render: (applyWhen: string | null) => applyWhen ?? '-',
    },
    { title: '版本', dataIndex: 'version', width: 70 },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 180,
      render: (value: string) => new Date(value).toLocaleString(),
    },
    {
      title: '操作',
      key: 'actions',
      width: 190,
      render: (_, doc) => (
        <Space>
          <Button type="link" size="small" style={{ padding: 0 }} onClick={() => openEdit(doc)}>
            编辑
          </Button>
          <Popconfirm
            title="确认删除该文档？"
            description="已创建任务使用创建时快照，不受影响。"
            onConfirm={() => void handleDelete(doc)}
          >
            <Button type="link" size="small" danger style={{ padding: 0 }}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24, height: '100%', overflowY: 'auto' }}>
      <Card
        title={<Title level={4} style={{ margin: 0 }}>规则文档</Title>}
        extra={
          <Space>
            <Upload accept=".md,.markdown,.txt" showUploadList={false} beforeUpload={handleUploadFile}>
              <Button icon={<UploadOutlined />}>上传 .md</Button>
            </Upload>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新建文档
            </Button>
          </Space>
        }
      >
        <Paragraph type="secondary" style={{ marginTop: 0 }}>
          创建任务时可将文档绑定到任务（版本锁定），派发时下发给节点；节点端按索引按需加载，
          不选择则节点不加载任何规范。
        </Paragraph>
        <Spin spinning={loading}>
          <Table
            rowKey="id"
            columns={columns}
            dataSource={docs}
            pagination={false}
            locale={{
              emptyText: <Empty description="暂无文档，可新建或上传规范文档" />,
            }}
          />
        </Spin>
      </Card>

      <Modal
        title={editing ? `编辑文档：${editing.name}` : '新建文档'}
        open={editorOpen}
        onOk={() => void handleSave()}
        onCancel={() => setEditorOpen(false)}
        confirmLoading={saving}
        width={720}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
        forceRender
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="文档名"
            rules={[{ required: true, message: '请输入文档名' }]}
            extra={editing ? '文档名不可修改' : '唯一标识，节点端按此生成落盘文件名'}
          >
            <Input maxLength={100} disabled={!!editing} placeholder="如：python-backend-standards" />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Input maxLength={50} placeholder="如：coding-standard / decision / custom" />
          </Form.Item>
          <Form.Item name="description" label="用途说明">
            <Input.TextArea rows={2} maxLength={500} placeholder="供 AI 编排时介绍该文档的用途" />
          </Form.Item>
          <Form.Item
            name="apply_when"
            label="何时必须加载"
            extra="节点端 AGENTS.md 索引依据：动工哪部分就读哪份，避免全量加载"
          >
            <Input maxLength={500} placeholder="如：写/改后端 Python 代码前" />
          </Form.Item>
          <Form.Item
            name="content"
            label="Markdown 全文"
            rules={[{ required: true, message: '请输入文档内容' }]}
          >
            <Input.TextArea rows={16} style={{ fontFamily: 'monospace' }} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={preview ? `预览：${preview.name}（v${preview.version}）` : '预览'}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={760}
        destroyOnHidden
      >
        <Spin spinning={previewLoading}>
          <Text type="secondary">
            何时加载：{preview?.apply_when ?? '（未标注）'}
          </Text>
          <Paragraph
            style={{
              whiteSpace: 'pre-wrap',
              fontFamily: 'monospace',
              background: COLORS.surface,
              padding: 12,
              borderRadius: 8,
              maxHeight: 480,
              overflowY: 'auto',
            }}
          >
            {preview?.content ?? ''}
          </Paragraph>
        </Spin>
      </Modal>
    </div>
  );
}
