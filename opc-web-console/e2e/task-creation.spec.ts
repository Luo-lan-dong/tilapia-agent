import { test, expect } from '@playwright/test';

/**
 * 构造确定性 SSE 响应文本，用于 E2E 测试避免依赖真实 AI 的推理结果。
 *
 * Playwright 的 route.fulfill 只接受 string | Buffer 作为 body，
 * 因此直接返回 SSE 文本而非 ReadableStream。
 */
function createMockStreamResponse(): string {
  const events = [
    'event: meta\ndata: {"model":"deepseek-chat","mode":"thinking"}\n\n',
    // 模拟真实的 reasoning_content 流式增量
    'event: thinking\ndata: {"delta":"分析需求"}\n\n',
    'event: thinking\ndata: {"delta":"，"}\n\n',
    'event: thinking\ndata: {"delta":"登录接口涉及用户名和密码验证"}\n\n',
    'event: chunk\ndata: {"delta":"了解了，这是一个登录接口需求。"}\n\n',
    'event: specification\ndata: {"spec":"# Spec\\n\\n开发登录接口","tasks":"# Tasks\\n\\n1. 实现登录","checklist":"# Checklist\\n\\n- [ ] 登录成功"}\n\n',
    'event: status\ndata: {"is_ready_to_create":true}\n\n',
    'event: done\ndata: {}\n\n',
  ];

  return events.join('');
}

test.describe('任务创建 Spec Chat 流程', () => {
  test.beforeEach(async ({ page }) => {
    // 拦截 LLM 配置接口，避免依赖真实后端配置
    await page.route('**/api/v1/settings/llm', async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code: 0,
          message: 'ok',
          data: {
            provider: 'deepseek',
            api_key: 'test-key',
            base_url: 'https://api.deepseek.com',
            model: 'deepseek-chat',
            is_active: true,
          },
        }),
      });
    });

    // 拦截 SSE 流式接口，使用确定性 mock 响应
    await page.route('**/api/v1/tasks/build/chat/stream', async (route) => {
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: createMockStreamResponse(),
      });
    });

    // 拦截任务构建会话接口，避免依赖真实后端
    await page.route('**/api/v1/tasks/build/sessions', async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: 0,
            message: 'ok',
            data: [
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
          }),
        });
        return;
      }

      if (method === 'POST') {
        await route.fulfill({
          status: 201,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: 0,
            message: 'ok',
            data: {
              id: 'new-session-id',
              title: '开发一个登录接口',
              messages: [],
              specification: null,
              mode: 'thinking',
              model: 'deepseek-chat',
              created_at: '2026-08-08T00:00:00Z',
              updated_at: '2026-08-08T00:00:00Z',
            },
          }),
        });
        return;
      }

      await route.continue();
    });

    // 拦截任务创建接口
    await page.route('**/api/v1/tasks', async (route) => {
      const method = route.request().method();
      if (method === 'POST') {
        await route.fulfill({
          status: 201,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: 0,
            message: 'ok',
            data: {
              id: 'task-1',
              title: '开发一个登录接口',
              agent_type: 'programming',
              status: 'pending',
            },
          }),
        });
        return;
      }

      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            code: 0,
            message: 'ok',
            data: {
              items: [
                {
                  id: 'task-1',
                  title: '开发一个登录接口',
                  agent_type: 'programming',
                  status: 'pending',
                  progress: 0,
                  source: 'spec_chat',
                  created_at: '2026-08-08T00:00:00Z',
                  updated_at: '2026-08-08T00:00:00Z',
                },
              ],
              total: 1,
            },
          }),
        });
        return;
      }

      await route.continue();
    });
  });

  test('SSE 多轮对话生成三件套并创建任务', async ({ page }) => {
    page.on('request', (req) => console.log('>>', req.method(), req.url()));
    page.on('response', (res) => console.log('<<', res.status(), res.url()));
    await page.goto('/tasks/new');

    // 默认显示当前模型和思考模式
    await expect(page.getByTestId('current-model')).toHaveText('deepseek-chat');
    await expect(page.getByLabel('思考')).toBeChecked();

    // 输入需求并发送
    await page.getByPlaceholder('描述你的任务需求').fill('开发一个登录接口');
    await page.getByRole('button', { name: /发送/ }).click();

    // 等待流式回复完成并启用创建任务按钮
    await expect(page.getByRole('button', { name: '创建任务' })).toBeEnabled({ timeout: 10000 });

    // 右侧文档区应展示三件套内容
    await expect(page.getByText('# Spec')).toBeVisible();

    // 创建任务
    await page.getByRole('button', { name: '创建任务' }).click();

    // 跳转任务队列并展示新建任务
    await expect(page).toHaveURL('/tasks');
    await expect(page.getByText('开发一个登录接口')).toBeVisible();
  });

  test('历史会话管理', async ({ page }) => {
    await page.goto('/tasks/new');

    // 触发对话以创建会话
    await page.getByPlaceholder('描述你的任务需求').fill('历史会话测试');
    await page.getByRole('button', { name: /发送/ }).click();
    await expect(page.getByRole('button', { name: '创建任务' })).toBeEnabled({ timeout: 10000 });

    // 打开历史会话抽屉
    await page.getByTestId('history-button').click();
    await expect(page.getByRole('button', { name: '刷新' })).toBeVisible();

    // 新建会话清空当前状态
    await page.getByRole('button', { name: '新建会话' }).click();
    await expect(page.getByPlaceholder('描述你的任务需求')).toHaveValue('');
  });
});
