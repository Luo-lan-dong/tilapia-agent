import { http, HttpResponse } from 'msw';

/**
 * MSW 请求处理器。
 *
 * 拦截 Tilapia 中枢 API，为单元/集成测试提供稳定的 mock 响应。
 */
export const handlers = [
  http.get('/api/v1/settings/llm', () => {
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: {
        provider: 'deepseek',
        api_key: 'test-key',
        base_url: 'https://api.deepseek.com',
        model: 'deepseek-chat',
        is_active: true,
      },
    });
  }),

  http.get('/api/v1/settings/llm/models', () => {
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: [
        {
          id: 'model-1',
          provider: 'deepseek',
          model: 'deepseek-chat',
          base_url: 'https://api.deepseek.com',
          api_key: 'sk-***',
          is_active: true,
          supports_vision: false,
          context_window: 65536,
        },
      ],
    });
  }),

  http.post('/api/v1/tasks/build/chat/stream', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        const events = [
          'event: meta\ndata: {"model":"deepseek-chat","mode":"thinking"}\n\n',
          'event: thinking\ndata: {"delta":"我需要分析这个登录接口需求"}\n\n',
          'event: chunk\ndata: {"delta":"了解了"}\n\n',
          'event: chunk\ndata: {"delta":"，还需要确认一下技术栈。"}\n\n',
          'event: specification\ndata: {"spec":"# Spec\\n\\n开发登录接口","tasks":"# Tasks\\n\\n1. 实现登录","checklist":"# Checklist\\n\\n- [ ] 登录成功"}\n\n',
          'event: status\ndata: {"is_ready_to_create":true}\n\n',
          'event: done\ndata: {}\n\n',
        ];
        for (const event of events) {
          controller.enqueue(encoder.encode(event));
        }
        controller.close();
      },
    });

    return new HttpResponse(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
      },
    });
  }),

  http.get('/api/v1/tasks/build/sessions', () => {
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: {
        items: [
          {
            id: 'session-1',
            title: '历史会话示例',
            messages: [{ role: 'user', content: '历史需求' }],
            specification: {
              spec: '# Spec\n\n历史',
              tasks: '# Tasks\n\n历史',
              checklist: '# Checklist\n\n历史',
            },
            mode: 'thinking',
            model: 'deepseek-chat',
            created_at: '2026-08-07T00:00:00Z',
            updated_at: '2026-08-07T00:00:00Z',
          },
        ],
        pagination: { page: 1, page_size: 100, total: 1, total_pages: 1 },
      },
    });
  }),

  http.get('/api/v1/tasks/build/sessions/:id', ({ params }) => {
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: {
        id: params.id,
        title: '历史会话示例',
        messages: [{ role: 'user', content: '历史需求' }],
        specification: {
          spec: '# Spec\n\n历史',
          tasks: '# Tasks\n\n历史',
          checklist: '# Checklist\n\n历史',
        },
        mode: 'thinking',
        model: 'deepseek-chat',
        created_at: '2026-08-07T00:00:00Z',
        updated_at: '2026-08-07T00:00:00Z',
      },
    });
  }),

  http.post('/api/v1/tasks/build/sessions', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        code: 0,
        message: 'ok',
        data: {
          id: 'new-session-id',
          title: body.title,
          messages: body.messages ?? [],
          specification: body.specification ?? null,
          mode: body.mode ?? 'thinking',
          model: body.model ?? null,
          created_at: '2026-08-07T00:00:00Z',
          updated_at: '2026-08-07T00:00:00Z',
        },
      },
      { status: 201 },
    );
  }),

  http.patch('/api/v1/tasks/build/sessions/:id', async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: {
        id: params.id,
        title: '已更新会话',
        messages: body.messages ?? [],
        specification: body.specification ?? null,
        mode: body.mode ?? 'thinking',
        model: body.model ?? null,
        created_at: '2026-08-07T00:00:00Z',
        updated_at: '2026-08-07T00:00:00Z',
      },
    });
  }),

  http.delete('/api/v1/tasks/build/sessions/:id', () => {
    return new HttpResponse(null, { status: 204 });
  }),

  http.post('/api/v1/tasks', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        code: 0,
        message: 'ok',
        data: {
          id: 'task-1',
          title: body.requirement,
          agent_type: 'programming',
          status: 'pending',
        },
      },
      { status: 201 },
    );
  }),

  http.get('/api/v1/tasks', () => {
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: {
        items: [],
        pagination: { page: 1, page_size: 100, total: 0, total_pages: 0 },
      },
    });
  }),

  http.get('/api/v1/tasks/:id', ({ params }) => {
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: {
        id: params.id,
        title: '示例任务',
        agent_type: 'programming',
        status: 'completed',
        progress: 100,
        source: 'spec_chat',
        execution_mode: 'plan',
        change_brief: null,
        plan_content: '# 实施方案\n\n1. 新增登录接口',
        parent_task_id: null,
        session_id: 'ses_task_demo',
        created_at: '2026-08-15T00:00:00Z',
        updated_at: '2026-08-15T00:00:00Z',
      },
    });
  }),

  http.post('/api/v1/tasks/:id/dispatch', () => {
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: { status: 'queued' },
    });
  }),

  http.post('/api/v1/tasks/:id/approve-plan', () => {
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: { status: 'queued' },
    });
  }),

  http.post('/api/v1/tasks/:id/reject-plan', async ({ request }) => {
    const body = (await request.json()) as { feedback?: string };
    if (!body.feedback || body.feedback.trim().length === 0) {
      return HttpResponse.json({
        code: 1,
        message: '驳回反馈不能为空',
        data: null,
        request_id: 'req-reject',
      });
    }
    return HttpResponse.json({
      code: 0,
      message: 'ok',
      data: { status: 'queued' },
    });
  }),
];
