"""任务查询类技能：任务状态 / 队列概况（ADR-033）。"""

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus
from app.skills.base import Skill, SkillContext, SkillResult

_VALID_STATUSES = [s.value for s in TaskStatus]


class GetTaskStatusSkill(Skill):
    """查询任务详情或任务队列概况。"""

    name = "get_task_status"
    display_name = "查询任务状态"
    description = (
        "查询任务执行状态。传入 task_id 查询单个任务详情；不传时返回任务队列"
        "概况（各状态数量与待处理任务列表）。用户询问任务进度、队列情况时调用。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "可选，任务 UUID"},
            "status": {
                "type": "string",
                "enum": _VALID_STATUSES,
                "description": "可选，按状态过滤队列列表",
            },
        },
    }
    returns = "任务详情（标题/状态/进度/节点/时间）或队列概况（各状态计数 + 任务列表）"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """查询任务详情或队列概况并产出结果事件。"""
        task_id_raw = str(context.args.get("task_id") or "").strip()
        if task_id_raw:
            try:
                task = await session.get(Task, uuid.UUID(task_id_raw))
            except ValueError:
                task = None
            if task is None:
                yield {
                    "event": "_result",
                    "data": SkillResult(success=False, summary=f"任务 {task_id_raw} 不存在"),
                }
                return
            summary = _format_task(task)
            yield {
                "event": "_result",
                "data": SkillResult(success=True, summary=summary, data={"task_id": task_id_raw}),
            }
            return

        status_filter = str(context.args.get("status") or "").strip() or None
        counts_result = await session.execute(
            select(Task.status, func.count(Task.id)).group_by(Task.status)
        )
        counts = dict(counts_result.all())
        overview = (
            "任务队列概况：" + "，".join(f"{k} {v} 个" for k, v in counts.items()) or "队列为空"
        )

        query = select(Task).order_by(Task.created_at.desc()).limit(10)
        if status_filter:
            query = query.where(Task.status == status_filter)
        rows = list((await session.execute(query)).scalars().all())
        if rows:
            listing = "\n".join(_format_task(task) for task in rows)
            overview += f"\n最近任务：\n{listing}"
        yield {
            "event": "_result",
            "data": SkillResult(success=True, summary=overview, data={"counts": counts}),
        }


def _format_task(task: Task) -> str:
    """格式化单任务摘要文本。"""
    return (
        f"- [{task.status}] {task.title}（进度 {task.progress}%，"
        f"类型 {task.agent_type}，ID {task.id}）"
    )
