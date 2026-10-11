import { useEffect, useRef } from 'react';
import { fetchTaskBuildingSessionById } from '@/api/taskBuildingSessions';
import type { ChatMessage } from '@/types/taskBuilding';
import { messageReplyTo } from '@/utils/chatMessage';

/** 轮询间隔（毫秒，ADR-064 M3：复用会话详情接口的轻量轮询，不引入事件总线） */
const POLL_INTERVAL_MS = 2000;
/** 单个回复目标的最长等待（毫秒）：节点侧续聊超时 300s + 网络落库余量 */
const REPLY_TIMEOUT_MS = 320_000;

/** 待回复目标：node-chat 202 返回的 reply_to + 发起时刻（用于单目标超时） */
export interface PendingNodeReply {
  replyTo: string;
  startedAt: number;
}

interface NodeChatReplyPollParams {
  /** 目标构建会话 ID（任务关联会话；缺失时无法轮询，静默不启动） */
  sessionId?: string;
  /** 待回复目标列表（发送 202 后追加，收到回复/超时后由页面移除） */
  pending: PendingNodeReply[];
  /** 见到回复回调：replyTo 对应的 assistant 消息（每个目标至多回调一次） */
  onReply: (replyTo: string, message: ChatMessage) => void;
  /** 单目标超时回调（320s 未回，页面据此移除目标） */
  onExpire: (replyTo: string) => void;
}

/**
 * 节点续聊回复轮询（ADR-064 M3 实时性）。
 *
 * node-chat 202 后中枢把用户消息与节点回复（assistant+reply_to）落库到
 * 任务关联构建会话；本 Hook 复用会话详情接口每 2s 轻量拉取，见到目标
 * reply_to 的 assistant 消息即回调 onReply，320s 未回回调 onExpire。
 * 拉取失败静默重试（旁路原则，不干扰会话主链路）；已回调/已超时的目标
 * 即使仍留在 pending 中也不会重复触发。
 */
export function useNodeChatReplyPoll({
  sessionId,
  pending,
  onReply,
  onExpire,
}: NodeChatReplyPollParams): void {
  const onReplyRef = useRef(onReply);
  const onExpireRef = useRef(onExpire);
  onReplyRef.current = onReply;
  onExpireRef.current = onExpire;
  // 已触发去重集合：pending 更新滞后于回调时防止重复 onReply/onExpire
  const notifiedRef = useRef(new Set<string>());

  useEffect(() => {
    notifiedRef.current = new Set();
    if (!sessionId || pending.length === 0) {
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const session = await fetchTaskBuildingSessionById(sessionId);
        if (cancelled) {
          return;
        }
        for (const target of pending) {
          if (notifiedRef.current.has(target.replyTo)) {
            continue;
          }
          const reply = session.messages.find(
            (msg) => msg.role === 'assistant' && messageReplyTo(msg) === target.replyTo,
          );
          if (reply) {
            notifiedRef.current.add(target.replyTo);
            onReplyRef.current(target.replyTo, reply);
          } else if (Date.now() - target.startedAt >= REPLY_TIMEOUT_MS) {
            notifiedRef.current.add(target.replyTo);
            onExpireRef.current(target.replyTo);
          }
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
  }, [sessionId, pending]);
}
