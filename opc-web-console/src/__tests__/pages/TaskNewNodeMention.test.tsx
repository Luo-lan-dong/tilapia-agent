import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { App as AntApp } from 'antd';
import { beforeEach, describe, expect, it } from 'vitest';
import { TaskNew } from '@/pages/TaskNew';
import { server } from '@/test/setup';

/**
 * `@节点` 空闲续聊分流（ADR-064 M3）页面级测试。
 *
 * 分流逻辑在 TaskNew 页面 handleSend 内，组件级用例无法覆盖，
 * 故经 MSW 覆盖任务详情 / node-chat / steer / 会话详情接口断言：
 * 空闲 @ → node-chat（202 续聊）、running @ → steer、失败 → 报错可见。
 */

/** 任务详情基线：已完成（空闲）、已派发到 node-1（编程节点）、关联会话 session-1 */
const BASE_TASK = {
  id: 'task-1',
  title: '登录页开发',
  agent_type: 'programming',
  status: 'completed',
  progress: 100,
  source: 'spec_chat',
  execution_mode: 'build',
  assigned_node_id: 'node-1',
  assigned_node_name: '编程节点',
  session_id: 'session-1',
  created_at: '2026-10-05T00:00:00Z',
  updated_at: '2026-10-05T00:00:00Z',
};

const BASE_SESSION = {
  id: 'session-1',
  title: '续聊会话',
  specification: null,
  mode: 'thinking',
  model: null,
  task_id: 'task-1',
  created_at: '2026-10-05T00:00:00Z',
  updated_at: '2026-10-05T00:00:00Z',
};

/** node-chat / steer 请求体记录（分流断言用） */
let nodeChatBodies: Record<string, unknown>[];
let steerBodies: Record<string, unknown>[];

function installTaskHandlers(taskOverrides: Record<string, unknown> = {}) {
  server.use(
    http.get('/api/v1/tasks/:id', () =>
      HttpResponse.json({
        code: 0,
        message: 'ok',
        data: { ...BASE_TASK, ...taskOverrides },
      }),
    ),
    http.get('/api/v1/tasks/:id/agent-trace', () =>
      HttpResponse.json({ code: 0, message: 'ok', data: { rows: [], has_more: false } }),
    ),
    http.get('/api/v1/tasks/:id/orchestrator-todos', () =>
      HttpResponse.json({ code: 0, message: 'ok', data: { items: [] } }),
    ),
    http.get('/api/v1/tasks/:id/insight', () =>
      HttpResponse.json(
        { code: 1, message: 'insight unavailable', data: null, request_id: 'req-insight' },
        { status: 500 },
      ),
    ),
  );
}

/** 选中 @ 节点并发送一条消息 */
async function mentionAndSend(text: string) {
  const user = userEvent.setup();
  await screen.findByTestId('mention-node-button');
  await user.click(screen.getByTestId('mention-node-button'));
  await user.click(screen.getByText('@编程节点'));
  await user.type(screen.getByRole('textbox'), text);
  await user.click(screen.getByTestId('send-button'));
}

function renderTaskNew() {
  return render(
    <MemoryRouter initialEntries={['/tasks/new?task=task-1']}>
      <AntApp>
        <TaskNew />
      </AntApp>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  nodeChatBodies = [];
  steerBodies = [];
});

