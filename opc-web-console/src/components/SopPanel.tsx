import { useCallback, useEffect, useState } from 'react';
import {
  App as AntApp,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { createSop, deleteSop, fetchSopList, updateSop } from '@/api/sop';
import type { SopItem } from '@/types/sop';

/**
 * SOP 知识管理面板（ADR-051）：内置场景要点 + 对话固化的部署 SOP。
 * bt_deploy_guide 按 scene 命中注入，此处负责查看/编辑/启停/删除/新建。
 */
export function SopPanel() {
  const { message } = AntApp.useApp();
  const [items, setItems] = useState<SopItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState('');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState<SopItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchSopList({
        keyword: keyword || undefined,
        source: sourceFilter === 'all' ? undefined : sourceFilter,
        page,
        page_size: pageSize,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载 SOP 列表失败');
    } finally {
      setLoading(false);
    }
  }, [keyword, sourceFilter, page, pageSize, message]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  const openEdit = (record: SopItem) => {
    setCreating(false);
    setActive(record);
    form.setFieldsValue({
      name: record.name,
      scene: record.scene,
      content: record.content,
      enabled: record.enabled,
    });
  };

  const openCreate = () => {
    setCreating(true);
    setActive(null);
    form.setFieldsValue({ name: '', scene: '', content: '', enabled: true });
  };

  const handleSave = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      if (creating) {
        await createSop({ ...values, source: 'learned' });
        message.success('SOP 已创建');
      } else if (active) {
        await updateSop(active.id, values);
        message.success('已保存');
      }
      setActive(null);
      setCreating(false);
      void load();
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (record: SopItem) => {
    try {
      await deleteSop(record.id);
      message.success('已删除');
      void load();
    } catch {
      message.error('删除失败');
    }
  };

  const handleToggle = async (record: SopItem, enabled: boolean) => {
    try {
      await updateSop(record.id, { enabled });
      void load();
    } catch {
      message.error('更新失败');
    }
  };

  const columns: ColumnsType<SopItem> = [
    {
      title: '名称',
      dataIndex: 'name',
      ellipsis: true,
      render: (value: string, record) => (
        <Button type="link" size="small" style={{ paddingLeft: 0 }} onClick={() => openEdit(record)}>
          {value}
        </Button>
      ),
    },
    {
      title: '场景',
      dataIndex: 'scene',
      width: 110,
      render: (value: string) => <Tag color="blue">{value}</Tag>,
    },
    {
      title: '来源',
      dataIndex: 'source',
      width: 90,
      render: (value: string) =>
        value === 'builtin' ? <Tag>内置</Tag> : <Tag color="green">固化</Tag>,
    },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 80,
      render: (_: boolean, record) => (
        <Switch size="small" checked={record.enabled} onChange={(v) => void handleToggle(record, v)} />
      ),
    },
    { title: '命中', dataIndex: 'hit_count', width: 70, align: 'right' },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      width: 165,
      render: (value: string) => new Date(value).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Popconfirm title="确认删除该 SOP？" onConfirm={() => void handleDelete(record)}>
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <Space wrap style={{ marginBottom: 16 }}>
        <Input.Search
          allowClear
          placeholder="搜索名称 / 场景 / 正文"
          style={{ width: 260 }}
          onSearch={(value) => {
            setKeyword(value.trim());
            setPage(1);
          }}
        />
        <Select
          style={{ width: 120 }}
          value={sourceFilter}
          onChange={(value) => {
            setSourceFilter(String(value));
            setPage(1);
          }}
          options={[
            { label: '全部来源', value: 'all' },
            { label: '内置', value: 'builtin' },
            { label: '固化', value: 'learned' },
          ]}
        />
        <Button onClick={openCreate}>新建 SOP</Button>
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table
        rowKey="id"
        size="middle"
        columns={columns}
        dataSource={items}
        loading={loading}
        locale={{ emptyText: <Empty description="暂无 SOP（内置要点由系统迁移提供）" /> }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          onChange: (nextPage, nextSize) => {
            setPage(nextPage);
            setPageSize(nextSize);
          },
        }}
      />
      <Drawer
        title={creating ? '新建 SOP' : '编辑 SOP'}
        width={560}
        open={creating || active !== null}
        onClose={() => {
          setActive(null);
          setCreating(false);
        }}
        destroyOnHidden
        extra={
          <Space>
            <Button
              onClick={() => {
                setActive(null);
                setCreating(false);
              }}
            >
              取消
            </Button>
            <Button type="primary" loading={saving} onClick={() => void handleSave()}>
              保存
            </Button>
          </Space>
        }
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：FastAPI 项目部署到宝塔" />
          </Form.Item>
          <Form.Item name="scene" label="场景标识" rules={[{ required: true }]}>
            <Input placeholder="如 python/html/php 或自定义小写标识" />
          </Form.Item>
          <Form.Item name="content" label="SOP 正文（Markdown 要点式）" rules={[{ required: true }]}>
            <Input.TextArea rows={12} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
        {!creating && (
          <Typography.Text type="secondary">
            固化来源的 SOP 可随时编辑纠正；禁用后 bt_deploy_guide 不再命中。
          </Typography.Text>
        )}
      </Drawer>
    </>
  );
}
