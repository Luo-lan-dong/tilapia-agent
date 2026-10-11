#!/usr/bin/env node
/**
 * 前端探针（ADR-061 M7）：给子 Agent 一份**文本优先**的浏览器反馈。
 *
 * 设计要点（用户裁决 Q16/Q17）：
 * - **文本优先**：默认只输出结构化 JSON（标题 / 结构快照 / 可见文本摘要 /
 *   控制台错误 / 页面异常 / 失败请求与状态码），无头机器上快且稳；
 * - **截图可选**：加 `--shots <dir>` 才落盘截图（作为产物），不默认产生体积；
 * - **退出码即判据**：0 无问题 / 1 有发现（控制台错误或失败请求）/ 2 导航失败 /
 *   3 依赖缺失（浏览器未预装）——可直接被 `verify.json` 的 L3 检查引用。
 *
 * 用法：
 *   node .tilacode/tools/frontend_probe.mjs --url http://localhost:5173
 *   node .tilacode/tools/frontend_probe.mjs --url http://localhost:5173 --shots out/shots
 */

import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const EXIT_OK = 0;
const EXIT_FINDINGS = 1;
const EXIT_NAVIGATION = 2;
const EXIT_DEPENDENCY = 3;
/** 文本摘要上限（文本优先，避免把整页塞进上下文） */
const TEXT_LIMIT = 1500;
const STRUCTURE_LIMIT = 40;

function parseArgs(argv) {
  const args = {
    url: undefined,
    shots: undefined,
    timeout: 20000,
    waitUntil: 'domcontentloaded',
    fullPageShot: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === '--url') args.url = argv[++index];
    else if (token === '--shots') args.shots = argv[++index];
    else if (token === '--timeout') args.timeout = Number(argv[++index]) || args.timeout;
    else if (token === '--wait-until') args.waitUntil = argv[++index] || args.waitUntil;
    else if (token === '--full-page') args.fullPageShot = true;
  }
  return args;
}

function emit(payload, exitCode) {
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
  process.exit(exitCode);
}

async function loadPlaywright() {
  // 1) 常规解析（探针与依赖同处一棵 node_modules 时命中）
  try {
    return await import('playwright');
  } catch {
    // 2) 回退：按节点安装目录解析（探针被复制进任务工作区，与节点 tools/ 不同树）。
    //    节点启动时注入 TILAPIA_TOOLS_DIR（见 tools/README.md 与 ADR-061 M7）。
    const toolsDir = process.env.TILAPIA_TOOLS_DIR;
    if (toolsDir) {
      try {
        const entry = path.join(toolsDir, 'node_modules', 'playwright', 'index.mjs');
        return await import(pathToFileURL(entry).href);
      } catch {
        // 落到统一报错
      }
    }
  }
  emit(
    {
      ok: false,
      error: 'playwright_unavailable',
      message:
        '未找到 playwright：节点模板应预装（tools/ 下 npm ci + npx playwright install chromium），'
        + '并确保节点进程设置了 TILAPIA_TOOLS_DIR 指向该 tools 目录。',
    },
    EXIT_DEPENDENCY,
  );
  return undefined;
}

function truncate(text, limit) {
  const normalized = String(text ?? '').replace(/\s+\n/g, '\n').trim();
  return normalized.length > limit ? `${normalized.slice(0, limit)}\n…（已截断）` : normalized;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.url) {
    emit({ ok: false, error: 'missing_url', message: '必须提供 --url' }, EXIT_NAVIGATION);
    return;
  }
  const playwright = await loadPlaywright();
  if (!playwright) return;

  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  let browser;
  try {
    browser = await playwright.chromium.launch();
  } catch (error) {
    emit(
      {
        ok: false,
        error: 'chromium_launch_failed',
        message: 'chromium 启动失败：确认已执行 npx playwright install chromium（节点镜像预装）。',
        detail: String(error?.message ?? error),
      },
      EXIT_DEPENDENCY,
    );
    return;
  }

  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 500));
  });
  page.on('pageerror', (error) => pageErrors.push(String(error?.message ?? error).slice(0, 500)));
  page.on('requestfailed', (request) => {
    failedRequests.push({
      method: request.method(),
      url: request.url(),
      failure: request.failure()?.errorText ?? 'unknown',
    });
  });
  page.on('response', (response) => {
    if (response.status() >= 400) {
      failedRequests.push({
        method: response.request().method(),
        url: response.url(),
        status: response.status(),
      });
    }
  });

  let status;
  try {
    const response = await page.goto(args.url, {
      timeout: args.timeout,
      waitUntil: args.waitUntil,
    });
    status = response?.status() ?? null;
  } catch (error) {
    await browser.close();
    emit(
      {
        ok: false,
        error: 'navigation_failed',
        url: args.url,
        message: String(error?.message ?? error).slice(0, 500),
      },
      EXIT_NAVIGATION,
    );
    return;
  }

  // 结构快照（文本优先：标题层级 + 可交互元素的可访问名）
  const structure = await page.evaluate((limit) => {
    const items = [];
    for (const element of document.querySelectorAll('h1,h2,h3,button,a,input,select,textarea')) {
      const tag = element.tagName.toLowerCase();
      const label =
        element.getAttribute('aria-label') ||
        element.getAttribute('placeholder') ||
        element.textContent ||
        element.getAttribute('name') ||
        '';
      const text = String(label).replace(/\s+/g, ' ').trim();
      if (!text && !['input', 'select', 'textarea'].includes(tag)) continue;
      items.push(`${tag}: ${text.slice(0, 80) || '（无名称）'}`);
      if (items.length >= limit) break;
    }
    return items;
  }, STRUCTURE_LIMIT);

  const visibleText = await page.evaluate(() => document.body?.innerText ?? '');
  const shots = [];
  if (args.shots) {
    await mkdir(args.shots, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const viewportPath = path.join(args.shots, `probe-${stamp}.png`);
    await page.screenshot({ path: viewportPath });
    shots.push(viewportPath);
    if (args.fullPageShot) {
      const fullPath = path.join(args.shots, `probe-${stamp}-full.png`);
      await page.screenshot({ path: fullPath, fullPage: true });
      shots.push(fullPath);
    }
    await writeFile(
      path.join(args.shots, `probe-${stamp}.txt`),
      `${await page.title()}\n\n${visibleText}`,
      'utf8',
    );
  }

  const result = {
    ok: consoleErrors.length === 0 && pageErrors.length === 0 && failedRequests.length === 0,
    url: args.url,
    http_status: status,
    title: await page.title(),
    structure,
    text_excerpt: truncate(visibleText, TEXT_LIMIT),
    console_errors: consoleErrors,
    page_errors: pageErrors,
    failed_requests: failedRequests,
    screenshots: shots,
  };
  await browser.close();
  emit(result, result.ok ? EXIT_OK : EXIT_FINDINGS);
}

main().catch((error) => {
  emit(
    { ok: false, error: 'probe_crashed', message: String(error?.message ?? error) },
    EXIT_NAVIGATION,
  );
});
