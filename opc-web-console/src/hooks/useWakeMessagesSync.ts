import { useEffect, useRef } from 'react';
import { fetchTaskBuildingSessionById } from '@/api/taskBuildingSessions';
import type { ChatMessage } from '@/types/taskBuilding';

/** 轮询间隔（毫秒）：唤醒轮为后台编排（数秒~数十秒），3s 足够「准实时」 */
const POLL_INTERVAL_MS = 3000;

interface UseWakeMessagesSyncParams {
  /** 任务关联构建会话 ID（缺失时不轮询） */
  sessionId?: string;
  /**
   * 是否启用同步：任务终态（completed/failed）且本地无活跃对话流时开启——
   * 唤醒轮发生在任务终态之后；正常对话由本地流实时渲染，无需本 Hook。
   */
  enabled: boolean;
  /** 本地对话消息（用于判重：已展示过的唤醒消息不重复追加） */
  localMessages: ChatMessage[];
  /** 同步回调：把库中新增的唤醒消息（system_wake user + 其后 assistant）按序追加 */
  onAppend: (messages: ChatMessage[]) => void;
}

/**
 * 唤醒轮消息实时同步（ADR-066 唤醒可视化，2026-10-07 用户裁决）。
 *
 * 任务终态自动唤醒轮在**中枢后台**执行（不经前端 SSE 流），触发消息
 * （`source=system_wake` 的 user 消息）与编排 AI 回复（含 thinking/steps
 * 步骤卡）直接落会话库。本 Hook 复用会话详情接口每 3s 轻量拉取，发现
 * 本地尚未展示的唤醒消息即按序回调 onAppend——对话区像正常对话一样
 * 呈现中枢被唤醒后继续工作的过程。
 *
 * 判重策略：以本地是否已有**相同 content 的 system_wake user 消息**为准
 * （唤醒触发消息内容全局唯一）；其后的 assistant 回复跟随该触发消息一并
 * 追加，不单独判重。拉取失败静默重试（旁路原则，不干扰会话主链路）。
 */
export function useWakeMessagesSync({
  sessionId,
  enabled,
  localMessages,
  onAppend,
}: UseWakeMessagesSyncParams): void {
  const onAppendRef = useRef(onAppend);
  onAppendRef.current = onAppend;
  // 最新本地消息（ref 形式，避免 effect 因 messages 变化反复重启动）
  const localMessagesRef = useRef(localMessages);
  localMessagesRef.current = localMessages;

  useEffect(() => {
    if (!sessionId || !enabled) {
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const session = await fetchTaskBuildingSessionById(sessionId);
        if (cancelled) {
          return;
        }
        const remote = session.messages ?? [];
        // 库中的唤醒触发消息（system_wake user）索引
        const wakeIdx = remote
          .map((msg, index) => ({ msg, index }))
          .filter(({ msg }) => msg.role === 'user' && msg.source === 'system_wake');
        if (wakeIdx.length === 0) {
          return;
        }
        const local = localMessagesRef.current;
        // 逐个唤醒回合收集本地缺失的消息：assistant 回复晚于触发消息落库
        // （唤醒轮结束才整条写库），故每轮按 role+content 与本地比对增量追加
        const appended: ChatMessage[] = [];
        wakeIdx.forEach(({ index }, i) => {
          const end = i + 1 < wakeIdx.length ? wakeIdx[i + 1].index : remote.length;
          for (const msg of remote.slice(index, end)) {
            const exists = local.some((m) => m.role === msg.role && m.content === msg.content);
            if (!exists) {
              appended.push(msg);
            }
          }
        });
        if (appended.length > 0) {
          onAppendRef.current(appended);
        }
      } catch {
        // 拉取失败：本轮跳过，下个周期重试（旁路原则）
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sessionId, enabled]);
}
