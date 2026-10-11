import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { fetchTaskById } from '@/api/tasks';
import { fetchTaskBuildingSessionById } from '@/api/taskBuildingSessions';
import type { TaskBuildingSession } from '@/types/taskBuilding';
import type { Task } from '@/types/task';
import { patchSearchParams } from '@/utils/searchParams';

export interface UseParentTaskContextResult {
  /** 父任务详情（含 change_summary）；拉取失败或无父任务时为 undefined */
  parentTask: Task | undefined;
}

/**
 * 迭代模式父任务上下文：`?parent=` 进入构建页时，自动拉取父任务详情，
 * 并按其关联的构建会话 ID 恢复历史对话与三件套（落实 ADR-012
 * 「迭代必须在中枢同一个任务构建会话中进行」的决策）。
 *
 * 恢复的会话若已关联任务（task_id，如 AI 智能派发的任务）且 URL 尚无
 * task 参数，自动写入 URL 建立跟踪态——中枢重启后回到该会话，
 * 右侧「产物」/「任务摘要」/「方案」Tab 立即关联上一轮任务。
 *
 * 父任务无关联会话（如 API 直接创建）时不恢复，保持空白新会话；
 * 同一 parentTaskId 只恢复一次，避免覆盖用户后续编辑。
 */
export function useParentTaskContext(
  parentTaskId: string | undefined,
  onRestoreSession: (session: TaskBuildingSession) => void,
): UseParentTaskContextResult {
  const [parentTask, setParentTask] = useState<Task>();
  const [prevParentTaskId, setPrevParentTaskId] = useState(parentTaskId);
  const restoreRef = useRef(onRestoreSession);
  const restoredParentRef = useRef<string>();
  const [, setSearchParams] = useSearchParams();

  // 渲染期状态调整（React 官方推荐模式）：parentTaskId 变化时同步重置，
  // 替代 effect 内同步 setState（react-hooks/set-state-in-effect 禁止）
  if (parentTaskId !== prevParentTaskId) {
    setPrevParentTaskId(parentTaskId);
    setParentTask(undefined);
  }

  useEffect(() => {
    restoreRef.current = onRestoreSession;
  }, [onRestoreSession]);

  useEffect(() => {
    if (!parentTaskId) {
      restoredParentRef.current = undefined;
      return;
    }

    // StrictMode 下 effect 双执行（setup → cleanup → setup）：首次请求会被
    // cleanup 取消，因此仅在数据成功落地后标记 ref；未落地前放行后续
    // setup 重试，避免恢复链路被静默丢弃。
    if (restoredParentRef.current === parentTaskId) {
      return;
    }

    let cancelled = false;
    const load = async () => {
      try {
        const task = await fetchTaskById(parentTaskId);
        if (cancelled) {
          return;
        }
        setParentTask(task);
        restoredParentRef.current = parentTaskId;
        if (!task.session_id) {
          return;
        }
        const session = await fetchTaskBuildingSessionById(task.session_id);
        if (cancelled && !(session && session.messages.length > 0)) {
          return;
        }
        if (session && session.messages.length > 0) {
          restoreRef.current(session);
          // 会话已关联任务且 URL 尚无跟踪参数：建立跟踪态（产物/摘要/方案 Tab 数据源）
          if (session.task_id) {
            setSearchParams(
              patchSearchParams((next) => {
                if (!next.get('task')) {
                  next.set('task', session.task_id as string);
                }
              }),
              { replace: true },
            );
          }
        }
      } catch {
        // 拉取失败保持现状（空白会话），不打断用户
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [parentTaskId, setSearchParams]);

  return { parentTask };
}
