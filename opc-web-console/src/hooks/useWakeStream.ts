import { useEffect, useRef } from 'react';
import { subscribeWakeStream } from '@/api/taskBuilding';
import type { TaskBuildStreamEvent } from '@/types/taskBuilding';

/** 唤醒流订阅结束后的重连等待（毫秒）：任务终态窗口内新唤醒可能随时开始 */
const RESUBSCRIBE_DELAY_MS = 4000;

interface UseWakeStreamParams {
  /** 任务关联构建会话 ID（缺失时不订阅） */
  sessionId?: string;
  /**
   * 是否订阅：任务终态（completed/failed）且本地无活跃对话流时开启——
   * 唤醒轮发生在任务终态之后；正常对话由本地流实时渲染。
   */
  enabled: boolean;
  /** 事件回调：直接复用 applyChatStreamEvent 渲染到对话区 */
  onEvent: (event: TaskBuildStreamEvent) => void;
}

/**
 * 唤醒轮事件流实时订阅（ADR-066 唤醒可视化，2026-10-07 用户裁决）。
 *
 * 中枢唤醒轮在后台执行时把编排事件（thinking/chunk/step/done）按会话
 * 广播（`GET /sessions/{id}/wake-stream`），本 Hook 订阅并回调，由页面把
 * 事件喂给 `applyChatStreamEvent`——对话区像正常对话一样逐字实时展示
 * 中枢被唤醒后继续工作的过程。
 *
 * 生命周期：`done` 或连接错误即结束，延迟后重新订阅（捕捉下一次唤醒）；
 * 服务端无活跃唤醒轮时立即返回 done，本 Hook 依此节律轮转。事件渲染与
 * 落库判重由 useWakeMessagesSync 兜底补齐（两者按 content 判重不冲突）。
 */
export function useWakeStream({ sessionId, enabled, onEvent }: UseWakeStreamParams): void {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!sessionId || !enabled) {
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    const controller = new AbortController();

    const loop = async () => {
      while (!cancelled) {
        try {
          await subscribeWakeStream(sessionId, (event) => {
            if (!cancelled) {
              onEventRef.current(event);
            }
          }, controller.signal);
        } catch {
          // 订阅异常静默（旁路原则）
        }
        if (cancelled) {
          return;
        }
        // 本次订阅结束（done/断开）：延迟后重订，等待可能的下一次唤醒
        await new Promise((resolve) => {
          timer = window.setTimeout(resolve, RESUBSCRIBE_DELAY_MS);
        });
      }
    };

    void loop();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
    };
  }, [sessionId, enabled]);
}
