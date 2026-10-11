/* 职责说明（coding-standards 4.3）：本页面保留 Spec Chat 会话编排主链路与工具面板
   打开状态；输入底栏（TaskNewInputBar）、右侧工具面板（TaskNewRightPanel：空态
   入口/标签页/工作目录）、工具元数据（TaskNewToolDefs/TaskToolEmpty）等已按职责
   拆分为独立文件。迭代条（TaskNewIterationBar）已移除（2026-09-15）：迭代进入
   ?parent= 时直接恢复父任务构建会话，任务链上下文经对话请求透传给 create_task
   技能，不再展示"基于任务继续迭代"UI。 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { App, Modal, Tooltip, Button } from 'antd';
import { MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { uploadSessionAttachment } from '@/api/attachments';
import { decideTerminalApproval } from '@/api/terminal';
import { terminalStore } from '@/stores/terminalStore';
import { fetchLLMProviderConfig, fetchLLMModels } from '@/api/llmProvider';
import { cancelTask, createTask, fetchTasks, nodeChatTask, steerTask } from '@/api/tasks';
import { fetchTaskBuildingSessionById } from '@/api/taskBuildingSessions';
import { TaskNewChatPanel } from '@/components/TaskNewChatPanel';
import { TaskNewInputBar } from '@/components/TaskNewInputBar';
import { TaskNewRightPanel } from '@/components/TaskNewRightPanel';
import { TaskSessionDrawer } from '@/components/TaskSessionDrawer';
import type { ToolKey } from '@/components/TaskNewToolDefs';
import type { NodeMentionCandidate, NodeTraceGroup } from '@/components/TaskNewChatPanel';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { useParentTaskContext } from '@/hooks/useParentTaskContext';
import { useSessionParamRestore } from '@/hooks/useSessionParamRestore';
import { usePlanReviewFlow } from '@/hooks/usePlanReviewFlow';
import { useResizablePanel } from '@/hooks/useResizablePanel';
import { useSessionOrchestratorTodos } from '@/hooks/useSessionOrchestratorTodos';
import { useTaskAgentTrace } from '@/hooks/useTaskAgentTrace';
import { useTaskBuildingChat } from '@/hooks/useTaskBuildingChat';
import { useTaskBuildingSessions } from '@/hooks/useTaskBuildingSessions';
import { useTaskPlanTracking } from '@/hooks/useTaskPlanTracking';
import { useTaskSessionActions } from '@/hooks/useTaskSessionActions';
import { useWakeMessagesSync } from '@/hooks/useWakeMessagesSync';
import { useWakeStream } from '@/hooks/useWakeStream';
import type {
  ChatMessage,
  ChatMessageAttachment,
  TaskBuildingSession,
  TaskBuildingSessionSummary,
  TaskSpecification,
  UploadingAttachment,
} from '@/types/taskBuilding';
import type { ExecutorModelSelection, TaskExecutionMode } from '@/types/task';
import { useNodeChatReplyPoll } from '@/hooks/useNodeChatReplyPoll';
import type { PendingNodeReply } from '@/hooks/useNodeChatReplyPoll';
import { patchSearchParams } from '@/utils/searchParams';
import './TaskNew.css';

/** 移动端断点 */
const MOBILE_BREAKPOINT = '(max-width: 767px)';

/**
 * 任务创建页。
 *
 * 通过多轮 AI 对话收集需求信息，右侧实时展示并允许编辑 spec/tasks/checklist
 * 三件套；「方案」Tab 跟踪已创建任务的方案审批闭环（plan 方案 → 批准/驳回
 * → build 开发 → 本轮修改说明，见 ADR-012 对话式评审增补决策）。
 */
