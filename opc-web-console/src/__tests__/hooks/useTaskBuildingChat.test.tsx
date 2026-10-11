import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { App as AntApp } from 'antd';
import { useTaskBuildingChat } from '@/hooks/useTaskBuildingChat';
import { taskBuildingChatStream } from '@/api/taskBuilding';

vi.mock('@/api/taskBuilding', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/taskBuilding')>();
  return {
    ...actual,
    taskBuildingChatStream: vi.fn().mockResolvedValue(undefined),
  };
});

const mockedStream = vi.mocked(taskBuildingChatStream);

const wrapper = ({ children }: { children: React.ReactNode }) => <AntApp>{children}</AntApp>;

/**
 * 任务构建对话 Hook 单测：历史消息回传保真（会话恢复不丢展示数据）。
 *
 * 背景 bug：sendMessage 回传历史时丢弃 thinking/steps，导致后端持久化的
 * 会话缺思考内容，切换页面恢复后第一条对话的「思考内容」消失。
 */
describe('useTaskBuildingChat sendMessage 历史回传', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('回传历史 assistant 消息时保留 thinking 与 steps（ADR-033）', async () => {
    const { result } = renderHook(() => useTaskBuildingChat(), { wrapper });

    act(() => {
      result.current[1].reset([
        {
          role: 'assistant',
          content: '今天是 2026 年 9 月 22 日。',
          thinking: 'User asks date/time.',
        },
        {
          role: 'assistant',
          content: '当前有空闲节点。',
          thinking: 'User asks to check idle nodes.',
          steps: [
            {
              step_id: 'step-1',
              seq: 1,
              skill_name: 'find_idle_nodes',
              skill_display_name: '查找空闲节点',
              status: 'success',
            },
          ],
        },
      ]);
    });

    await act(async () => {
      await result.current[1].sendMessage('下一句');
    });

    expect(mockedStream).toHaveBeenCalledTimes(1);
    const options = mockedStream.mock.calls[0][0];
    // user 消息之前的两条历史 assistant 均须携带 thinking / steps
    expect(options.messages).toHaveLength(3);
    expect(options.messages[0]).toMatchObject({
      role: 'assistant',
      thinking: 'User asks date/time.',
    });
    expect(options.messages[1]).toMatchObject({
      role: 'assistant',
      thinking: 'User asks to check idle nodes.',
    });
    expect(options.messages[1]).toHaveProperty('steps');
    expect(options.messages[2]).toMatchObject({ role: 'user', content: '下一句' });
  });

  it('回传工具轨迹消息（tool_calls 载体与 role=tool）供下一轮流回 LLM（ADR-040）', async () => {
    const { result } = renderHook(() => useTaskBuildingChat(), { wrapper });

    act(() => {
      result.current[1].reset([
        {
          role: 'assistant',
          content: '',
          tool_calls: [
            { id: 'call-1', function: { name: 'find_idle_nodes', arguments: '{}' } },
          ],
        },
        { role: 'tool', tool_call_id: 'call-1', content: '工具结果摘要' },
      ]);
    });

    await act(async () => {
      await result.current[1].sendMessage('继续');
    });

    const options = mockedStream.mock.calls[0][0];
    expect(options.messages[0]).toMatchObject({
      role: 'assistant',
      content: '',
      tool_calls: [{ id: 'call-1' }],
    });
    expect(options.messages[1]).toMatchObject({ role: 'tool', tool_call_id: 'call-1' });
  });
});

/**
 * 回合分割单测（docs/issues/2026-09-26 排查记录问题一）：
 * 工具轮不产生正文（content 恒空），分割条件只看 last.content 时
 * 多轮 thinking/steps 全部堆进同一条消息。修复后按
 * 「思考块 N → 该轮工具卡片们 → 思考块 N+1 → …」逐回合层叠。
 */
describe('useTaskBuildingChat 回合分割', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /** 驱动一段 SSE 事件序列（通过 mock 捕获 onEvent 后手动触发） */
  function mockStreamWithEvents(
    events: Array<[TaskBuildEventName, Record<string, unknown>]>,
  ) {
    mockedStream.mockImplementation(async (options) => {
      const { onEvent } = options;
      if (!onEvent) {
        throw new Error('onEvent callback missing');
      }
      for (const [name, data] of events) {
        onEvent({ event: name, data });
      }
    });
  }

  type TaskBuildEventName = Parameters<
    NonNullable<Parameters<typeof taskBuildingChatStream>[0]['onEvent']>
  >[0]['event'];

  it('工具轮结束后新一轮 thinking 新建消息，多轮不再堆叠', async () => {
    mockStreamWithEvents([
      // 轮 1：thinking + 两个并行工具
      ['thinking', { delta: '第一轮思考' }],
      ['step_started', { step_id: 's1', seq: 1, skill_name: 'bt_mcp_servers' }],
      ['step_started', { step_id: 's2', seq: 2, skill_name: 'bt_mcp_tools' }],
      ['step_finished', { step_id: 's1', status: 'success' }],
      ['step_finished', { step_id: 's2', status: 'success' }],
      // 轮 2：新一轮思考（工具轮 content 为空，旧逻辑会合并进轮 1）
      ['thinking', { delta: '第二轮思考' }],
      ['chunk', { delta: '最终回复' }],
      ['done', {}],
    ]);

    const { result } = renderHook(() => useTaskBuildingChat(), { wrapper });
    await act(async () => {
      await result.current[1].sendMessage('部署');
    });

    const { messages } = result.current[0];
    // user + 轮1(thinking+steps) + 轮2(thinking+正文)
    expect(messages).toHaveLength(3);
    expect(messages[1]).toMatchObject({
      role: 'assistant',
      content: '',
      thinking: '第一轮思考',
    });
    expect(messages[1].steps).toHaveLength(2);
    expect(messages[2]).toMatchObject({
      role: 'assistant',
      thinking: '第二轮思考',
      content: '最终回复',
    });
    expect(messages[2].steps).toBeUndefined();
  });

  it('同轮并行多工具的 step_started 追加同一条消息（成组不分割）', async () => {
    mockStreamWithEvents([
      ['thinking', { delta: '思考' }],
      ['step_started', { step_id: 's1', seq: 1, skill_name: 'a' }],
      ['step_started', { step_id: 's2', seq: 2, skill_name: 'b' }],
      ['done', {}],
    ]);

    const { result } = renderHook(() => useTaskBuildingChat(), { wrapper });
    await act(async () => {
      await result.current[1].sendMessage('查节点');
    });

    const { messages } = result.current[0];
    expect(messages).toHaveLength(2); // user + 单条 assistant
    expect(messages[1].steps).toHaveLength(2);
    expect(messages[1].steps?.map((s) => s.step_id)).toEqual(['s1', 's2']);
  });

  it('上一轮工具全部结束后，新轮 step_started（无 thinking）同样新建消息', async () => {
    mockStreamWithEvents([
      ['step_started', { step_id: 's1', seq: 1, skill_name: 'a' }],
      ['step_finished', { step_id: 's1', status: 'success' }],
      ['step_started', { step_id: 's2', seq: 2, skill_name: 'b' }],
      ['done', {}],
    ]);

    const { result } = renderHook(() => useTaskBuildingChat(), { wrapper });
    await act(async () => {
      await result.current[1].sendMessage('继续');
    });

    const { messages } = result.current[0];
    expect(messages).toHaveLength(3); // user + 轮1(s1) + 轮2(s2)
    expect(messages[1].steps?.map((s) => s.step_id)).toEqual(['s1']);
    expect(messages[2].steps?.map((s) => s.step_id)).toEqual(['s2']);
  });
});
