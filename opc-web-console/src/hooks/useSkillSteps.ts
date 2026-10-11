import { useCallback } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { pickNumber, pickString } from '@/api/taskBuilding';
import type {
  ChatMessage,
  SkillStep,
  TaskBuildStreamEvent,
} from '@/types/taskBuilding';

/**
 * 技能执行步骤状态管理（ADR-033 执行链路可视化）。
 *
 * 从 useTaskBuildingChat 拆出（hook 文件 200 行上限，coding-standards 4.3）：
 * 处理 SSE ``step_started`` / ``step_finished`` 事件并维护消息内步骤列表。
 */
export function useSkillSteps(
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>,
): (event: TaskBuildStreamEvent) => void {
  return useCallback(
    (event: TaskBuildStreamEvent) => {
      if (event.event === 'step_started') {
        const stepId = pickString(event.data, 'step_id');
        if (!stepId) {
          return;
        }
        const skillName = pickString(event.data, 'skill_name') ?? '';
        const step: SkillStep = {
          step_id: stepId,
          seq: pickNumber(event.data, 'seq') ?? 0,
          skill_name: skillName,
          skill_display_name: pickString(event.data, 'skill_display_name') ?? skillName,
          args_summary: pickString(event.data, 'args_summary'),
          status: 'running',
        };
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'assistant') {
            // 回合分割：正文已开始，或上一条消息的步骤均已结束（无 running），
            // 说明 Orchestrator 开启了新一轮循环——新建 assistant 消息承载
            // 本轮步骤；同轮并行多工具（前一步骤仍 running）继续追加同消息，
            // 保持"一轮思考 + 该轮多工具"成组。
            if (
              last.content ||
              (last.steps && last.steps.some((s) => s.status !== 'running'))
            ) {
              return [...prev, { role: 'assistant', content: '', steps: [step] }];
            }
            return [...prev.slice(0, -1), { ...last, steps: [...(last.steps ?? []), step] }];
          }
          return [...prev, { role: 'assistant', content: '', steps: [step] }];
        });
        return;
      }
      if (event.event === 'step_finished') {
        const stepId = pickString(event.data, 'step_id');
        if (!stepId) {
          return;
        }
        // 全局按 step_id 查找更新（问题修复）：回合分割会把步骤卡留在较早的
        // 消息里，若只更新最后一条消息，分割后到达的 step_finished 会丢失，
        // 步骤卡永远停留在"执行中"。
        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.role !== 'assistant' || !msg.steps) {
              return msg;
            }
            if (!msg.steps.some((step) => step.step_id === stepId)) {
              return msg;
            }
            return {
              ...msg,
              steps: msg.steps.map((step) =>
                step.step_id === stepId
                  ? {
                      ...step,
                      status: pickString(event.data, 'status') === 'success' ? 'success' : 'failed',
                      result_summary: pickString(event.data, 'result_summary'),
                      duration_ms: pickNumber(event.data, 'duration_ms'),
                    }
                  : step,
              ),
            };
          }),
        );
      }
    },
    [setMessages],
  );
}
