import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { TaskNewChatPanel } from '@/components/TaskNewChatPanel';
import type { ChatMessage } from '@/types/taskBuilding';

function renderPanel(overrides: Record<string, unknown> = {}) {
  return render(
    <TaskNewChatPanel
      messages={[]}
      input=""
      sending={false}
      mode="fast"
      onInputChange={() => {}}
      onSend={() => {}}
      onOpenSessions={() => {}}
      footer={<div />}
      {...overrides}
    />,
  );
}

describe('TaskNewChatPanel `@节点`（ADR-063 M4/D6）', () => {
  it('任务已派发节点时展示 @ 按钮，选择后输入区出现 chip 且可移除', async () => {
    const user = userEvent.setup();
    const picked: unknown[] = [];
    const { rerender } = renderPanel({
      mentionCandidates: [{ id: 'node-1', name: '编程节点' }],
      onMentionPick: (node: unknown) => picked.push(node),
    });

    await user.click(screen.getByTestId('mention-node-button'));
    await user.click(screen.getByText('@编程节点'));
    expect(picked).toEqual([{ id: 'node-1', name: '编程节点' }]);

    rerender(
      <TaskNewChatPanel
        messages={[]}
        input=""
        sending={false}
        mode="fast"
        onInputChange={() => {}}
        onSend={() => {}}
        onOpenSessions={() => {}}
        footer={<div />}
        mentionCandidates={[{ id: 'node-1', name: '编程节点' }]}
        mentionNode={{ id: 'node-1', name: '编程节点' }}
        onMentionClear={() => {}}
      />,
    );
    expect(screen.getByTestId('mention-node-chip')).toHaveTextContent('发给节点：编程节点');
  });

  it('任务未派发节点时隐藏 @ 按钮', () => {
    renderPanel({ mentionCandidates: [] });
    expect(screen.queryByTestId('mention-node-button')).not.toBeInTheDocument();
  });

  it('user 消息携带 mentionNode 时显示 @ 来源标注（D6 来源区分）', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: '请先跑测试', mentionNode: '编程节点' },
    ];
    renderPanel({ messages });
    expect(screen.getByTestId('mention-tag')).toHaveTextContent('@编程节点');
  });

  it('assistant 消息携带 mentionNode 时渲染节点回复徽标（ADR-064 M3 历史恢复）', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: '把登录按钮改成绿色试试', mentionNode: '编程节点' },
      { role: 'assistant', content: '已改为绿色，效果符合预期。', mentionNode: '编程节点' },
    ];
    renderPanel({ messages });
    // 仅 assistant 消息渲染节点徽标；user 消息仍是 mention-tag 标注
    const badges = screen.getAllByTestId('node-reply-badge');
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent('@编程节点');
    expect(screen.getByTestId('mention-tag')).toHaveTextContent('@编程节点');
    expect(screen.getByText('已改为绿色，效果符合预期。')).toBeInTheDocument();
  });

  it('assistant 消息携带后端直传 mention_node 时同样渲染节点徽标（snake_case 兼容）', () => {
    const messages: ChatMessage[] = [
      {
        role: 'assistant',
        content: '节点侧回复正文',
        // 模拟后端落库直传形态（ADR-064 消息契约）
        mention_node: '编程节点',
      } as ChatMessage,
    ];
    renderPanel({ messages });
    expect(screen.getByTestId('node-reply-badge')).toHaveTextContent('@编程节点');
  });

  it('user 消息标记 failed 时展示「发送失败」（ADR-064 M3 错误可见）', () => {
    const messages: ChatMessage[] = [
      { role: 'user', content: '把登录按钮改成绿色试试', mentionNode: '编程节点', failed: true },
    ];
    renderPanel({ messages });
    expect(screen.getByTestId('message-failed-tag')).toHaveTextContent('发送失败');
    expect(screen.getByText('把登录按钮改成绿色试试')).toBeInTheDocument();
  });
});
