import { useEffect, useRef, useState } from 'react';
import { fetchTaskAgentTrace } from '@/api/tasks';
import type { AgentTraceRow } from '@/types/task';

/** 历史回看单页条数（后端上限 1000） */
const HISTORY_PAGE_LIMIT = 1000;
/** 历史回看最大页数（超过置 historyTruncated，防长任务首屏拉取过久） */
const HISTORY_MAX_PAGES = 5;
/** 展开态实时轮询间隔（ADR-063 M1b：按 after_seq 增量拉取，不引入事件总线） */
const POLL_INTERVAL_MS = 1000;
/** 空结果低频重拉间隔（2026-10-06 修⑤死锁：任务执行中首轮回看为空时） */
const EMPTY_RETRY_INTERVAL_MS = 3000;

export interface TaskAgentTraceState {
  /** 全量轨迹行（seq 升序，含所有节点；分组渲染由调用方完成） */
  rows: AgentTraceRow[];
  /** 首次回看加载中 */
  loadingHistory: boolean;
  /** 最近一次拉取失败原因（旁路展示，不影响会话主链路） */
  error: string | null;
  /** 数据库中的历史行数超过单次回看上限（更早的行未拉取） */
  historyTruncated: boolean;
}

/**
 * 节点 Agent 会话轨迹拉取 Hook（ADR-063 M3）。
 *
 * - taskId 变化时做一次回看加载（按 after_seq 翻页至尽，页数有上限）；
 * - ``pollActive`` 为 true 时每 ~1s 增量拉取新行（折叠块展开才传 true，
 *   收起即停——与 ADR-063「展开时拉取、收起即停」一致）；
 * - ``waitActive`` 为 true（任务未终态）且首轮回看为空时，每 3s 低频重拉
 *   直到拿到首行——否则折叠块无行不渲染、无法展开激活轮询，轨迹永远
 *   出不来（2026-10-06 修⑤死锁）；拿到行后即恢复「展开才高频轮询」常态；
 * - 任何失败只记入 error 并继续重试，绝不向会话主链路抛错。
 */
export function useTaskAgentTrace(
  taskId: string | undefined,
  pollActive: boolean,
  waitActive = false,
): TaskAgentTraceState {
  const [rows, setRows] = useState<AgentTraceRow[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyTruncated, setHistoryTruncated] = useState(false);
  const lastSeqRef = useRef(0);

  const appendRows = (incoming: AgentTraceRow[]) => {
    if (incoming.length === 0) {
      return;
    }
    setRows((prev) => {
      const seen = new Set(prev.map((row) => row.seq));
      const fresh = incoming.filter((row) => !seen.has(row.seq));
      if (fresh.length === 0) {
        return prev;
      }
      return [...prev, ...fresh].sort((a, b) => a.seq - b.seq);
    });
  };

  useEffect(() => {
    lastSeqRef.current = rows.length > 0 ? rows[rows.length - 1].seq : 0;
  }, [rows]);

  // 回看加载：taskId 变化时按 after_seq 翻页至 has_more 为止（有页数上限）
  useEffect(() => {
    if (!taskId) {
      setRows([]);
      setLoadingHistory(false);
      setError(null);
      setHistoryTruncated(false);
      return;
    }
    let cancelled = false;
    setRows([]);
    setLoadingHistory(true);
    setError(null);
    setHistoryTruncated(false);
    void (async () => {
      let cursor = 0;
      let moreAfterLoop = false;
      for (let page = 0; page < HISTORY_MAX_PAGES; page += 1) {
        try {
          const data = await fetchTaskAgentTrace(taskId, {
            afterSeq: cursor,
            limit: HISTORY_PAGE_LIMIT,
          });
          if (cancelled) {
            return;
          }
          appendRows(data.rows);
          if (!data.has_more) {
            moreAfterLoop = false;
            break;
          }
          if (data.rows.length === 0) {
            break;
          }
          cursor = data.rows[data.rows.length - 1].seq;
          moreAfterLoop = true;
        } catch (err) {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : '轨迹拉取失败');
          }
          moreAfterLoop = false;
          break;
        }
      }
      if (!cancelled) {
        setHistoryTruncated(moreAfterLoop);
        setLoadingHistory(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  // 实时增量：展开时每 ~1s 拉 after_seq 之后的新行；收起即停
  useEffect(() => {
    if (!taskId || !pollActive) {
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchTaskAgentTrace(taskId, {
          afterSeq: lastSeqRef.current,
          limit: HISTORY_PAGE_LIMIT,
        });
        if (cancelled) {
          return;
        }
        appendRows(data.rows);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '轨迹拉取失败');
        }
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [taskId, pollActive]);

  // 空结果低频重拉（2026-10-06 修⑤死锁）：任务刚派发时首轮回看必然为空，
  // 折叠块无行不渲染 → 无法展开 → pollActive 永不激活 → 轨迹永不出现。
  // 任务未终态（waitActive）时每 3s 重拉直到拿到首行；拿到行后 rows.length>0
  // 使本 effect 失活，恢复「展开才高频轮询」常态；任务终态即停，无无限轮询。
  useEffect(() => {
    if (!taskId || !waitActive || rows.length > 0) {
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await fetchTaskAgentTrace(taskId, {
          afterSeq: lastSeqRef.current,
          limit: HISTORY_PAGE_LIMIT,
        });
        if (cancelled) {
          return;
        }
        appendRows(data.rows);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '轨迹拉取失败');
        }
      }
    };
    const timer = window.setInterval(() => void tick(), EMPTY_RETRY_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [taskId, waitActive, rows.length]);

  return { rows, loadingHistory, error, historyTruncated };
}
