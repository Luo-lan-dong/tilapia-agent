import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { taskBuildingChatStream } from '@/api/taskBuilding';
import { server } from '@/test/setup';

describe('taskBuildingChatStream', () => {
  it('按顺序接收 meta/chunk/specification/status/done 事件', async () => {
    const events: Array<{ event: string; data: Record<string, unknown> }> = [];

    await taskBuildingChatStream({
      messages: [{ role: 'user', content: 'hello' }],
      mode: 'thinking',
      onEvent: (event) => events.push(event),
    });

    expect(events.map((e) => e.event)).toEqual([
      'meta',
      'thinking',
      'chunk',
      'chunk',
      'specification',
      'status',
      'done',
    ]);
    expect(events[1].data).toEqual({ delta: '我需要分析这个登录接口需求' });
    expect(events[0].data).toEqual({ model: 'deepseek-chat', mode: 'thinking' });
    expect(events[events.length - 2].data).toEqual({ is_ready_to_create: true });
  });

  it('请求失败时抛出错误', async () => {
    server.use(
      http.post('/api/v1/tasks/build/chat/stream', () => {
        return new HttpResponse('内部错误', { status: 500 });
      }),
    );

    await expect(
      taskBuildingChatStream({
        messages: [{ role: 'user', content: 'hello' }],
        onEvent: vi.fn(),
      }),
    ).rejects.toThrow();
  });
});
