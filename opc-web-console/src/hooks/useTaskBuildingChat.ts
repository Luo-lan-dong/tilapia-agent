/* 规范豁免说明（coding-standards 4.3，Hook 文件上限 200 行）：本 Hook 承载
   SSE 事件状态机（chunk/thinking/specification/status/step 等）对同一份
   messages/specification 状态的强内聚更新，拆分会引入跨文件状态传递层；
   技能步骤处理已拆至 useSkillSteps（ADR-033），故按豁免场景保留。 */
import { useCallback, useRef, useState } from 'react';
import { App } from 'antd';
import { steerSession, taskBuildingChatStream } from '@/api/taskBuilding';
import type {
  ChatMessage,
  ChatMessageAttachment,
  TaskBuildMode,
  TaskBuildStreamContextUsage,
  TaskBuildStreamEvent,
  TaskSpecification,
  TerminalApprovalRequest,
} from '@/types/taskBuilding';
import { terminalStore } from '@/stores/terminalStore';
import { useSkillSteps } from '@/hooks/useSkillSteps';

export interface UseTaskBuildingChatState {
  messages: ChatMessage[];
  specification: TaskSpecification;
  isReady: boolean;
  sending: boolean;
  model: string;
  mode: TaskBuildMode;
  error: string | null;
  /** 发送等待期的阶段提示文案（如「正在编写规范文档…」），空串回退默认气泡 */
  stageText: string;
  /** 上下文占用（ADR-041），null 表示本轮尚未收到计量 */
  contextUsage: TaskBuildStreamContextUsage | null;
  /** 历史对话压缩进行中（ADR-041 增强）：圆环显示压缩动画，完成后回落 */
  compacting: boolean;
  /** 终端命令审批请求（ADR-053）：manual/ai_auto 模式 AI 执行前等待用户决定 */
  approvalRequest: TerminalApprovalRequest | null;
  /** 中枢待办实际写入的任务 ID（ADR-061 M5d）：todo_write 成功时更新 */
  lastTodoTaskId: string | null;
}

export interface UseTaskBuildingChatActions {
  setMode: (mode: TaskBuildMode) => void;
  setModel: (model: string) => void;
  setSpecification: (specification: TaskSpecification) => void;
  /** 中断当前 AI 回答（问题修复）：abort SSE 流，已生成内容保留 */
  stop: () => void;
  sendMessage: (
    content: string,
    sessionId?: string,
    /** 任务链上下文（ADR-028）：AI 创建任务时复用迭代链与工作目录 */
    context?: { parent_task_id?: string; output_dir?: string },
    /** 图片附件（ADR-039）：随本轮 user 消息上传给后端 */
    attachments?: ChatMessageAttachment[],
  ) => Promise<void>;
  /**
   * 重置对话状态。传入 sessionId（恢复历史会话）时从 sessionStorage
   * 恢复上下文占用（ADR-041 圆环跨页面存活）；不传（新建会话）则清空。
   */
  reset: (
    initialMessages?: ChatMessage[],
    initialSpecification?: TaskSpecification,
    sessionId?: string,
  ) => void;
  /**
   * 执行中插话（ADR-045）：AI 回复进行中投递消息。
   * 入队成功返回 true（本地显示排队气泡）；失败（流已结束的竞态）
   * 返回 false，调用方降级为普通 sendMessage。
   */
  steer: (content: string, sessionId: string) => Promise<boolean>;
  /**
   * 本地追加一条用户消息（ADR-063 M4 `@节点`）：@消息不走中枢编排流，
   * 由页面层自行调用 steer/node-chat 接口后回显，带来源标注。
   */
  appendUserMessage: (message: ChatMessage) => void;
  /**
   * 追加节点回复消息（ADR-064 M3）：轮询见到 reply_to 对应的 assistant
   * 消息后回流会话流；按回复关联 ID 幂等去重。
   */
  appendAssistantMessage: (message: ChatMessage) => void;
  /**
   * 应用流式事件到本地消息列表（ADR-066 唤醒可视化）：thinking/chunk/step
   * 渲染逻辑与正常对话完全一致，供唤醒轮 SSE 订阅（useWakeStream）复用。
   */
  applyChatStreamEvent: (event: TaskBuildStreamEvent) => void;
  /** 将 202 返回的 reply_to 写回对应本地用户消息（ADR-064 M3 关联保存） */
  markMessageReplyTo: (target: ChatMessage, replyTo: string) => void;
  /** 标记消息发送失败（ADR-064 M3）：气泡保留并显式展示「发送失败」 */
  markMessageFailed: (target: ChatMessage) => void;
  clearError: () => void;
  /** 清空终端命令审批请求（ADR-053，用户批准/拒绝后调用） */
  setApprovalRequest: (request: TerminalApprovalRequest | null) => void;
}

