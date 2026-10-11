/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：节点轨迹折叠块
   聚合块头状态、四类轨迹行渲染、跟随/暂停与「加载更早」窗口，状态同源于
   rows 与滚动位置，拆分会引入回调透传与重复滚动治理，故整体豁免。 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Collapse, Spin, Tag } from 'antd';
import { ApartmentOutlined, ArrowDownOutlined, EllipsisOutlined } from '@ant-design/icons';
import type { AgentTraceRow } from '@/types/task';
import { renderMarkdown } from '@/components/Markdown';
import './NodeTraceBlock.css';

/** 默认渲染最近行数（ADR-063 暂定口径：最近 200 行 + 顶部「加载更早」） */
const DEFAULT_VISIBLE_ROWS = 200;
/** 每次「加载更早」追加的行数 */
const LOAD_EARLIER_STEP = 200;

/** 展开态下的自动跟随窗口（距底不足该值视为在底部） */
const FOLLOW_BOTTOM_THRESHOLD_PX = 48;

function asText(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' ? value : null;
}

/** 单行渲染：status 系统行 / reasoning 思考 / text 正文 / tool 工具（只读） */
function TraceRowView({ row }: { row: AgentTraceRow }) {
  if (row.kind === 'status') {
    const stage = asText(row.payload.stage);
    const message = asText(row.payload.message);
    return (
      <div
        className="node-trace__row node-trace__row--status"
        data-testid={`node-trace-row-${row.seq}`}
      >
        {stage && <span className="node-trace__status-stage">{stage}</span>}
        <span>{message}</span>
      </div>
    );
  }
  if (row.kind === 'reasoning') {
    return (
      <div
        className="node-trace__row node-trace__row--reasoning"
        data-testid={`node-trace-row-${row.seq}`}
      >
        <span className="node-trace__row-tag">思考</span>
        <div className="node-trace__reasoning-text">{asText(row.payload.text)}</div>
      </div>
    );
  }
  if (row.kind === 'text') {
    return (
      <div
        className="node-trace__row node-trace__row--text"
        data-testid={`node-trace-row-${row.seq}`}
      >
        <div className="node-trace__text-body">{renderMarkdown(asText(row.payload.text))}</div>
      </div>
    );
  }
  if (row.kind === 'tool') {
    const phase = asText(row.payload.phase) || 'called';
    const target = asText(row.payload.target);
    const summary = asText(row.payload.summary);
    const exitCode = asNumber(row.payload.exit_code);
    return (
      <div
        className={`node-trace__row node-trace__row--tool node-trace__row--tool-${phase}`}
        data-testid={`node-trace-row-${row.seq}`}
      >
        <span className="node-trace__row-tag">工具</span>
        <code className="node-trace__tool-name">{asText(row.payload.tool)}</code>
        <span className={`node-trace__tool-phase node-trace__tool-phase--${phase}`}>
          {phase === 'called' ? '调用' : phase === 'success' ? '完成' : '失败'}
        </span>
        {target && <code className="node-trace__tool-detail">{target}</code>}
        {summary && <span className="node-trace__tool-summary">{summary}</span>}
        {exitCode !== null && (
          <span className="node-trace__tool-exit">退出码 {exitCode}</span>
        )}
      </div>
    );
  }
  // todo / usage / 未知类型：紧凑降级渲染（M2 采集暂不产出）
  return (
    <div className="node-trace__row node-trace__row--status" data-testid={`node-trace-row-${row.seq}`}>
      <span className="node-trace__row-tag">{row.kind}</span>
      <code className="node-trace__tool-detail">{JSON.stringify(row.payload)}</code>
    </div>
  );
}

export interface NodeTraceBlockProps {
  /** 执行节点 ID（client_id 形态，来源标注用） */
  nodeId: string | null;
  /** 节点显示名（任务 assigned_node_name；缺失时回退 nodeId） */
  nodeName?: string;
  /** 任务名（块头展示） */
  taskName?: string;
  /** 任务进度（0-100，来自任务详情；未派发时为 undefined） */
  progress?: number;
  /** 该节点的轨迹行（seq 升序，由父层按 node_id 分组过滤） */
  rows: AgentTraceRow[];
  loading?: boolean;
  onExpandChange?: (expanded: boolean) => void;
}

