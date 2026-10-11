import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  App,
  Button,
  Card,
  Col,
  DatePicker,
  Input,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { fetchAgentLogs, fetchLogs, fetchTrace } from '@/api/logs';
import { fetchAgentNodes } from '@/api/agentNodes';
import type { AgentNode } from '@/types/agentNode';
import type { LogEntry, TraceEvent } from '@/types/logs';
import { COLORS } from '@/theme/tokens';
import './Logs.css';

const { Text } = Typography;

/** 级别 → Tag 颜色 */
const LEVEL_COLORS: Record<string, string> = {
  ERROR: 'red',
  WARNING: 'orange',
  INFO: 'blue',
  DEBUG: 'default',
};

/** 链路事件 kind → 展示名 */
const KIND_LABELS: Record<string, string> = {
  llm_call: 'LLM 调用',
  skill_call: '技能调用',
  skill_result: '技能结果',
  compaction: '上下文压缩',
  steering: '插话提升',
  dispatch: '任务派发',
  agent_task_event: '节点任务事件',
};

/** 链路事件状态 → 中文 */
const STATUS_LABELS: Record<string, string> = {
  success: '成功',
  started: '进行中',
  failed: '失败',
};

/** kind → Tag 颜色 */
const KIND_COLORS: Record<string, string> = {
  llm_call: 'geekblue',
  skill_call: 'purple',
  skill_result: 'cyan',
  compaction: 'gold',
  steering: 'magenta',
  dispatch: 'green',
  agent_task_event: 'volcano',
};

const LEVEL_OPTIONS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((level) => ({
  label: level,
  value: level,
}));

function formatTs(value: string | null): string {
  if (!value) return '-';
  const parsed = dayjs(value);
  // 缓冲时间为 UTC ISO，展示转本地；解析失败（文件尾原始行）原样截断
  return parsed.isValid()
    ? parsed.format('YYYY-MM-DD HH:mm:ss')
    : value.replace('T', ' ').slice(0, 19);
}

/**
 * 日志页（ADR-047）：报错可循、执行链路可溯源。
 *
 * 两个视图：
 * - 原始日志流：内存环形缓冲 / 文件尾部的运行日志，支持时间区间、级别、
 *   关键字筛选，条目可展开查看结构化字段；
 * - 执行链路：按 request_id / session_id 拉取 tool_call_logs 全链
 *   （时间升序瀑布），定位失败环节；
 * 另附节点日志按需拉取面板。
 */