/**
 * SSE 事件 data 字段安全取值（unknown → 目标类型）。
 */
function pickString(data: Record<string, unknown>, key: string): string | undefined {
  const value = data[key];
  return typeof value === 'string' ? value : undefined;
}

function pickNumber(data: Record<string, unknown>, key: string): number | undefined {
  const value = data[key];
  return typeof value === 'number' ? value : undefined;
}

/**
 * 上下文占用的会话级 sessionStorage 缓存键（ADR-041：圆环跨页面存活）。
 */
function contextUsageKey(sessionId: string): string {
  return `opc:ctx-usage:${sessionId}`;
}

/**
 * 三件套初始空模板。
 */
const EMPTY_SPECIFICATION: TaskSpecification = {
  spec: '# 任务规范\n\n在左侧输入你的需求，AI 会通过追问帮你澄清，并逐步完善这份规范。',
  tasks: '# 任务清单\n\n等待 AI 生成。',
  checklist: '# 验收清单\n\n等待 AI 生成。',
};

/**
 * 管理任务构建 SSE 流式对话的状态与副作用。
 */
export function useTaskBuildingChat(): [UseTaskBuildingChatState, UseTaskBuildingChatActions] {
  const { message: antdMessage } = App.useApp();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [specification, setSpecification] = useState<TaskSpecification>(EMPTY_SPECIFICATION);
  const [isReady, setIsReady] = useState(false);
  const [sending, setSending] = useState(false);
  // 中枢回答中断（问题修复）：abort 后 SSE 断开，后端生成器随之取消
  const abortRef = useRef<AbortController | null>(null);
  const [model, setModel] = useState('');
  const [mode, setMode] = useState<TaskBuildMode>('thinking');
  const [error, setError] = useState<string | null>(null);
  const [stageText, setStageText] = useState('');
  const [contextUsage, setContextUsage] = useState<TaskBuildStreamContextUsage | null>(null);
  const [compacting, setCompacting] = useState(false);
  // 终端命令审批请求（ADR-053）：run_command 在 manual/ai_auto(high|unknown) 下发出
  const [approvalRequest, setApprovalRequest] = useState<TerminalApprovalRequest | null>(null);
  // 中枢待办实际写入的任务（ADR-061 M5d）：todo_write 成功时更新，
  // 页面据此把任务摘要面板的跟踪任务对齐到该任务（会话绑定任务会随
  // 多次 create_task 漂移，仅靠 URL ?task= 参数会错位显示空清单）
  const [lastTodoTaskId, setLastTodoTaskId] = useState<string | null>(null);
  const activeRef = useRef(false);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const steer = useCallback(
    async (content: string, sessionId: string): Promise<boolean> => {
      const trimmed = content.trim();
      if (!trimmed) {
        return false;
      }
      try {
        await steerSession(sessionId, trimmed);
      } catch {
        // 无活跃流（投递瞬间流结束的竞态）：调用方降级为普通发送
        return false;
      }
      // 乐观展示排队气泡；steering_applied 事件到达后清除 queued 标记
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: trimmed, queued: true },
      ]);
      return true;
    },
    [],
  );

  const applyStepEvent = useSkillSteps(setMessages);

  /**
   * 应用流式事件到本地消息列表（渲染层共享逻辑，ADR-066 唤醒可视化）：
   * thinking 合并（含回合分割）、chunk 拼接、技能步骤卡片增删——
   * sendMessage 的正常对话流与唤醒轮 SSE 订阅（useWakeStream）共用，
   * 保证两种入口的渲染行为一致。
   */
  const applyChatStreamEvent = useCallback(
    (event: TaskBuildStreamEvent) => {
      switch (event.event) {
        case 'step_started':
        case 'step_finished': {
          applyStepEvent(event);
          break;
        }
        case 'thinking': {
          const delta = pickString(event.data, 'delta') ?? '';
          if (delta) {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                const lastStepRunning =
                  last.steps
                  && last.steps.length > 0
                  && last.steps[last.steps.length - 1].status === 'running';
                if (last.content || (last.steps && last.steps.length > 0 && !lastStepRunning)) {
                  // 回合分割：正文已开始，或上一条消息的工具步骤已全部
                  // 收尾（再收到思考即 Orchestrator 开启新一轮工具循环）
                  // ——封口旧回合，新建一条 assistant 消息承载本轮
                  // thinking/steps，对话流按"思考→工具→思考→…"层叠。
                  return [...prev, { role: 'assistant', content: '', thinking: delta }];
                }
                // 合并到当前消息（思考块渲染在工具卡片上方）
                return [...prev.slice(0, -1), { ...last, thinking: (last.thinking ?? '') + delta }];
              }
              return [...prev, { role: 'assistant', content: '', thinking: delta }];
            });
          }
          break;
        }
        case 'chunk': {
          const delta = pickString(event.data, 'delta') ?? '';
          if (delta) {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.role === 'assistant') {
                // spread 保留 steps：否则技能执行完第一个 chunk 会把步骤卡片抹掉
                return [...prev.slice(0, -1), { ...last, content: last.content + delta }];
              }
              return [...prev, { role: 'assistant', content: delta }];
            });
          }
          break;
        }
        default:
          break;
      }
    },
    [applyStepEvent],
  );

  /** 本地追加用户消息（ADR-063 M4 `@节点` 回显，带来源标注，不触发编排流）。 */
  const appendUserMessage = useCallback((message: ChatMessage) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  /**
   * 追加节点回复消息（ADR-064 M3）：按回复关联 ID（replyTo/reply_to 双形态）
   * 幂等去重——pending 目标更新滞后于回调时避免重复堆叠。仅对 assistant
   * 消息匹配：同一 reply_to 同时关联发起的 user 消息与节点 assistant 回复，
   * user 消息先行落 key 不得吞掉后续回复。
   */
  const appendAssistantMessage = useCallback((message: ChatMessage) => {
    setMessages((prev) => {
      const replyKey = message.replyTo ?? message.reply_to;
      if (
        replyKey &&
        prev.some(
          (m) => m.role === 'assistant' && (m.replyTo ?? m.reply_to) === replyKey,
        )
      ) {
        return prev;
      }
      return [...prev, message];
    });
  }, []);

  /** 将 202 的 reply_to 写回本地用户消息（按引用定位，ADR-064 M3）。 */
  const markMessageReplyTo = useCallback((target: ChatMessage, replyTo: string) => {
    setMessages((prev) => prev.map((m) => (m === target ? { ...m, replyTo } : m)));
  }, []);

  /** 标记发送失败（按引用定位；气泡保留供重发参考，ADR-064 M3）。 */
  const markMessageFailed = useCallback((target: ChatMessage) => {
    setMessages((prev) => prev.map((m) => (m === target ? { ...m, failed: true } : m)));
  }, []);

  const reset = useCallback(
    (
      initialMessages?: ChatMessage[],
      initialSpecification?: TaskSpecification,
      sessionId?: string,
    ) => {
      activeRef.current = false;
      setMessages(initialMessages ?? []);
      setSpecification(initialSpecification ?? EMPTY_SPECIFICATION);
      setIsReady(false);
      setSending(false);
      setStageText('');
      // 模型是全局 LLM 配置，不是会话状态，重置时不应清空
      setMode('thinking');
      setError(null);
      // 上下文占用（ADR-041）：恢复历史会话时从 sessionStorage 读回，
      // 新建会话（未传 sessionId）则清空
      let restoredUsage: TaskBuildStreamContextUsage | null = null;
      if (sessionId) {
        try {
          const raw = sessionStorage.getItem(contextUsageKey(sessionId));
          if (raw) {
            const parsed = JSON.parse(raw) as TaskBuildStreamContextUsage;
            if (typeof parsed.used_tokens === 'number') {
              restoredUsage = parsed;
            }
          }
        } catch {
          restoredUsage = null;
        }
      }
      setContextUsage(restoredUsage);
      setCompacting(false);
    },
    [],
  );

  const sendMessage = useCallback(
    async (
      content: string,
      sessionId?: string,
      context?: { parent_task_id?: string; output_dir?: string },
      attachments?: ChatMessageAttachment[],
    ) => {
      const hasAttachments = Boolean(attachments && attachments.length > 0);
      if ((!content.trim() && !hasAttachments) || sending || activeRef.current) {
        return;
      }

      const userMessage: ChatMessage = { role: 'user', content };
      if (hasAttachments) {
        userMessage.attachments = attachments;
      }
      const nextMessages: ChatMessage[] = [...messages, userMessage];
      setMessages(nextMessages);
      setSending(true);
      setError(null);
      setStageText('');
      // 上下文占用保留上一轮数值（修复：发送时圆环不再消失），
      // 压缩进行中标记复位，新 context_usage 事件到来时自然更新
      setCompacting(false);
      activeRef.current = true;

      let aiReply = '';
      let streamError = '';
      // 每轮新建中断控制器：停止按钮 abort 即断开 SSE（后端生成器随之取消）
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await taskBuildingChatStream({
          // preview_url 是本地 blob 预览地址，不随请求发往后端；
          // thinking / steps（ADR-033）随历史回传供后端持久化，恢复会话后
          // 思考内容与步骤卡片不丢失；后端构建 LLM 请求体时会自动剥离二者。
          // tool_calls / tool_call_id 是工具轨迹（ADR-040），须原样回传供回流
          signal: controller.signal,
          messages: nextMessages.map((m) => ({
            role: m.role,
            content: m.content,
            ...(m.thinking ? { thinking: m.thinking } : {}),
            ...(m.steps ? { steps: m.steps } : {}),
            ...(m.tool_calls ? { tool_calls: m.tool_calls } : {}),
            ...(m.tool_call_id ? { tool_call_id: m.tool_call_id } : {}),
            ...(m.attachments
              ? {
                  attachments: m.attachments.map(({ id, file_name, mime, size }) => ({
                    id,
                    file_name,
                    mime,
                    size,
                  })),
                }
              : {}),
          })),
          current_specification: specification,
          mode,
          session_id: sessionId,
          parent_task_id: context?.parent_task_id,
          output_dir: context?.output_dir,
          onEvent: (event: TaskBuildStreamEvent) => {
            switch (event.event) {
              case 'step_started':
              case 'step_finished': {
                applyChatStreamEvent(event);
                // 任务摘要面板对齐：todo_write（ADR-061 M5d）/ create_task
                // （ADR-067）成功后记录实际任务 ID，供页面把 ?task= 对齐过去。
                // ADR-067 待办先行后会话阶段待办挂会话（result_data 无 task_id
                // 不记录），创建任务的成功事件成为唯一的对齐信号
                const finishedSkill = pickString(event.data, 'skill_name');
                if (
                  (finishedSkill === 'todo_write' || finishedSkill === 'create_task')
                  && pickString(event.data, 'status') === 'success'
                ) {
                  const resultData = event.data?.result_data;
                  const todoTaskId =
                    resultData && typeof resultData === 'object'
                      ? pickString(resultData as Record<string, unknown>, 'task_id')
                      : undefined;
                  if (todoTaskId) {
                    setLastTodoTaskId(todoTaskId);
                  }
                }
                break;
              }
              case 'thinking': {
                applyChatStreamEvent(event);
                break;
              }
              case 'chunk': {
                const delta = pickString(event.data, 'delta') ?? '';
                if (delta) {
                  aiReply += delta;
                }
                applyChatStreamEvent(event);
                break;
              }
              case 'meta': {
                const model = pickString(event.data, 'model');
                const mode = pickString(event.data, 'mode');
                if (model) {
                  setModel(model);
                }
                if (mode === 'fast' || mode === 'thinking') {
                  setMode(mode);
                }
                break;
              }
              case 'specification': {
                const spec = pickString(event.data, 'spec');
                const tasks = pickString(event.data, 'tasks');
                const checklist = pickString(event.data, 'checklist');
                setSpecification((prev) => ({
                  spec: spec || prev.spec,
                  tasks: tasks || prev.tasks,
                  checklist: checklist || prev.checklist,
                }));
                break;
              }
              case 'specification_delta': {
                // 三件套流式增量（ADR-028 UI 增强）：按字段拼接，文档逐字出现
                const field = pickString(event.data, 'field');
                const delta = pickString(event.data, 'delta') ?? '';
                if (
                  delta &&
                  (field === 'spec' || field === 'tasks' || field === 'checklist')
                ) {
                  setSpecification((prev) => ({
                    ...prev,
                    [field]: (prev[field] ?? '') + delta,
                  }));
                }
                break;
              }
              case 'status': {
                const stageMessage = pickString(event.data, 'message');
                if (stageMessage) {
                  setStageText(stageMessage);
                }
                const isReadyToCreate = event.data.is_ready_to_create;
                if (typeof isReadyToCreate === 'boolean') {
                  setIsReady(isReadyToCreate);
                }
                break;
              }
              case 'warning': {
                // 非致命提示（ADR-039）：如当前对话模型不支持图片识别
                const warningText = pickString(event.data, 'message');
                if (warningText) {
                  antdMessage.warning(warningText);
                }
                break;
              }
              case 'context_usage': {
                // 上下文占用（ADR-041）：真实 usage 优先，驱动前端圆环指示器；
                // 按 sessionId 存 sessionStorage，跨页面切换后恢复会话仍可展示
                const usedTokens = pickNumber(event.data, 'used_tokens');
                if (usedTokens !== undefined) {
                  const rawPercent = event.data.percent;
                  const nextUsage: TaskBuildStreamContextUsage = {
                    used_tokens: usedTokens,
                    window_tokens: pickNumber(event.data, 'window_tokens') ?? 0,
                    percent: typeof rawPercent === 'number' ? rawPercent : null,
                  };
                  setContextUsage(nextUsage);
                  if (sessionId) {
                    try {
                      sessionStorage.setItem(
                        contextUsageKey(sessionId),
                        JSON.stringify(nextUsage),
                      );
                    } catch {
                      // 存储不可用时静默：圆环仅当前页面存活
                    }
                  }
                }
                break;
              }
              case 'context_compacting': {
                // 压缩进行中（ADR-041 增强）：圆环显示压缩动画
                setCompacting(true);
                break;
              }
              case 'context_compacted': {
                // 压缩完成（ADR-041 增强）：复位动画标记；
                // 压缩生效时随后的 context_usage 事件令圆环回落
                setCompacting(false);
                if (event.data.compacted === true) {
                  antdMessage.info('已压缩历史对话，上下文占用将回落');
                }
                break;
              }
              case 'steering_applied': {
                // 插话已提升进 prompt（ADR-045）：清除对应排队气泡标记。
                // 按 content 匹配最早一条 queued 消息（多条插话按投递顺序提升）
                const steeredContent = pickString(event.data, 'content');
                if (steeredContent) {
                  setMessages((prev) => {
                    const idx = prev.findIndex(
                      (m) => m.role === 'user' && m.queued && m.content === steeredContent,
                    );
                    if (idx < 0) {
                      return prev;
                    }
                    const next = [...prev];
                    next[idx] = { ...next[idx], queued: undefined };
                    return next;
                  });
                }
                break;
              }
              case 'command_approval_request': {
                const approvalId = pickString(event.data, 'approval_id');
                const command = pickString(event.data, 'command');
                if (approvalId && command) {
                  setApprovalRequest({
                    approval_id: approvalId,
                    command,
                    risk_level: pickString(event.data, 'risk_level') ?? 'unknown',
                  });
                }
                break;
              }
              case 'command_executing': {
                // AI 开始执行终端命令：自动展开终端抽屉（ADR-053）
                terminalStore.setOpen(true);
                break;
              }
              case 'error': {
                // 错误可见化（ADR-028 期修复）：后端解析失败等错误直接
                // 写入占位消息，避免只显示「未生成正文」掩盖真实原因
                streamError = pickString(event.data, 'message') || 'AI 生成规范失败，请重试';
                setMessages((prev) => {
                  const last = prev[prev.length - 1];
                  if (last && last.role === 'assistant' && last.content === '') {
                    return [...prev.slice(0, -1), { role: 'assistant', content: streamError }];
                  }
                  return prev;
                });
                break;
              }
              case 'done':
              default:
                break;
            }
          },
        });
      } catch (err) {
        // 用户主动停止（abort）：静默结束，保留已生成内容，不作为错误展示
        const aborted =
          err instanceof DOMException && err.name === 'AbortError';
        if (aborted) {
          return;
        }
        const message = err instanceof Error ? err.message : 'AI 生成规范失败，请重试';
        setError(message);
        // 如果 AI 尚未输出任何内容，移除占位消息
        if (aiReply === '') {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last && last.role === 'assistant' && last.content === '') {
              return prev.slice(0, -1);
            }
            return prev;
          });
        }
      } finally {
        setSending(false);
        setStageText('');
        activeRef.current = false;
        abortRef.current = null;
        // 步骤收尾兜底（问题修复）：abort/断流后不会再收到 step_finished，
        // 仍为 running 的步骤统一标记失败，避免历史会话恢复后永远「执行中」
        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.role !== 'assistant' || !msg.steps?.some((s) => s.status === 'running')) {
              return msg;
            }
            return {
              ...msg,
              steps: msg.steps.map((step) =>
                step.status === 'running'
                  ? { ...step, status: 'failed', result_summary: '已中断，未收到结束事件' }
                  : step,
              ),
            };
          }),
        );
        // 空回复兜底（ADR-028 期修复）：思考结束但未产出任何正文时给用户可见提示，
        // 避免「看起来卡住」；后端已下发 error 事件时保持错误文案，不再覆盖
        setMessages((prev) => {
          if (streamError) {
            return prev;
          }
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant' && last.content.trim() === '') {
            return [
              ...prev.slice(0, -1),
              {
                ...last,
                content: '（本轮未生成回复正文，可重新发送或改用「快速」模式重试）',
              },
            ];
          }
          return prev;
        });
      }
    },
    [messages, specification, mode, sending, setModel, applyStepEvent, antdMessage],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return [
    {
      messages,
      specification,
      isReady,
      sending,
      model,
      mode,
      error,
      stageText,
      contextUsage,
      compacting,
      approvalRequest,
      lastTodoTaskId,
    },
    { setMode, setModel, setSpecification, stop, sendMessage, reset, steer, appendUserMessage, appendAssistantMessage, markMessageReplyTo, markMessageFailed, clearError, setApprovalRequest, applyChatStreamEvent },
  ];
}
