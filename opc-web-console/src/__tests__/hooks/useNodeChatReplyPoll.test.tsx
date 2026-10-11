import { act } from 'react';
import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useNodeChatReplyPoll } from '@/hooks/useNodeChatReplyPoll';
import type { PendingNodeReply } from '@/hooks/useNodeChatReplyPoll';
import type { ChatMessage, TaskBuildingSession } from '@/types/taskBuilding';
import { fetchTaskBuildingSessionById } from '@/api/taskBuildingSessions';

vi.mock('@/api/taskBuildingSessions', () => ({
  fetchTaskBuildingSessionById: vi.fn(),
}));

const mockedFetch = vi.mocked(fetchTaskBuildingSessionById);

/** 构造会话详情响应（轮询目标数据源） */
function makeSession(messages: ChatMessage[]): TaskBuildingSession {
  return {
    id: 'session-1',
    title: '续聊会话',
    messages,
    specification: null,
    mode: 'thinking',
    model: null,
    task_id: 'task-1',
    created_at: '2026-10-05T00:00:00Z',
    updated_at: '2026-10-05T00:00:00Z',
  };
}

describe('useNodeChatReplyPoll（ADR-064 M3 节点续聊回复轮询）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedFetch.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('见到 reply_to 对应 assistant 消息即回调，且同一目标不重复回调', async () => {
    const startedAt = Date.now();
    const onReply = vi.fn();
    const onExpire = vi.fn();
    const pending: PendingNodeReply[] = [{ replyTo: 'reply-1', startedAt }];

    mockedFetch
      .mockResolvedValueOnce(makeSession([]))
      .mockResolvedValue(
        makeSession([
          {
            role: 'assistant',
            content: '已改为绿色，效果符合预期。',
            reply_to: 'reply-1',
          } as ChatMessage,
        ]),
      );

    const { rerender } = renderHook(
      (props: { pending: PendingNodeReply[] }) => useNodeChatReplyPoll({
        sessionId: 'session-1',
        pending: props.pending,
        onReply,
        onExpire,
      }),
      { initialProps: { pending } },
    );

    // 首个 tick（立即执行）无回复
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(onReply).not.toHaveBeenCalled();

    // 第二个 tick（2s 后）见到回复 → 回调一次
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(onReply).toHaveBeenCalledTimes(1);
    expect(onReply).toHaveBeenCalledWith('reply-1', expect.objectContaining({ role: 'assistant' }));

    // pending 更新滞后（不移除目标）：继续轮询也不重复回调
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(onReply).toHaveBeenCalledTimes(1);
    expect(onExpire).not.toHaveBeenCalled();

    // 消费方移除目标（正常流程）后停止轮询
    rerender({ pending: [] });
    const callsAfterStop = mockedFetch.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(mockedFetch.mock.calls.length).toBe(callsAfterStop);
  });

  it('超过 320s 未回回调 onExpire（单目标一次），轮询停止', async () => {
    const onReply = vi.fn();
    const onExpire = vi.fn();
    mockedFetch.mockResolvedValue(makeSession([]));

    renderHook(() =>
      useNodeChatReplyPoll({
        sessionId: 'session-1',
        pending: [{ replyTo: 'reply-1', startedAt: Date.now() }],
        onReply,
        onExpire,
      }),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(320_500);
    });

    expect(onExpire).toHaveBeenCalledTimes(1);
    expect(onExpire).toHaveBeenCalledWith('reply-1');
    expect(onReply).not.toHaveBeenCalled();
  });

  it('会话 ID 缺失时静默不轮询', async () => {
    const onReply = vi.fn();
    renderHook(() =>
      useNodeChatReplyPoll({
        sessionId: undefined,
        pending: [{ replyTo: 'reply-1', startedAt: Date.now() }],
        onReply,
        onExpire: vi.fn(),
      }),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(mockedFetch).not.toHaveBeenCalled();
    expect(onReply).not.toHaveBeenCalled();
  });

  it('单次拉取失败静默重试，下个周期继续（旁路原则）', async () => {
    const onReply = vi.fn();
    mockedFetch
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValue(
        makeSession([
          { role: 'assistant', content: '节点回复', reply_to: 'reply-1' } as ChatMessage,
        ]),
      );

    renderHook(() =>
      useNodeChatReplyPoll({
        sessionId: 'session-1',
        pending: [{ replyTo: 'reply-1', startedAt: Date.now() }],
        onReply,
        onExpire: vi.fn(),
      }),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(onReply).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(mockedFetch).toHaveBeenCalledTimes(2);
    expect(onReply).toHaveBeenCalledTimes(1);
  });
});
