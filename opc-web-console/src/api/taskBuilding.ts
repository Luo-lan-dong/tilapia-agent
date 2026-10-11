import type {
  TaskBuildChatRequest,
  TaskBuildChatResponse,
  TaskBuildMode,
  TaskBuildStreamEvent,
  TaskSpecification,
  ChatMessageToolCall,
} from '@/types/taskBuilding';
import { request } from './request';

export type { ChatMessage } from '@/types/taskBuilding';

/**
 * 任务构建对话：调用 Spec Agent 生成/更新三件套（同步接口，向后兼容）。
 */
export function taskBuildingChat(data: TaskBuildChatRequest): Promise<TaskBuildChatResponse> {
  return request.post<TaskBuildChatResponse>('/v1/tasks/build/chat', data);
}

/**
 * 执行中插话投递（ADR-045 Steering）：AI 回复进行中把消息入队，
 * Orchestrator 在工具轮边界提升进 prompt。
 *
 * 会话无活跃流（竞态：投递瞬间流结束）时后端返回非 0 业务码，
 * request 封装抛出 ApiError——调用方应降级为普通 sendMessage。
 */
export function steerSession(sessionId: string, content: string): Promise<{ accepted: boolean }> {
  return request.post<{ accepted: boolean }>('/v1/tasks/build/chat/steer', {
    session_id: sessionId,
    content,
  });
}

/**
 * SSE 流式对话配置选项。
 */
export interface TaskBuildingChatStreamOptions {
  messages: {
    role: 'user' | 'assistant' | 'tool';
    content: string;
    /** 工具调用清单（ADR-040 轨迹回流），仅 assistant 消息携带 */
    tool_calls?: ChatMessageToolCall[];
    /** 对应的工具调用 ID（ADR-040），仅 role=tool 消息携带 */
    tool_call_id?: string;
    /** 图片附件（ADR-039）：随消息透传给后端 */
    attachments?: { id: string; file_name: string; mime: string; size: number }[];
  }[];
  current_specification?: TaskSpecification;
  mode?: TaskBuildMode;
  session_id?: string;
  /** 任务链父任务 ID（ADR-028）：AI 创建任务时复用迭代链与工作目录 */
  parent_task_id?: string;
  /** 任务工作目录：AI 创建任务时锁定同目录 */
  output_dir?: string;
  onEvent?: (event: TaskBuildStreamEvent) => void;
  onError?: (error: Error) => void;
  /** 中断信号（问题修复：中枢回答可打断）——abort 即断开 SSE，后端生成器随之取消 */
  signal?: AbortSignal;
}

/**
 * 使用 fetch + ReadableStream 读取 SSE 事件流。
 *
 * EventSource 原生不支持 POST 请求和请求体，因此使用 fetch 手动解析 SSE。
 */
export async function taskBuildingChatStream(
  options: TaskBuildingChatStreamOptions,
): Promise<void> {
  const {
    messages,
    current_specification,
    mode,
    session_id,
    parent_task_id,
    output_dir,
    onEvent,
    onError,
    signal,
  } = options;

  const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api';
  // 例外说明：此处有意绕过统一 request 封装（api/request.ts）。request 基于 axios，
  // 无法返回 ReadableStream 以逐块解析 SSE 增量事件；且 EventSource 原生不支持
  // POST 请求体，故 SSE 流式场景必须使用原生 fetch（见上方 JSDoc）。
  const response = await fetch(`${apiBaseUrl}/v1/tasks/build/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({
      messages,
      current_specification,
      mode,
      session_id,
      parent_task_id,
      output_dir,
    }),
    signal,
  });

  if (!response.ok || !response.body) {
    const errorText = await response.text().catch(() => '流式请求失败');
    throw new Error(errorText || '流式请求失败');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  function parseEventBlock(block: string): void {
    const lines = block.split('\n').map((line) => line.trim());
    let eventName = '';
    let dataText = '';
    for (const line of lines) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataText = line.slice(5).trim();
      }
    }
    if (!eventName) {
      return;
    }
    let data: Record<string, unknown> = {};
    try {
      data = dataText ? (JSON.parse(dataText) as Record<string, unknown>) : {};
    } catch {
      // SSE 数据解析失败时以空对象透传
    }
    onEvent?.({ event: eventName as TaskBuildStreamEvent['event'], data });
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() ?? '';
      for (const block of parts) {
        if (block.trim()) {
          parseEventBlock(block);
        }
      }
    }

    if (buffer.trim()) {
      parseEventBlock(buffer);
    }
  } catch (error) {
    const normalizedError = error instanceof Error ? error : new Error(String(error));
    onError?.(normalizedError);
    throw normalizedError;
  } finally {
    reader.releaseLock();
  }
}

/**
 * 从 SSE 事件 data 中安全读取 string 字段（缺失或类型不符返回 undefined）。
 *
 * 替代消费侧的 `data as {...}` 断言：运行时逐字段校验，避免后端事件结构
 * 变化时前端拿到错误类型仍继续执行。
 */
export function pickString(data: Record<string, unknown>, key: string): string | undefined {
  const value = data[key];
  return typeof value === 'string' ? value : undefined;
}

/**
 * 从 SSE 事件 data 中安全读取 number 字段（缺失或类型不符返回 undefined）。
 */
export function pickNumber(data: Record<string, unknown>, key: string): number | undefined {
  const value = data[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

/**
 * 订阅唤醒轮事件流（ADR-066 唤醒可视化，GET SSE）。
 *
 * 任务终态后中枢在后台执行唤醒轮，本接口实时接收其编排事件
 * （thinking/chunk/step/meta/warning/error/done）。会话无活跃唤醒轮时
 * 服务端立即返回 done 结束；收到 done 或连接错误即结束本次订阅。
 * 与 taskBuildingChatStream 同款 fetch + ReadableStream 解析
 * （EventSource 不支持自定义端点生命周期控制，且需手动收尾防自动重连）。
 */
export async function subscribeWakeStream(
  sessionId: string,
  onEvent: (event: TaskBuildStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api';
  const response = await fetch(
    `${apiBaseUrl}/v1/tasks/build/sessions/${sessionId}/wake-stream`,
    {
      method: 'GET',
      headers: { Accept: 'text/event-stream' },
      signal,
    },
  );
  if (!response.ok || !response.body) {
    return; // 端点不可用视为无唤醒轮，静默结束（旁路原则）
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finished = false;

  const parseEventBlock = (block: string): void => {
    const lines = block.split('\n').map((line) => line.trim());
    let eventName = '';
    let dataText = '';
    for (const line of lines) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataText = line.slice(5).trim();
      }
    }
    if (!eventName) {
      return;
    }
    let data: Record<string, unknown> = {};
    try {
      data = dataText ? (JSON.parse(dataText) as Record<string, unknown>) : {};
    } catch {
      // SSE 数据解析失败时以空对象透传
    }
    if (eventName === 'done') {
      finished = true;
    }
    onEvent({ event: eventName as TaskBuildStreamEvent['event'], data });
  };

  try {
    while (!finished) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop() ?? '';
      for (const block of parts) {
        if (block.trim()) {
          parseEventBlock(block);
        }
      }
    }
  } catch {
    // 连接中断：静默结束（落库 + 轮询同步兜底，旁路原则）
  } finally {
    reader.releaseLock();
  }
}