/**
 * 节点 Agent 会话轨迹折叠块（ADR-063 M3，群聊式、只读旁观）。
 *
 * 默认折叠；展开后按 seq 升序渲染轨迹行，自动跟随最新，用户上滚即暂停
 * 跟随并提示「有新输出」。块内不提供任何输入（D2 只读）。
 */
export function NodeTraceBlock({
  nodeId,
  nodeName,
  taskName,
  progress,
  rows,
  loading = false,
  onExpandChange,
}: NodeTraceBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const [visibleCount, setVisibleCount] = useState(DEFAULT_VISIBLE_ROWS);
  const [following, setFollowing] = useState(true);
  const [pausedAtCount, setPausedAtCount] = useState(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const latestStatus = useMemo(
    () => [...rows].reverse().find((row) => row.kind === 'status'),
    [rows],
  );
  const title = nodeName || (nodeId ? nodeId.slice(0, 12) : '节点');

  const visibleRows = useMemo(
    () => (rows.length > visibleCount ? rows.slice(rows.length - visibleCount) : rows),
    [rows, visibleCount],
  );
  const hiddenCount = rows.length - visibleRows.length;

  useEffect(() => {
    if (!following || !expanded) {
      return;
    }
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [following, expanded, rows.length]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) {
      return;
    }
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_BOTTOM_THRESHOLD_PX;
    if (atBottom) {
      setFollowing(true);
      setPausedAtCount(0);
    } else if (following) {
      setFollowing(false);
      setPausedAtCount(rows.length);
    }
  };

  const hasNewWhilePaused = !following && rows.length > pausedAtCount;

  return (
    <div className="node-trace" data-testid="node-trace-block">
      <Collapse
        size="small"
        activeKey={expanded ? ['trace'] : []}
        onChange={(keys) => {
          const next = Array.isArray(keys) ? keys.includes('trace') : keys === 'trace';
          setExpanded(next);
          if (next) {
            setFollowing(true);
            setPausedAtCount(0);
          }
          onExpandChange?.(next);
        }}
        items={[
          {
            key: 'trace',
            label: (
              <span className="node-trace__header" data-testid="node-trace-header">
                <ApartmentOutlined className="node-trace__header-icon" />
                <Tag className="node-trace__source-tag" data-testid="node-trace-source">节点</Tag>
                <span className="node-trace__title">{title}</span>
                {taskName && <span className="node-trace__task">{taskName}</span>}
                <span className="node-trace__action">
                  {latestStatus ? asText(latestStatus.payload.message) : '等待节点上报'}
                </span>
                {typeof progress === 'number' && (
                  <span className="node-trace__progress">{progress}%</span>
                )}
                <span className="node-trace__count">{rows.length} 行</span>
              </span>
            ),
            children: (
              <div className="node-trace__body">
                {loading && rows.length === 0 && (
                  <div className="node-trace__loading">
                    <Spin size="small" /> <span>正在加载节点轨迹…</span>
                  </div>
                )}
                {hiddenCount > 0 && (
                  <button
                    type="button"
                    className="node-trace__load-earlier"
                    data-testid="node-trace-load-earlier"
                    onClick={() => setVisibleCount((count) => count + LOAD_EARLIER_STEP)}
                  >
                    <EllipsisOutlined /> 加载更早（还有 {hiddenCount} 行）
                  </button>
                )}
                <div className="node-trace__rows" ref={scrollRef} onScroll={handleScroll}>
                  {visibleRows.map((row) => (
                    <TraceRowView key={row.seq} row={row} />
                  ))}
                </div>
                {hasNewWhilePaused && (
                  <button
                    type="button"
                    className="node-trace__new-pill"
                    data-testid="node-trace-new-pill"
                    onClick={() => {
                      setFollowing(true);
                      setPausedAtCount(0);
                    }}
                  >
                    <ArrowDownOutlined /> 有新输出
                  </button>
                )}
              </div>
            ),
          },
        ]}
        className="node-trace__collapse"
      />
    </div>
  );
}