export function Logs() {
  const { message } = App.useApp();

  // ---- 原始日志流 ----
  const [source, setSource] = useState<'memory' | 'file'>('memory');
  const [levels, setLevels] = useState<string[]>([]);
  const [keyword, setKeyword] = useState('');
  const [range, setRange] = useState<[string, string] | null>(null);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const loadLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params: Parameters<typeof fetchLogs>[0] = { source, limit: 200 };
      if (levels.length) params.level = levels;
      if (keyword.trim()) params.keyword = keyword.trim();
      if (range) {
        params.start = range[0];
        params.end = range[1];
      }
      const data = await fetchLogs(params);
      setEntries(data.entries);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '日志查询失败');
    } finally {
      setLoading(false);
    }
  }, [source, levels, keyword, range, message]);

  useEffect(() => {
    void (async () => {
      await loadLogs();
    })();
    // 初始加载一次（source 变化由查询按钮/刷新触发）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  // ---- 执行链路 ----
  // recent：最近事件概览（默认自动展示）；detail：单链溯源瀑布
  const [traceView, setTraceView] = useState<'recent' | 'detail'>('recent');
  const [recentEvents, setRecentEvents] = useState<TraceEvent[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);
  const [traceKey, setTraceKey] = useState('');
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [traceLoading, setTraceLoading] = useState(false);

  const loadRecentTrace = useCallback(async () => {
    setRecentLoading(true);
    try {
      const data = await fetchTrace({ limit: 50 });
      setRecentEvents(data.events);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '最近链路事件加载失败');
    } finally {
      setRecentLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void loadRecentTrace();
  }, [loadRecentTrace]);

  const openTraceDetail = useCallback(
    async (key: string) => {
      setTraceKey(key);
      setTraceLoading(true);
      try {
        const data =
          key.startsWith('ses_') || key.length > 40
            ? await fetchTrace({ session_id: key })
            : await fetchTrace({ request_id: key });
        // 请求未命中时按会话再试一次（request_id 常缺省）
        const events =
          data.total > 0 ? data.events : (await fetchTrace({ session_id: key })).events;
        setTraceEvents(events);
        setTraceView('detail');
        if (!events.length) message.info('未找到匹配的链路事件');
      } catch (err) {
        message.error(err instanceof Error ? err.message : '链路查询失败');
      } finally {
        setTraceLoading(false);
      }
    },
    [message],
  );

  const loadTrace = useCallback(async () => {
    const key = traceKey.trim();
    if (!key) {
      message.warning('请输入 request_id 或会话 ID');
      return;
    }
    await openTraceDetail(key);
  }, [traceKey, openTraceDetail]);

  // ---- 节点日志拉取 ----
  const [nodes, setNodes] = useState<AgentNode[]>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [fetchLevel, setFetchLevel] = useState<string>('error');
  const [fetchLines, setFetchLines] = useState<number>(200);
  const [nodeLog, setNodeLog] = useState<string | null>(null);
  const [nodeFetching, setNodeFetching] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const data = await fetchAgentNodes();
        const online = data.items.filter((node) => node.status !== 'offline');
        setNodes(online);
        setSelectedNode((prev) => prev ?? online[0]?.client_id ?? null);
      } catch {
        // 节点列表加载失败不阻塞页面
      }
    })();
  }, []);

  const handleFetchNodeLog = useCallback(async () => {
    if (!selectedNode) {
      message.warning('请选择节点');
      return;
    }
    setNodeFetching(true);
    setNodeLog(null);
    try {
      const data = await fetchAgentLogs({
        client_id: selectedNode,
        tail_lines: fetchLines,
        level: fetchLevel || null,
      });
      setNodeLog(data.content || '(空)');
    } catch (err) {
      message.error(err instanceof Error ? err.message : '节点日志拉取失败');
    } finally {
      setNodeFetching(false);
    }
  }, [selectedNode, fetchLines, fetchLevel, message]);

  const entryColumns: ColumnsType<LogEntry> = useMemo(
    () => [
      {
        title: '时间',
        dataIndex: 'timestamp',
        width: 180,
        render: (value: string | null) => <Text code>{formatTs(value)}</Text>,
      },
      {
        title: '级别',
        dataIndex: 'level',
        width: 90,
        render: (value: string | null) =>
          value ? <Tag color={LEVEL_COLORS[value] ?? 'default'}>{value}</Tag> : '-',
      },
      { title: '来源', dataIndex: 'logger', width: 240, ellipsis: true },
      { title: '消息', dataIndex: 'message', ellipsis: true },
      {
        title: 'request_id',
        dataIndex: 'request_id',
        width: 150,
        ellipsis: true,
        render: (value: string | null) =>
          value ? (
            <Typography.Link
              copyable
              onClick={() => {
                setTraceKey(value);
                setTraceEvents([]);
              }}
            >
              {value.slice(0, 12)}...
            </Typography.Link>
          ) : (
            '-'
          ),
      },
    ],
    [],
  );

  const traceColumns: ColumnsType<TraceEvent> = useMemo(
    () => [
      {
        title: '时间',
        dataIndex: 'created_at',
        width: 180,
        render: (value: string | null) => <Text code>{formatTs(value)}</Text>,
      },
      {
        title: '事件',
        dataIndex: 'kind',
        width: 130,
        render: (value: string) => (
          <Tag color={KIND_COLORS[value] ?? 'default'}>{KIND_LABELS[value] ?? value}</Tag>
        ),
      },
      {
        title: (
          <Tooltip title="技能调用/技能结果=技能名；LLM 调用=模型名；任务派发=节点名；节点任务事件=生命周期阶段（accepted/started/completed/failed）">
            <span>主体</span>
          </Tooltip>
        ),
        dataIndex: 'name',
        width: 200,
        ellipsis: true,
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 90,
        render: (value: string) => (
          <Tag color={value === 'success' ? 'green' : value === 'failed' ? 'red' : 'blue'}>
            {STATUS_LABELS[value] ?? value}
          </Tag>
        ),
      },
      {
        title: '任务 / 会话',
        dataIndex: 'task_id',
        width: 130,
        render: (_: string | null, record) => (
          <Space size={0} direction="vertical">
            {record.task_id && (
              <Typography.Text code copyable={{ text: record.task_id }} style={{ fontSize: 12 }}>
                {record.task_id.slice(0, 8)}
              </Typography.Text>
            )}
            {record.session_id && (
              <Typography.Text code copyable={{ text: record.session_id }} style={{ fontSize: 12 }}>
                会话 {record.session_id.slice(0, 8)}
              </Typography.Text>
            )}
            {!record.task_id && !record.session_id && '-'}
          </Space>
        ),
      },
      {
        title: '耗时',
        dataIndex: 'duration_ms',
        width: 90,
        render: (value: number | null) => (value != null ? `${value} ms` : '-'),
      },
      {
        title: '详情',
        dataIndex: 'result_summary',
        ellipsis: true,
        render: (value: string | null, record) => {
          const text = record.error || value || record.args_summary || '-';
          return (
            <Tooltip title={<pre style={{ maxWidth: 480, whiteSpace: 'pre-wrap' }}>{text}</pre>}>
              <span>{text}</span>
            </Tooltip>
          );
        },
      },
    ],
    [],
  );

  const rangePresets = useMemo(
    () => [
      { label: '最近 1 小时', hours: 1 },
      { label: '最近 6 小时', hours: 6 },
      { label: '最近 24 小时', hours: 24 },
    ],
    [],
  );

  /** 最近事件概览列：traceColumns + 可点击 request_id（点击溯源全链） */
  const recentColumns: ColumnsType<TraceEvent> = useMemo(
    () => [
      ...traceColumns,
      {
        title: 'request_id',
        dataIndex: 'request_id',
        width: 150,
        ellipsis: true,
        render: (value: string | null) =>
          value ? (
            <Typography.Link
              copyable
              onClick={() => {
                void openTraceDetail(value);
              }}
            >
              {value.slice(0, 12)}...
            </Typography.Link>
          ) : (
            '-'
          ),
      },
    ],
    [traceColumns, openTraceDetail],
  );

  return (
    // 页名已在顶部标题栏展示（AppLayout pageTitle），页内不再重复渲染标题；
    // 固定视口高度：Tabs/筛选区固定，仅表格区域内部滚动（Logs.css）
    <div className="logs-page">
      <Tabs
        className="logs-page__tabs"
        defaultActiveKey="raw"
        items={[
          {
            key: 'raw',
            label: '原始日志流',
            children: (
              <Card
                size="small"
                className="logs-page__card"
                title={
                  <Space wrap>
                    <Segmented
                      value={source}
                      onChange={(value) => setSource(value as 'memory' | 'file')}
                      options={[
                        { label: '内存缓冲', value: 'memory' },
                        { label: '文件尾部', value: 'file' },
                      ]}
                    />
                    {source === 'memory' && (
                      <Select
                        mode="multiple"
                        allowClear
                        placeholder="级别"
                        style={{ minWidth: 180 }}
                        options={LEVEL_OPTIONS}
                        value={levels}
                        onChange={setLevels}
                      />
                    )}
                    <Input
                      allowClear
                      placeholder="关键字"
                      prefix={<SearchOutlined />}
                      style={{ width: 220 }}
                      value={keyword}
                      onChange={(e) => setKeyword(e.target.value)}
                      onPressEnter={loadLogs}
                    />
                    {source === 'memory' && (
                      <DatePicker.RangePicker
                        showTime
                        presets={rangePresets.map((preset) => ({
                          label: preset.label,
                          value: (): [Dayjs, Dayjs] => [
                            dayjs().subtract(preset.hours, 'hour'),
                            dayjs(),
                          ],
                        }))}
                        onChange={(_, strings) => {
                          setRange(strings[0] && strings[1] ? [strings[0], strings[1]] : null);
                        }}
                      />
                    )}
                    <Button type="primary" icon={<ReloadOutlined />} loading={loading} onClick={loadLogs}>
                      查询
                    </Button>
                  </Space>
                }
              >
                <Table<LogEntry>
                  rowKey={(_, index) => String(index)}
                  size="small"
                  loading={loading}
                  columns={entryColumns}
                  dataSource={entries}
                  sticky
                  pagination={{ pageSize: 50, showSizeChanger: false }}
                  expandable={{
                    expandedRowRender: (record) => (
                      <pre style={{ margin: 0, fontSize: 12, whiteSpace: 'pre-wrap' }}>
                        {JSON.stringify(record.metadata ?? {}, null, 2)}
                      </pre>
                    ),
                    rowExpandable: (record) =>
                      Boolean(record.metadata && Object.keys(record.metadata).length > 0),
                  }}
                />
              </Card>
            ),
          },
          {
            key: 'trace',
            label: '执行链路',
            children: (
              <Card size="small" className="logs-page__card">
                <Space.Compact style={{ width: '100%', maxWidth: 720 }}>
                  <Input
                    placeholder="输入 request_id 或会话 ID（溯源执行链路）"
                    value={traceKey}
                    onChange={(e) => setTraceKey(e.target.value)}
                    onPressEnter={loadTrace}
                  />
                  <Button type="primary" loading={traceLoading} onClick={loadTrace}>
                    溯源
                  </Button>
                </Space.Compact>
                <Typography.Paragraph
                  type="secondary"
                  style={{ marginTop: 12, marginBottom: 0, fontSize: 12 }}
                >
                  执行链路记录中枢侧编排事件（LLM 调用 / 技能调用 / 上下文压缩 / 插话提升 /
                  派发决策）与节点任务生命周期上报；点击事件可按 request_id
                  溯源完整链路。节点本地详细日志请使用「节点日志」标签页。
                </Typography.Paragraph>
                {traceView === 'recent' ? (
                  <Table<TraceEvent>
                    rowKey="id"
                    size="small"
                    style={{ marginTop: 16 }}
                    loading={recentLoading}
                    columns={recentColumns}
                    dataSource={recentEvents}
                    sticky
                    pagination={{ pageSize: 20, showSizeChanger: false }}
                    locale={{ emptyText: '暂无链路事件（对话编排 / 任务派发后自动记录）' }}
                  />
                ) : (
                  <>
                    <Button
                      type="link"
                      size="small"
                      style={{ paddingLeft: 0, marginTop: 8 }}
                      onClick={() => {
                        setTraceView('recent');
                        void loadRecentTrace();
                      }}
                    >
                      ← 返回最近事件
                    </Button>
                    {traceLoading ? (
                      <div style={{ textAlign: 'center', padding: 40 }}>
                        <Spin />
                      </div>
                    ) : traceEvents.length > 0 ? (
                      <Row gutter={16}>
                        <Col span={14}>
                          <Table<TraceEvent>
                            rowKey="id"
                            size="small"
                            columns={traceColumns}
                            dataSource={traceEvents}
                            pagination={false}
                          />
                        </Col>
                        <Col span={10}>
                          <Timeline
                            items={traceEvents.map((event) => ({
                              color:
                                event.status === 'failed'
                                  ? 'red'
                                  : event.status === 'success'
                                    ? 'green'
                                    : 'blue',
                              children: (
                                <div>
                                  <Space size={4} wrap>
                                    <Tag color={KIND_COLORS[event.kind] ?? 'default'}>
                                      {KIND_LABELS[event.kind] ?? event.kind}
                                    </Tag>
                                    <Text strong>{event.name}</Text>
                                    {event.duration_ms != null && (
                                      <Text type="secondary">{event.duration_ms} ms</Text>
                                    )}
                                  </Space>
                                  {(event.error || event.result_summary) && (
                                    <div>
                                      <Text
                                        type={event.error ? 'danger' : 'secondary'}
                                        style={{ fontSize: 12, wordBreak: 'break-all' }}
                                      >
                                        {(event.error || event.result_summary)?.slice(0, 160)}
                                      </Text>
                                    </div>
                                  )}
                                </div>
                              ),
                            }))}
                          />
                        </Col>
                      </Row>
                    ) : (
                      <div
                        style={{ color: COLORS.textSecondary, padding: 24, textAlign: 'center' }}
                      >
                        未找到该链路的事件，可返回最近事件列表选择其他链路。
                      </div>
                    )}
                  </>
                )}
              </Card>
            ),
          },
          {
            key: 'agent',
            label: '节点日志',
            children: (
              <Card size="small" className="logs-page__card">
                <Space wrap>
                  <Select
                    showSearch
                    placeholder="选择节点"
                    style={{ minWidth: 260 }}
                    value={selectedNode ?? undefined}
                    onChange={setSelectedNode}
                    options={nodes.map((node) => ({
                      label: `${node.name}（${node.status}）`,
                      value: node.client_id ?? node.id,
                    }))}
                  />
                  <Select
                    style={{ width: 120 }}
                    value={fetchLevel}
                    onChange={setFetchLevel}
                    options={[
                      { label: 'ERROR', value: 'error' },
                      { label: 'WARNING', value: 'warning' },
                      { label: 'INFO', value: 'info' },
                      { label: '全部', value: '' },
                    ]}
                  />
                  <Select
                    style={{ width: 140 }}
                    value={fetchLines}
                    onChange={setFetchLines}
                    options={[100, 200, 500, 1000, 2000].map((n) => ({
                      label: `最近 ${n} 行`,
                      value: n,
                    }))}
                  />
                  <Button
                    type="primary"
                    icon={<ReloadOutlined />}
                    loading={nodeFetching}
                    onClick={handleFetchNodeLog}
                  >
                    拉取节点日志
                  </Button>
                </Space>
                {nodeLog != null && (
                  <pre
                    style={{
                      marginTop: 16,
                      maxHeight: 480,
                      overflow: 'auto',
                      fontSize: 12,
                      background: COLORS.bgBase,
                      padding: 12,
                      borderRadius: 8,
                    }}
                  >
                    {nodeLog}
                  </pre>
                )}
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
}
