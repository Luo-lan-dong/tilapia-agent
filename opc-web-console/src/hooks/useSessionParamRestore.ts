import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { fetchTaskBuildingSessionById } from '@/api/taskBuildingSessions';
import type { TaskBuildingSession } from '@/types/taskBuilding';
import { patchSearchParams } from '@/utils/searchParams';

/**
 * `?session=<id>` 直链恢复（ADR-064 E2E 循环补回归）：
 * 从外部入口/历史会话链接带会话 ID 打开构建页时，自动拉取会话详情并恢复
 * 历史消息与三件套——否则直链打开只显示空白欢迎态（2026-10-05 浏览器实测
 * 发现该逻辑在重构中丢失，ADR-061 曾实现的 ?session= 挂载恢复回归）。
 *
 * 会话已关联任务（task_id）时自动写入 ?task= 建立跟踪态（产物/摘要/方案
 * Tab 数据源），与 useParentTaskContext 的 ?parent= 行为一致。
 * 同一 sessionId 只恢复一次，避免 StrictMode 双执行覆盖用户后续编辑。
 */
export function useSessionParamRestore(
  sessionId: string | undefined,
  onRestoreSession: (session: TaskBuildingSession) => void,
): void {
  const restoreRef = useRef(onRestoreSession);
  const restoredRef = useRef<string>();
  const [, setSearchParams] = useSearchParams();

  useEffect(() => {
    restoreRef.current = onRestoreSession;
  }, [onRestoreSession]);

  useEffect(() => {
    if (!sessionId) {
      restoredRef.current = undefined;
      return;
    }
    if (restoredRef.current === sessionId) {
      return;
    }

    let cancelled = false;
    const load = async () => {
      try {
        const session = await fetchTaskBuildingSessionById(sessionId);
        if (cancelled || !session || session.messages.length === 0) {
          return;
        }
        restoredRef.current = sessionId;
        restoreRef.current(session);
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
      } catch {
        // 拉取失败保持现状（空白会话），不打断用户
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [sessionId, setSearchParams]);
}
