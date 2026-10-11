import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TaskNewChatPanel } from '@/components/TaskNewChatPanel';
import type { ChatMessage } from '@/types/taskBuilding';

/**
 * 聊天面板渲染过滤单测：工具轨迹消息（ADR-040）不在对话流中直接渲染。
 *
 * 背景 bug：会话恢复后 assistant(tool_calls) 空载体消息渲染为空气泡、
 * role:"tool" 结果消息渲染为一段 AI 正文，用户看到「莫名其妙的空泡与多余正文」。
 */
function renderPanel(messages: ChatMessage[]) {
  return render(
    <TaskNewChatPanel
      messages={messages}
      input=""
      sending={false}
      mode="thinking"
      onInputChange={vi.fn()}
      onSend={vi.fn()}
      onOpenSessions={vi.fn()}
      footer={null}
    />,
  );
}

describe('TaskNewChatPanel 工具轨迹渲染过滤', () => {
  it('不渲染 role=tool 消息与空 tool_calls 载体消息', () => {
    renderPanel([
      { role: 'user', content: '查一下空闲节点' },
      {
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'call-1', function: { name: 'find_idle_nodes', arguments: '{}' } }],
      },
      { role: 'tool', tool_call_id: 'call-1', content: '当前共 2 个可用节点：code、笔记本' },
      {
        role: 'assistant',
        content: '当前有 2 个空闲的 code 类型节点。',
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

    // tool 结果不作为独立 AI 正文出现
    expect(screen.queryByText(/当前共 2 个可用节点/)).not.toBeInTheDocument();
    // 正常的最终回复与思考卡片（折叠标签）保留
    expect(screen.getByText('当前有 2 个空闲的 code 类型节点。')).toBeInTheDocument();
    expect(screen.getByText('思考过程')).toBeInTheDocument();
    // 仅最终回复一条 AI 消息（空载体被过滤，无空气泡）
    expect(screen.getAllByText('AI')).toHaveLength(1);
    // 用户消息保留
    expect(screen.getByText('查一下空闲节点')).toBeInTheDocument();
  });

  it('流式占位的空 assistant 消息（无 content/thinking/steps）不渲染空气泡', () => {
    renderPanel([{ role: 'assistant', content: '' }]);
    expect(screen.queryAllByText('AI')).toHaveLength(0);
  });

  it('有思考/步骤但正文为空的回合不渲染空正文气泡（修复工具卡下方白色空条）', () => {
    const { container } = renderPanel([
      { role: 'user', content: '当前经验记忆有多少条经验未总结?' },
      {
        role: 'assistant',
        content: '',
        thinking: '调用 experience_recall 查询会话待总结条目。',
        steps: [
          {
            step_id: 'step-1',
            seq: 1,
            skill_name: 'experience_recall',
            skill_display_name: '查询经验记忆',
            status: 'failed',
          },
        ],
      },
    ]);
    // 思考卡（折叠标签）与步骤卡保留
    expect(screen.getByText('思考过程')).toBeInTheDocument();
    expect(screen.getByText('查询经验记忆')).toBeInTheDocument();
    // 空正文不再渲染 AI 气泡（白色空条根因）；用户消息气泡正常保留
    expect(
      container.querySelectorAll('.task-new__message--assistant .task-new__message-content'),
    ).toHaveLength(0);
    expect(
      container.querySelectorAll('.task-new__message--user .task-new__message-content'),
    ).toHaveLength(1);
  });
});
