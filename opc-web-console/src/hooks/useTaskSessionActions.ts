import { useCallback } from 'react';
import { App } from 'antd';
import { useSearchParams } from 'react-router-dom';
import { patchSearchParams } from '@/utils/searchParams';
import { fetchTaskBuildingSessionById } from '@/api/taskBuildingSessions';
import type { ChatMessage } from '@/types/taskBuilding';
import type { TaskBuildMode } from '@/types/taskBuilding';
import type {
  TaskBuildingSession,
  TaskBuildingSessionSummary,
  TaskSpecification,
} from '@/types/taskBuilding';

/** antd message 实例类型（App.useApp() 消费，避免静态方法警告） */
type MessageInstance = ReturnType<typeof App.useApp>['message'];

interface TaskSessionActionsParams {
  message: MessageInstance;
  /** chatActions.reset（第三参 sessionId：恢复上下文占用，ADR-041） */
  reset: (
    initialMessages?: ChatMessage[],
    initialSpecification?: TaskSpecification,
    sessionId?: string,
  ) => void;
  /** chatActions.setMode / setModel */
  setMode: (mode: TaskBuildMode) => void;
  setModel: (model: string) => void;
  /** sessionActions */
  removeSession: (id: string) => Promise<void>;
  /** 当前关联会话 ID 与更新回调 */
  currentSessionId: string | undefined;
  onSessionIdChange: (id: string | undefined) => void;
  /** 会话关联任务时进入方案跟踪态（切 Tab + 恢复轮询） */
  onEnterTracking: () => void;
  /** 新建会话时回到三件套 Tab */
  onSpecTab: () => void;
  onCloseDrawer: () => void;
}

interface TaskSessionActionsResult {
  /** 恢复构建会话到页面（历史消息 + 三件套 + 对话模式） */
  restoreSession: (session: TaskBuildingSession) => void;
  /** 加载历史会话（先拉详情再恢复；含方案跟踪态同步） */
  handleContinueSession: (session: TaskBuildingSessionSummary) => Promise<void>;
  /** 清空当前状态开始新会话 */
  handleNewSession: () => void;
  /** 删除历史会话 */
  handleDeleteSession: (session: TaskBuildingSessionSummary) => Promise<void>;
}

/**
 * 任务构建会话管理动作。
 *
 * 从 TaskNew 页面拆出：会话的恢复/继续/新建/删除/保存五个动作，
 * URL 参数（task/parent）切换内聚在本 Hook 中；聊天发送链路
 * （ensureSession/handleSend）仍留在页面主文件。
 */
export function useTaskSessionActions({
  message,
  reset,
  setMode,
  setModel,
  removeSession,
  currentSessionId,
  onSessionIdChange,
  onEnterTracking,
  onSpecTab,
  onCloseDrawer,
}: TaskSessionActionsParams): TaskSessionActionsResult {
  const [, setSearchParams] = useSearchParams();

  /**
   * 恢复构建会话到页面（历史消息 + 三件套 + 对话模式 + 上下文占用）。
   */
  const restoreSession = useCallback(
    (session: TaskBuildingSession) => {
      // 第三参传会话 ID：从 sessionStorage 恢复上下文占用圆环（ADR-041）
      reset(session.messages, session.specification ?? undefined, session.id);
      onSessionIdChange(session.id);
      if (session.mode) {
        setMode(session.mode);
      }
      if (session.model) {
        setModel(session.model);
      }
    },
    [reset, onSessionIdChange, setMode, setModel],
  );

  /**
   * 加载历史会话到当前页面。
   *
   * 列表项为轻量摘要（不含 messages / specification），先拉详情接口
   * 取完整数据再恢复；失败时提示且不动当前页面。
   *
   * 若该会话已关联创建过的任务（task_id），同步恢复方案跟踪态
   * （URL 记录 ?task=<id> 并切换到方案 Tab），否则清除 task 参数。
   */
  const handleContinueSession = useCallback(
    async (summary: TaskBuildingSessionSummary) => {
      let session: TaskBuildingSession;
      try {
        session = await fetchTaskBuildingSessionById(summary.id);
      } catch {
        message.error('加载会话详情失败，请重试');
        return;
      }
      restoreSession(session);
      setSearchParams(
        patchSearchParams((next) => {
          if (session.task_id) {
            next.set('task', session.task_id);
          } else {
            next.delete('task');
          }
        }),
        { replace: true },
      );
      if (session.task_id) {
        onEnterTracking();
      }
      onCloseDrawer();
      message.success('已加载历史会话');
    },
    [restoreSession, setSearchParams, onEnterTracking, onCloseDrawer, message],
  );

  /**
   * 清空当前状态，开始新会话。
   *
   * 同时清除 URL 中的 parent/task 迭代参数，避免从历史会话点击
   * 新建对话后仍停留在任务迭代模式。
   */
  const handleNewSession = useCallback(() => {
    reset();
    onSessionIdChange(undefined);
    onSpecTab();
    setSearchParams(
      patchSearchParams((next) => {
        next.delete('parent');
        next.delete('task');
      }),
      { replace: true },
    );
    onCloseDrawer();
  }, [reset, onSessionIdChange, onSpecTab, setSearchParams, onCloseDrawer]);

  /**
   * 删除历史会话。
   */
  const handleDeleteSession = useCallback(
    async (session: TaskBuildingSessionSummary) => {
      try {
        await removeSession(session.id);
        if (currentSessionId === session.id) {
          reset();
          onSessionIdChange(undefined);
        }
        message.success('会话已删除');
      } catch (err) {
        message.error(err instanceof Error ? err.message : '删除会话失败');
      }
    },
    [removeSession, currentSessionId, reset, onSessionIdChange, message],
  );

  return {
    restoreSession,
    handleContinueSession,
    handleNewSession,
    handleDeleteSession,
  };
}