export function TaskNew() {
  const { message } = App.useApp();
  const [searchParams, setSearchParams] = useSearchParams();
  const [chatState, chatActions] = useTaskBuildingChat();
  const [sessionState, sessionActions] = useTaskBuildingSessions();
  const isMobile = useMediaQuery(MOBILE_BREAKPOINT);

  const [input, setInput] = useState('');
  // 待发送图片附件（ADR-039）：选择/粘贴后立即上传到会话，发送时随消息带上
  const [pendingAttachments, setPendingAttachments] = useState<ChatMessageAttachment[]>([]);
  // 上传中的附件（ADR-039）：输入框内实时进度展示，避免大文件误以为卡死
  const [uploadingList, setUploadingList] = useState<UploadingAttachment[]>([]);
  const uploadControllersRef = useRef(new Map<string, AbortController>());
  // 工具面板打开状态：openTools 为已打开工具（标签栏顺序），activeTool 为激活视图
  const [openTools, setOpenTools] = useState<ToolKey[]>([]);
  const [activeTool, setActiveTool] = useState<ToolKey | null>(null);
  const [creating, setCreating] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [currentSessionId, setCurrentSessionId] = useState<string | undefined>();
  const [executionMode, setExecutionMode] = useState<TaskExecutionMode>('build');
  const [resumeKey, setResumeKey] = useState(0);
  // 任务工作目录（ADR-028 任务锁定）：下拉为历史目录（来自已创建任务去重），可自由输入
  const [workspaceDir, setWorkspaceDir] = useState('');

  // 终端抽屉（ADR-053）：同步会话 ID 与工作目录（PTY 初始 cwd 与 AI 执行回显目标）
  useEffect(() => {
    terminalStore.setSession(currentSessionId ?? null, workspaceDir.trim() || null);
  }, [currentSessionId, workspaceDir]);
  // 子 Agent 执行模型（ADR-029/030）：默认中枢配置模型，可选自定义模型
  const [executorModel, setExecutorModel] = useState<ExecutorModelSelection>({ source: 'hub' });
  const [workspaceTasks, setWorkspaceTasks] = useState<
    { id: string; title: string; output_dir: string | null }[]
  >([]);

  // 右侧整体面板折叠与宽度（像素）：默认 400px（2026-09-13 布局改版收窄）。
  // 当初始可用宽度不足以同时容纳左侧聊天区（最小 320px）和右侧面板（400px）时，
  // 默认折叠右侧面板，避免挤压聊天区域。
  const [rightCollapsed, setRightCollapsed] = useState(() => {
    const siderWidth = isMobile ? 0 : 200;
    const availableWidth = window.innerWidth - siderWidth;
    return availableWidth < 920;
  });
  // 右侧面板拖拽调宽逻辑已抽取为通用 Hook（useResizablePanel）。
  // 会话区保底宽度（2026-10-05）：max 动态收窄为「窗口宽 - 侧栏 - 会话区保底」，
  // 防止把分隔条一路拖到底导致会话区被完全挤没；1400 仅作大屏绝对上限。
  const CHAT_MIN_WIDTH = 360;
  const [windowWidth, setWindowWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  const rightPanelMax = Math.max(
    320,
    windowWidth - (isMobile ? 0 : 200) - CHAT_MIN_WIDTH,
  );
  const { width: rightWidth, isResizing, handleResizeStart } = useResizablePanel(400, {
    max: Math.min(1400, rightPanelMax),
  });

  /** 迭代链父任务 ID，来自任务队列「继续修改」入口（/tasks/new?parent=<task_id>） */
  const parentTaskId = searchParams.get('parent') ?? undefined;
  /** 创建成功后跟踪的任务 ID（?task=），方案 Tab 据此轮询任务状态 */
  const activeTaskId = searchParams.get('task') ?? undefined;

  const { messages, specification, isReady, sending, model, mode, error, stageText, contextUsage, compacting, lastTodoTaskId } =
    chatState;
  const { setMode, setModel, setSpecification, sendMessage, reset, steer, appendUserMessage, appendAssistantMessage, markMessageReplyTo, markMessageFailed, clearError, stop, applyChatStreamEvent } =
    chatActions;
  const { sessions, loading: sessionsLoading } = sessionState;
  const { createSession, updateSession, removeSession, refresh } = sessionActions;

  // orchestratorTodos / insight 一并解构（2026-10-06 修复：此前仅取 task/loading，
  // 中枢待办与洞察数据从未传入右侧面板，摘要下段恒显「暂未登记待办」）
  const { task: trackedTask, loading: taskLoading, orchestratorTodos, insight } =
    useTaskPlanTracking(activeTaskId, resumeKey);
  // ADR-067 待办先行：任务创建前（无 ?task=）待办挂构建会话，按会话维度轮询；
  // 任务创建成功后待办自动迁移归属任务，改走任务维度轮询（上方 useTaskPlanTracking）
  const sessionTodos = useSessionOrchestratorTodos(
    activeTaskId ? undefined : currentSessionId,
  );
  // ADR-066 唤醒可视化（2026-10-07 用户裁决）：任务终态后中枢自动唤醒轮在
  // 后台执行并落库，本同步把唤醒触发消息与 AI 回复（含步骤卡）实时追加到
  // 对话区——像正常对话一样呈现中枢被唤醒后继续工作的过程
  useWakeMessagesSync({
    sessionId: trackedTask?.session_id ?? currentSessionId,
    enabled: Boolean(
      trackedTask
        && (trackedTask.status === 'completed' || trackedTask.status === 'failed')
        && !sending,
    ),
    localMessages: messages,
    onAppend: (newMessages) => {
      for (const msg of newMessages) {
        if (msg.role === 'user') {
          appendUserMessage(msg);
        } else if (msg.role === 'assistant') {
          appendAssistantMessage(msg);
        }
      }
    },
  });
  // 唤醒轮 SSE 实时订阅（同上裁决）：直接订阅中枢广播的编排事件流，
  // thinking/chunk/step 逐字实时渲染（与正常对话同一套渲染逻辑）；
  // 轮询同步兜底补触发消息与漏收的历史。
  useWakeStream({
    sessionId: trackedTask?.session_id ?? currentSessionId,
    enabled: Boolean(
      trackedTask
        && (trackedTask.status === 'completed' || trackedTask.status === 'failed')
        && !sending,
    ),
    onEvent: applyChatStreamEvent,
  });

  // 中枢待办对齐（ADR-061 M5d）：todo_write 写入的任务与会话绑定任务可能
  // 随多次 create_task 漂移——成功写入后把 ?task= 对齐到实际写入的任务，
  // 右侧任务摘要下段（中枢待办）立即跟踪正确对象，不再显示空清单。
  useEffect(() => {
    if (!lastTodoTaskId || lastTodoTaskId === activeTaskId) {
      return;
    }
    setSearchParams(patchSearchParams((next) => next.set('task', lastTodoTaskId)), {
      replace: true,
    });
  }, [lastTodoTaskId, activeTaskId, setSearchParams]);

  // 节点 Agent 会话轨迹（ADR-063 M3）：任一折叠块展开才开启实时轮询（收起即停）；
  // 行按 node_id 分组渲染成群聊式折叠块，来源与中枢编排 agent 明确区分。
  const [tracePollActive, setTracePollActive] = useState(false);
  // 空结果低频重拉开关（2026-10-06 修⑤死锁）：任务未终态时为 true——任务刚
  // 派发首轮回看为空、折叠块不渲染无法展开，需靠重拉等首行出现。终态判定
  // 与 useTaskPlanTracking 的 isTerminal 对齐（failed/completed/review 即停）。
  const traceWaitActive =
    Boolean(activeTaskId) &&
    (trackedTask === undefined ||
      (trackedTask.status !== 'completed' &&
        trackedTask.status !== 'failed' &&
        trackedTask.status !== 'review'));
  const { rows: traceRows, loadingHistory: traceLoading } = useTaskAgentTrace(
    activeTaskId,
    tracePollActive,
    traceWaitActive,
  );
  const nodeTraceGroups = useMemo(() => {
    const grouped = new Map<string, NodeTraceGroup>();
    for (const row of traceRows) {
      const key = row.node_id ?? 'unknown';
      const group = grouped.get(key) ?? { nodeId: row.node_id ?? null, rows: [] };
      group.rows.push(row);
      grouped.set(key, group);
    }
    return [...grouped.values()];
  }, [traceRows]);

  // `@节点`（ADR-063 M4）：候选 = 任务链上已派发/指定节点；D4 双语义——
  // 任务执行中 @ → steer 补充发言（不中断）；空闲 @ → node-chat 续聊对话
  // （ADR-064：不再建任务，中枢落库用户消息并返回 reply_to）
  const [mentionNode, setMentionNode] = useState<NodeMentionCandidate | null>(null);
  // 节点续聊回复等待（ADR-064 M3）：202 的 reply_to 目标列表；轮询见到
  // 会话 messages 中对应 assistant 消息即回流渲染（节点徽标），超时移除
  const [pendingReplies, setPendingReplies] = useState<PendingNodeReply[]>([]);
  useNodeChatReplyPoll({
    // 落库目标是任务关联构建会话；缺失时回退当前会话（无法定位则轮询静默不启动）
    sessionId: trackedTask?.session_id ?? currentSessionId,
    pending: pendingReplies,
    onReply: (replyTo, reply) => {
      setPendingReplies((prev) => prev.filter((item) => item.replyTo !== replyTo));
      appendAssistantMessage(reply);
    },
    onExpire: (replyTo) => {
      // 320s 未回（节点超时兜底已过）：停止关注；消息刷新后可从会话恢复
      setPendingReplies((prev) => prev.filter((item) => item.replyTo !== replyTo));
    },
  });
  const mentionCandidates = useMemo<NodeMentionCandidate[]>(() => {
    const candidates = new Map<string, NodeMentionCandidate>();
    if (trackedTask?.assigned_node_id && trackedTask.assigned_node_name) {
      candidates.set(trackedTask.assigned_node_id, {
        id: trackedTask.assigned_node_id,
        name: trackedTask.assigned_node_name,
      });
    }
    if (trackedTask?.target_node_id && trackedTask.target_node_name) {
      candidates.set(trackedTask.target_node_id, {
        id: trackedTask.target_node_id,
        name: trackedTask.target_node_name,
      });
    }
    return [...candidates.values()];
  }, [trackedTask]);

  /** 打开工具：已打开则仅激活，否则追加并激活（内部仅函数式更新，引用稳定）。 */
  const openTool = useCallback((key: ToolKey) => {
    setOpenTools((prev) => (prev.includes(key) ? prev : [...prev, key]));
    setActiveTool(key);
  }, []);

  /** 关闭工具：关闭激活工具时激活相邻标签，全部关闭回到空态。 */
  const closeTool = (key: ToolKey) => {
    const index = openTools.indexOf(key);
    const next = openTools.filter((tool) => tool !== key);
    setOpenTools(next);
    if (activeTool === key) {
      setActiveTool(next.length === 0 ? null : (next[Math.min(index, next.length - 1)] ?? null));
    }
  };

  // URL 携带 ?task= 直接进入跟踪态时，自动打开「方案」工具展示审批闭环。
  // 采用渲染期状态调整模式（React 官方推荐），避免 effect 内同步 setState；
  // lastTrackedTaskId 初始为 undefined 以保证首次挂载（含直接带参打开）也能触发。
  const [lastTrackedTaskId, setLastTrackedTaskId] = useState<string | undefined>(undefined);
  if (activeTaskId !== lastTrackedTaskId) {
    setLastTrackedTaskId(activeTaskId);
    // 切换跟踪任务时清掉上一任务的 @ 节点选择与回复等待（候选随之刷新）
    setMentionNode(null);
    setPendingReplies([]);
    if (activeTaskId) {
      openTool('plan');
    }
  }

  // 会话恢复/继续/新建/删除动作（含 URL 参数切换）已拆至 useTaskSessionActions；
  // 会话为自动实时保存（首轮发送时 ensureSession 自动创建，后端每轮流结束持久化）
  const {
    restoreSession,
    handleContinueSession,
    handleNewSession,
    handleDeleteSession,
  } = useTaskSessionActions({
    message,
    reset,
    setMode,
    setModel,
    removeSession,
    currentSessionId,
    onSessionIdChange: setCurrentSessionId,
    onEnterTracking: () => {
      openTool('plan');
      setResumeKey((key) => key + 1);
    },
    onSpecTab: () => openTool('spec'),
    onCloseDrawer: () => setDrawerOpen(false),
  });

  /**
   * 恢复会话时验证模型是否仍存在（Bug 修复：历史会话的模型被删除后，
   * 不保留该模型，回退到当前配置的活动模型）。
   */
  const validateAndRestoreSession = useCallback(
    (session: TaskBuildingSession) => {
      restoreSession(session);
      // 异步校验：若会话的模型在可用列表中不存在，回退到配置的当前模型
      if (session.model) {
        fetchLLMModels()
          .then((models) => {
            const exists = models.some(
              (m) => m.model === session.model,
            );
            if (!exists) {
              // 模型已删除：从配置拉取当前活动模型
              return fetchLLMProviderConfig().then((config) => {
                if (config.model) {
                  setModel(config.model);
                }
              });
            }
            return undefined;
          })
          .catch(() => {
            // 拉取失败静默，保持当前 model 状态
          });
      }
    },
    [restoreSession, setModel],
  );

  /** 抽屉继续按钮：恢复会话（先拉详情）+ 校验模型是否仍可用。 */
  const handleContinueSessionWithValidation = useCallback(
    (session: TaskBuildingSessionSummary) => {
      void handleContinueSession(session).then(() => {
        if (session.model) {
          fetchLLMModels()
            .then((models) => {
              if (!models.some((m) => m.model === session.model)) {
                return fetchLLMProviderConfig().then((config) => {
                  if (config.model) setModel(config.model);
                });
              }
            })
            .catch(() => {});
        }
      });
    },
    [handleContinueSession, setModel],
  );

  // 父任务上下文：?parent= 进入时按其关联会话恢复历史对话与三件套
  // （恢复副作用内聚在 Hook 中，返回的父任务详情当前无 UI 消费）；
  // 使用 validateAndRestoreSession 确保已删除的模型不回带到当前对话
  useParentTaskContext(parentTaskId, validateAndRestoreSession);

  // ?session= 直链恢复（ADR-064 E2E 循环补回归）：外部/历史链接带会话 ID
  // 打开时自动拉取详情恢复消息与三件套，否则直链只显示空白欢迎态
  useSessionParamRestore(
    searchParams.get('session') ?? undefined,
    validateAndRestoreSession,
  );

  /** 当前工作目录下的任务 ID 集合：历史会话抽屉据此过滤（ADR-028） */
  const currentDirTaskIds = useMemo(() => {
    if (!workspaceDir.trim()) {
      return undefined;
    }
    const ids = workspaceTasks
      .filter((task) => task.output_dir === workspaceDir.trim())
      .map((task) => task.id);
    return new Set(ids);
  }, [workspaceDir, workspaceTasks]);

  /**
   * 页面加载时获取当前 LLM 模型配置，用于顶部模型展示和会话初始化。
   */
  useEffect(() => {
    let cancelled = false;
    fetchLLMProviderConfig()
      .then((config) => {
        if (!cancelled && config.model) {
          setModel(config.model);
        }
      })
      .catch(() => {
        // 配置获取失败时保持空状态，由 UI 展示“未连接”
      });
    return () => {
      cancelled = true;
    };
  }, [setModel]);

  /**
   * 页面加载时拉取任务列表：用于工作目录历史下拉与历史会话过滤（ADR-028）。
   */
  useEffect(() => {
    let cancelled = false;
    fetchTasks({ limit: 100 })
      .then((list) => {
        if (!cancelled) {
          setWorkspaceTasks(
            list.items.map((task) => ({
              id: task.id,
              title: task.title,
              output_dir: task.output_dir ?? null,
            })),
          );
        }
      })
      .catch(() => {
        // 列表加载失败时下拉仅显示空选项，不阻断主流程
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /**
   * 确保当前对话已关联会话，首次发送时自动创建。
   */
  const ensureSession = async (content: string): Promise<string | undefined> => {
    if (currentSessionId) {
      return currentSessionId;
    }
    if (messages.length === 0) {
      const title = content.slice(0, 50);
      const session = await createSession(title, [], specification, mode, model);
      setCurrentSessionId(session.id);
      return session.id;
    }
    return undefined;
  };

  /** 移除待发送附件（同时释放本地预览地址） */
  const handleRemoveAttachment = useCallback((id: string) => {
    setPendingAttachments((prev) => {
      const target = prev.find((item) => item.id === id);
      if (target?.preview_url?.startsWith('blob:')) {
        URL.revokeObjectURL(target.preview_url);
      }
      return prev.filter((item) => item.id !== id);
    });
  }, []);

  /** 取消上传中的附件：中止请求、清理占位与本地预览 */
  const handleCancelUploading = useCallback((key: string) => {
    uploadControllersRef.current.get(key)?.abort();
    uploadControllersRef.current.delete(key);
    setUploadingList((prev) => {
      const target = prev.find((item) => item.key === key);
      if (target?.preview_url?.startsWith('blob:')) {
        URL.revokeObjectURL(target.preview_url);
      }
      return prev.filter((item) => item.key !== key);
    });
  }, []);

  /**
   * 选择/粘贴图片与 zip（ADR-039）：先确保会话存在（附件归属会话），
   * 再逐个上传；输入框内实时展示进度（大文件不再像卡死），支持取消。
   */
  const handleFilesPicked = useCallback(
    async (files: File[]) => {
      if (files.length === 0) {
        return;
      }
      try {
        const sessionId = await ensureSession('附件上传');
        if (!sessionId) {
          message.warning('会话未就绪，无法上传附件');
          return;
        }
        for (const file of files) {
          const isImage = file.type.startsWith('image/');
          const previewUrl = isImage ? URL.createObjectURL(file) : undefined;
          const key = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
          const controller = new AbortController();
          uploadControllersRef.current.set(key, controller);
          setUploadingList((prev) => [
            ...prev,
            {
              key,
              file_name: file.name,
              mime: file.type || 'application/octet-stream',
              size: file.size,
              preview_url: previewUrl,
              percent: 0,
            },
          ]);
          try {
            const uploaded = await uploadSessionAttachment(sessionId, file, {
              onProgress: (percent) =>
                setUploadingList((prev) =>
                  prev.map((item) => (item.key === key ? { ...item, percent } : item)),
                ),
              signal: controller.signal,
            });
            setPendingAttachments((prev) => [
              ...prev,
              {
                id: uploaded.id,
                file_name: uploaded.file_name,
                mime: uploaded.mime,
                size: uploaded.size,
                preview_url: previewUrl,
              },
            ]);
          } catch (err) {
            if (previewUrl) {
              URL.revokeObjectURL(previewUrl);
            }
            if (controller.signal.aborted) {
              return; // 用户主动取消：不再继续上传后续文件
            }
            message.error(
              err instanceof Error ? err.message : `附件 ${file.name} 上传失败`,
            );
          } finally {
            uploadControllersRef.current.delete(key);
            setUploadingList((prev) => prev.filter((item) => item.key !== key));
          }
        }
      } catch {
        // 会话创建失败提示由 createSession 上抛处理
      }
    },
    // ensureSession 依赖会话状态；此处沿用渲染时快照（input 场景固定）
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentSessionId, messages.length, specification, mode, model],
  );

  /**
   * 发送用户消息并请求 AI 流式回复。
   *
   * 对话结束后同步会话详情：AI 智能派发成功时技能会把 task_id 回写会话
   * （ADR-033），发现新的关联任务即写入 URL 进入跟踪态，右侧「产物」/
   * 「任务摘要」/「方案」Tab 随之关联该任务（无需手动创建任务）。
   */
  const handleSend = async () => {
    const content = input.trim();
    if (!content || uploadingList.length > 0) {
      return;
    }
    // `@节点`（ADR-063 M4 / ADR-064 M3）：消息定向发给节点 agent，不走中枢编排流。
    // 执行中 → steer 补充发言（不中断任务）；空闲 → node-chat 续聊对话
    // （用户消息由中枢落库，202 返回 reply_to，节点回复经轮询回流）。
    if (mentionNode && activeTaskId) {
      const running =
        trackedTask?.status === 'running' || trackedTask?.status === 'assigned';
      const target = mentionNode;
      setInput('');
      setMentionNode(null);
      const userMessage: ChatMessage = { role: 'user', content, mentionNode: target.name };
      appendUserMessage(userMessage);
      try {
        if (running) {
          await steerTask(activeTaskId, content);
          message.success(`已发送给节点「${target.name}」（不中断任务）`);
        } else {
          const result = await nodeChatTask(activeTaskId, target.id, content);
          // reply_to 与该条用户消息建立关联（ADR-064 M3），并登记轮询目标
          markMessageReplyTo(userMessage, result.reply_to);
          setPendingReplies((prev) => [
            ...prev,
            { replyTo: result.reply_to, startedAt: Date.now() },
          ]);
          message.success(`已发送给节点「${target.name}」，等待节点回复`);
        }
      } catch (err) {
        // 错误可见（409 提示走 steer / 422 节点离线 / 404 任务不存在）：
        // 用户消息本地保留并标记发送失败，toast 明确展示后端原因
        markMessageFailed(userMessage);
        message.error(err instanceof Error ? err.message : '@节点发送失败');
      }
      return;
    }
    if (sending) {
      // Steering（ADR-045）：回复进行中发送 = 插话投递。
      // 无活跃流竞态（投递瞬间流结束）时降级为普通发送。
      if (!currentSessionId) {
        return;
      }
      setInput('');
      const accepted = await steer(content, currentSessionId);
      if (!accepted) {
        try {
          await sendMessage(content, currentSessionId, {
            parent_task_id: parentTaskId,
            output_dir: workspaceDir.trim() || undefined,
          });
        } catch (err) {
          message.error(err instanceof Error ? err.message : '发送失败');
        }
      }
      return;
    }
    if (!content && pendingAttachments.length === 0) {
      return;
    }
    setInput('');

    try {
      const sessionId = await ensureSession(content);
      await sendMessage(
        content,
        sessionId,
        {
          parent_task_id: parentTaskId,
          output_dir: workspaceDir.trim() || undefined,
        },
        pendingAttachments.length > 0 ? pendingAttachments : undefined,
      );
      setPendingAttachments([]);
      if (sessionId && !activeTaskId) {
        const detail = await fetchTaskBuildingSessionById(sessionId).catch(() => null);
        if (detail?.task_id) {
          setSearchParams(
            patchSearchParams((next) => next.set('task', detail.task_id as string)),
            { replace: true },
          );
          setResumeKey((key) => key + 1);
        }
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '发送失败');
    }
  };

  /**
   * 以第一条用户消息作为需求标题，提交三件套创建任务。
   *
   * 创建成功后不跳转任务队列：URL 记录 ?task=<id> 进入跟踪态，
   * 「方案」Tab 轮询展示方案审批闭环（见 ADR-012 增补决策）。
   */
  const handleCreate = async () => {
    const requirement = messages.find((m) => m.role === 'user')?.content ?? '';
    setCreating(true);
    try {
      const created = await createTask({
        requirement,
        specification,
        session_id: currentSessionId,
        execution_mode: executionMode,
        parent_task_id: parentTaskId,
        // 同目录任务锁定（ADR-028）：与历史任务同目录即在同一工作空间执行
        output_dir: workspaceDir.trim() || undefined,
        // 子 Agent 执行模型（ADR-029/030）：hub 为缺省语义不传，custom 显式下发
        executor_model:
          executorModel.source === 'hub' ? undefined : executorModel,
      });
      setSearchParams(
        patchSearchParams((next) => next.set('task', created.id)),
        { replace: true },
      );
      openTool('plan');
      setResumeKey((key) => key + 1);
      if (currentSessionId) {
        void updateSession(currentSessionId, { taskId: created.id });
      }
      message.success('任务创建成功，已加入任务队列');
    } catch (err) {
      message.error(err instanceof Error ? err.message : '任务创建失败');
    } finally {
      setCreating(false);
    }
  };

  // 方案审批闭环动作（批准/驳回/转迭代）已拆至 usePlanReviewFlow
  const { reviewing, handleApprovePlan, handleRejectPlan, handleContinueIteration } =
    usePlanReviewFlow({
      activeTaskId,
      trackedTask,
      message,
      onResume: () => setResumeKey((key) => key + 1),
    });

  const handleDocChange = (key: keyof TaskSpecification, value: string) => {
    setSpecification({ ...specification, [key]: value });
  };

  return (
    <div className="task-new">
      {/* 工具面板收起/展开切换（右上角，顶栏原头像位置） */}
      <Tooltip title={rightCollapsed ? '展开工具面板' : '收起工具面板'} placement="bottom">
        <Button
          type="text"
          icon={
            rightCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />
          }
          onClick={() => setRightCollapsed((v) => !v)}
          className="task-new__top-toggle"
          aria-label="切换工具面板"
        />
      </Tooltip>
      <div className={`task-new__content${rightCollapsed ? ' task-new__content--right-collapsed' : ''}${isResizing ? ' task-new__content--resizing' : ''}`}>
        <TaskNewChatPanel
          messages={messages}
          input={input}
          sending={sending}
          mode={mode}
          stageText={stageText}
          onInputChange={setInput}
          onSend={() => void handleSend()}
          onOpenSessions={() => {
            setDrawerOpen(true);
            void refresh();
          }}
          sessionId={currentSessionId}
          pendingAttachments={pendingAttachments}
          uploadingList={uploadingList}
          onRemoveAttachment={handleRemoveAttachment}
          onCancelUploading={handleCancelUploading}
          onFilesPicked={(files) => void handleFilesPicked(files)}
          approvalRequest={chatState.approvalRequest}
          nodeTraceGroups={nodeTraceGroups}
          nodeTraceLoading={traceLoading}
          nodeTraceTaskName={trackedTask?.title}
          nodeTraceProgress={trackedTask?.progress}
          onNodeTraceExpandChange={setTracePollActive}
          mentionCandidates={mentionCandidates}
          mentionNode={mentionNode}
          onMentionPick={setMentionNode}
          onMentionClear={() => setMentionNode(null)}
          onApprovalDecide={(approve) => {
            const request = chatState.approvalRequest;
            if (request) {
              void decideTerminalApproval(request.approval_id, approve).catch(() => {
                message.error('审批操作失败');
              });
            }
            chatActions.setApprovalRequest(null);
          }}
          footer={
            <TaskNewInputBar
              model={model}
              mode={mode}
              onModeChange={setMode}
              executionMode={executionMode}
              onExecutionModeChange={setExecutionMode}
              executorModel={executorModel}
              contextUsage={contextUsage}
              compacting={compacting}
              onExecutorModelChange={setExecutorModel}
              onChatModelActivated={setModel}
              inputLength={input.length}
              sending={sending}
              canSend={
                (Boolean(input.trim()) || pendingAttachments.length > 0) &&
                uploadingList.length === 0
              }
              onSend={() => void handleSend()}
              onStop={stop}
            />
          }
        />
        <div
          className={`task-new__resizer${isResizing ? ' task-new__resizer--active' : ''}`}
          onMouseDown={handleResizeStart}
          title="拖动调整宽度"
        />
        <TaskNewRightPanel
          collapsed={rightCollapsed}
          width={rightWidth}
          onExpand={() => setRightCollapsed(false)}
          onCollapse={() => setRightCollapsed(true)}
          openTools={openTools}
          activeTool={activeTool}
          onOpenTool={openTool}
          onActivateTool={setActiveTool}
          onCloseTool={closeTool}
          workspaceDir={workspaceDir}
          onWorkspaceDirChange={setWorkspaceDir}
          workspaceDirDisabled={Boolean(parentTaskId)}
          trackedTask={trackedTask}
          taskLoading={taskLoading}
          orchestratorTodos={activeTaskId ? orchestratorTodos : sessionTodos}
          insight={insight}
          specification={specification}
          isReady={isReady}
          creating={creating}
          reviewing={reviewing}
          onDocChange={handleDocChange}
          onCreate={() => void handleCreate()}
          onApprovePlan={() => void handleApprovePlan()}
          onRejectPlan={(feedback) => void handleRejectPlan(feedback)}
          onContinueIteration={handleContinueIteration}
          onCancelTask={async () => {
            if (!trackedTask) {
              return;
            }
            try {
              const result = await cancelTask(trackedTask.id);
              message.success(
                result.node_notified
                  ? '已发送中断指令，节点将停止开发'
                  : '任务已标记取消（节点离线，中断指令未送达）',
              );
            } catch (err) {
              message.error(err instanceof Error ? err.message : '取消失败');
            }
          }}
        />
      </div>

      <TaskSessionDrawer
        open={drawerOpen}
        sessions={sessions}
        loading={sessionsLoading}
        filterTaskIds={currentDirTaskIds}
        onClose={() => setDrawerOpen(false)}
        onRefresh={() => void refresh()}
        onNewSession={handleNewSession}
        onContinue={handleContinueSessionWithValidation}
        onDelete={(session) => void handleDeleteSession(session)}
      />

      <Modal
        title="对话失败"
        open={Boolean(error)}
        onOk={clearError}
        onCancel={clearError}
        okText="知道了"
        cancelButtonProps={{ style: { display: 'none' } }}
      >
        {error}
      </Modal>
    </div>
  );
}
