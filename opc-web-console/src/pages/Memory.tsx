import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App as AntApp,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Segmented,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { deleteExperience, fetchExperienceList, updateExperience } from '@/api/experience';
import type { ExperienceItem, ExperienceStatus } from '@/types/experience';
import { SopPanel } from '@/components/SopPanel';

const STATUS_OPTIONS = [
  { label: '全部', value: 'all' },
  { label: '待总结', value: 'pending' },
  { label: '已解决', value: 'resolved' },
];

const SIGNATURE_MAX = 80;

/**
 * 经验记忆页面（ADR-050）：跨会话踩坑结论库的管理入口。
 *
 * 列表（搜索/状态过滤）+ 详情抽屉（三栏可编辑）+ 删除；
 * 待总结条目高亮，引导用户确认 AI 的收尾总结。
 */
export function Memory() {
  const { message } = AntApp.useApp();
  const [items, setItems] = useState<ExperienceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState<ExperienceItem | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const formInitial = useMemo(() => ({}), []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchExperienceList({
        keyword: keyword || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
        page,
        page_size: pageSize,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载经验列表失败');
    } finally {
      setLoading(false);
    }
  }, [keyword, statusFilter, page, pageSize, message]);

  useEffect(() => {
    // 异步 IIFE 中加载（react-hooks/set-state-in-effect 要求 setState
    // 出现在异步回调而非 effect 同步调用链上）
    void (async () => {
      await load();
    })();
  }, [load]);

  const openDetail = (record: ExperienceItem) => {
    setActive(record);
    form.setFieldsValue({
      tool: record.tool,
      error_signature: record.error_signature,
      problem: record.problem ?? '',
      root_cause: record.root_cause ?? '',
      solution: record.solution ?? '',
      status: record.status,
    });
  };

  const handleSave = async () => {
    if (!active) {
      return;
    }
    const values = await form.validateFields();
    setSaving(true);
    try {
      await updateExperience(active.id, values);
      message.success('已保存');
      setActive(null);
      void load();
    } catch {
      message.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (record: ExperienceItem) => {
    try {
      await deleteExperience(record.id);
      message.success('已删除');
      if (active?.id === record.id) {
        setActive(null);
      }
      void load();
    } catch {
      message.error('删除失败');
    }
  };

  const columns: ColumnsType<ExperienceItem> = [
    {
      title: '工具',
      dataIndex: 'tool',
      width: 160,
      render: (value: string) => value || <Typography.Text type="secondary">通用</Typography.Text>,
    },
    {
      title: '错误签名',
      dataIndex: 'error_signature',
      ellipsis: true,
      render: (value: string) => <Typography.Text code>{value.slice(0, SIGNATURE_MAX)}</Typography.Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (value: ExperienceStatus) =>
        value === 'resolved' ? (
          <Tag color="green">已解决</Tag>
        ) : (
          <Tag color="orange">待总结</Tag>
        ),
    },
    { title: '命中', dataIndex: 'hit_count', width: 70, align: 'right' },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 170,
      render: (value: string) => new Date(value).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      render: (_, record) => (
        <Space>
          <Button type="link" size="small" onClick={() => openDetail(record)}>
            详情
          </Button>
          <Popconfirm title="确认删除该经验条目？" onConfirm={() => void handleDelete(record)}>
            <Button type="link" size="small" danger>
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
        title={
          <Typography.Title level={4} style={{ margin: 0 }}>
            记忆
          </Typography.Title>
        }
      >
        <Tabs
          defaultActiveKey="experience"
          items={[
            {
              key: 'experience',
              label: '经验记忆',
              children: (
                <>
                  <Space wrap style={{ marginBottom: 16 }}>
                    <Input.Search
                      allowClear
                      placeholder="搜索工具 / 签名 / 解法关键词"
                      style={{ width: 280 }}
                      onSearch={(value) => {
                        setKeyword(value.trim());
                        setPage(1);
                      }}
                    />
                    <Segmented
                      options={STATUS_OPTIONS}
                      value={statusFilter}
                      onChange={(value) => {
                        setStatusFilter(String(value));
                        setPage(1);
                      }}
                    />
                    <Button onClick={() => void load()}>刷新</Button>
                  </Space>
                  <Table
                    rowKey="id"
                    size="middle"
                    columns={columns}
                    dataSource={items}
                    loading={loading}
                    locale={{
                      emptyText: <Empty description="暂无经验条目（AI 踩坑后会自动记录）" />,
                    }}
                    rowClassName={(record) =>
                      record.status === 'pending' ? 'memory-row-pending' : ''
                    }
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
                </>
              ),
            },
            {
              key: 'sops',
              label: '部署 SOP',
              children: <SopPanel />,
            },
          ]}
        />
      </Card>
      <Drawer
        title="经验详情"
        width={560}
        open={active !== null}
        onClose={() => setActive(null)}
        destroyOnHidden
        extra={
          <Space>
            <Button onClick={() => setActive(null)}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => void handleSave()}>
              保存
            </Button>
          </Space>
        }
      >
        <Form form={form} initialValues={formInitial} layout="vertical">
          <Form.Item name="tool" label="工具">
            <Input placeholder="空表示通用经验" />
          </Form.Item>
          <Form.Item name="error_signature" label="错误签名">
            <Input />
          </Form.Item>
          <Form.Item name="problem" label="现象快照">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="root_cause" label="根因">
            <Input.TextArea rows={3} placeholder="AI 总结草案，可在此纠正" />
          </Form.Item>
          <Form.Item name="solution" label="解法">
            <Input.TextArea rows={3} placeholder="AI 总结草案，可在此纠正" />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              options={[
                { label: '待总结', value: 'pending' },
                { label: '已解决', value: 'resolved' },
              ]}
            />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