describe('TaskNew `@节点` 续聊分流（ADR-064 M3）', () => {
  it('UT-F1a 空闲 @ → 调 node-chat（202 续聊），用户消息回显且提示等待节点回复', async () => {
    installTaskHandlers();
    server.use(
      http.post('/api/v1/tasks/:id/node-chat', async ({ request }) => {
        nodeChatBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          { code: 0, message: 'ok', data: { reply_to: 'reply-1' } },
          { status: 202 },
        );
      }),
    );
    renderTaskNew();

    await mentionAndSend('把登录按钮改成绿色试试');

    await waitFor(() => {
      expect(nodeChatBodies).toEqual([
        { node_id: 'node-1', text: '把登录按钮改成绿色试试' },
      ]);
    });
    // 用户消息本地保留 + @ 来源标注
    expect(screen.getByText('把登录按钮改成绿色试试')).toBeInTheDocument();
    expect(screen.getByTestId('mention-tag')).toHaveTextContent('@编程节点');
    // 成功提示为续聊语义（不再是「开启新一轮任务」）
    await waitFor(() => {
      expect(screen.getByText('已发送给节点「编程节点」，等待节点回复')).toBeInTheDocument();
    });
  });

  it('UT-F1b 执行中 @ → 调 steer 补充发言，不触发 node-chat', async () => {
    installTaskHandlers({ status: 'running', progress: 40 });
    server.use(
      http.post('/api/v1/tasks/:id/steer', async ({ request }) => {
        steerBodies.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({ code: 0, message: 'ok', data: { delivered: true } });
      }),
    );
    renderTaskNew();

    await mentionAndSend('补充一下：还要支持回车提交');

    await waitFor(() => {
      expect(steerBodies).toEqual([{ text: '补充一下：还要支持回车提交' }]);
    });
    expect(nodeChatBodies).toHaveLength(0);
    await waitFor(() => {
      expect(screen.getByText('已发送给节点「编程节点」（不中断任务）')).toBeInTheDocument();
    });
  });

  it('UT-F2 node-chat 422 → 报错可见且用户消息保留并标记发送失败', async () => {
    installTaskHandlers();
    server.use(
      http.post('/api/v1/tasks/:id/node-chat', () =>
        HttpResponse.json(
          { code: 1, message: '节点离线，无法投递续聊消息', data: null, request_id: 'req-422' },
          { status: 422 },
        ),
      ),
    );
    renderTaskNew();

    await mentionAndSend('把登录按钮改成绿色试试');

    // toast 明确展示后端原因
    await waitFor(() => {
      expect(screen.getByText('节点离线，无法投递续聊消息')).toBeInTheDocument();
    });
    // 用户消息本地保留 + 「发送失败」标记
    expect(screen.getByText('把登录按钮改成绿色试试')).toBeInTheDocument();
    expect(screen.getByTestId('message-failed-tag')).toHaveTextContent('发送失败');
  });

  it('UT-F4 节点回复落库后经轮询回流渲染（节点徽标）', { timeout: 15000 }, async () => {
    installTaskHandlers();
    let sessionFetchCount = 0;
    server.use(
      http.post('/api/v1/tasks/:id/node-chat', () =>
        HttpResponse.json(
          { code: 0, message: 'ok', data: { reply_to: 'reply-1' } },
          { status: 202 },
        ),
      ),
      http.get('/api/v1/tasks/build/sessions/:id', () => {
        sessionFetchCount += 1;
        // 第一次轮询尚无回复，第二次起返回落库的节点回复（后端契约形态）
        const messages =
          sessionFetchCount === 1
            ? []
            : [
                {
                  role: 'assistant',
                  content: '已改为绿色，效果符合预期。',
                  mention_node: '编程节点',
                  reply_to: 'reply-1',
                },
              ];
        return HttpResponse.json({
          code: 0,
          message: 'ok',
          data: { ...BASE_SESSION, messages },
        });
      }),
    );
    renderTaskNew();

    await mentionAndSend('把登录按钮改成绿色试试');

    // 轮询（首 tick 立即空 → 2s 后见回复）回流渲染：节点徽标 + 正文
    await waitFor(
      () => {
        expect(screen.getByTestId('node-reply-badge')).toHaveTextContent('@编程节点');
      },
      { timeout: 6000 },
    );
    expect(screen.getByText('已改为绿色，效果符合预期。')).toBeInTheDocument();
    expect(sessionFetchCount).toBeGreaterThanOrEqual(2);
  });
});
