"""任务构建回合收集（ADR-033/ADR-040 流式落库支撑）。

从 ``app/api/v1/endpoints/task_building.py`` 抽出的纯逻辑单元，承载一次
任务构建对话内「思考 → 技能步骤」按 LLM 回合收集的状态机：

- 回合边界 = 工具轨迹事件（``_history_trajectory``），或当前回合已有步骤时
  收到新的思考增量（与前端回合分割语义一致）；
- 步骤按 ``step_id`` 建索引，``step_finished`` 到达时**跨回合**更新——
  修复「回合分割后结束事件丢失、步骤卡永远执行中」的问题；
- 落库前 :func:`finalize_running_steps` 把仍为执行中的步骤收尾为失败，
  覆盖流中断/结束事件丢失的历史会话。
"""

from __future__ import annotations

from typing import Any

#: 步骤仍在执行中时的落库收尾文案（流中断/未收到结束事件）
INTERRUPTED_STEP_RESULT = "流中断，未收到技能结束事件"


class BuildRoundCollector:
    """按 LLM 回合收集思考与技能步骤（供 SSE 流落库使用）。"""

    def __init__(self) -> None:
        self._rounds: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._steps_by_id: dict[str, dict[str, Any]] = {}

    @property
    def rounds(self) -> list[dict[str, Any]]:
        """已收集的回合列表（``[{thinking, steps}]``）。"""
        return self._rounds

    def add_thinking(self, delta: str) -> None:
        """追加思考增量；当前回合已有工具步骤时分割开启新回合。"""
        if not delta:
            return
        active = self._ensure_round()
        if active["steps"]:
            self._current = None
            active = self._ensure_round()
        active["thinking"] += delta

    def start_step(self, data: dict[str, Any]) -> None:
        """记录 ``step_started``：追加到当前回合并按 step_id 建索引。"""
        step = {
            "step_id": data.get("step_id"),
            "seq": data.get("seq"),
            "skill_name": data.get("skill_name"),
            "skill_display_name": data.get("skill_display_name"),
            "args_summary": data.get("args_summary"),
            "status": "running",
        }
        self._ensure_round()["steps"].append(step)
        step_id = step["step_id"]
        if step_id is not None:
            self._steps_by_id[str(step_id)] = step

    def finish_step(self, data: dict[str, Any]) -> None:
        """记录 ``step_finished``：跨回合按 step_id 更新（修复卡执行中）。"""
        step_id = data.get("step_id")
        if step_id is None:
            return
        step = self._steps_by_id.get(str(step_id)) or self._find_step(str(step_id))
        if step is None:
            return
        step["status"] = "success" if data.get("status") == "success" else "failed"
        step["result_summary"] = data.get("result_summary")
        step["duration_ms"] = data.get("duration_ms")

    def close_round(self) -> None:
        """闭合当前回合（``_history_trajectory`` 事件到达时调用）。"""
        self._current = None

    def _ensure_round(self) -> dict[str, Any]:
        """取当前回合（无则开启新回合）。"""
        if self._current is None:
            self._current = {"thinking": "", "steps": []}
            self._rounds.append(self._current)
        return self._current

    def _find_step(self, step_id: str) -> dict[str, Any] | None:
        """兜底查找：索引缺失时从全部回合倒序扫描。"""
        for round_ in reversed(self._rounds):
            for step in reversed(round_["steps"]):
                if str(step.get("step_id")) == step_id:
                    return step
        return None


def finalize_running_steps(messages: list[dict[str, Any]]) -> int:
    """落库前收尾：把仍为 ``running`` 的步骤标记为失败并写明原因。

    流中断、结束事件丢失、旧前端未回传结束状态时，历史会话恢复出的步骤卡
    会永远停留在「执行中」；落库前统一收尾保证持久化状态都是终态。

    Returns:
        被收尾的步骤数量。
    """
    finalized = 0
    for message in messages:
        steps = message.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict) and step.get("status") == "running":
                step["status"] = "failed"
                step["result_summary"] = step.get("result_summary") or INTERRUPTED_STEP_RESULT
                finalized += 1
    return finalized
