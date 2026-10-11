"""中枢编排器待办技能 `todo_write`（ADR-061 M5d / ADR-067 待办先行）。

D5.5 的「任务摘要**下段** = 中枢编排器自己的待办」：编排器在对话里用本技能
维护**任务级**待办（与节点上报的 `tasks.todos` 物理分离），前端任务摘要下段
只读展示（Q5 裁决）。

Owner 维度（ADR-067 待办先行）：
- 会话已关联任务 → 挂任务（原语义）；
- **无任务时挂当前构建会话**——解决「待办依赖任务、任务依赖三件套」的鸡生蛋，
  收到执行型需求第一件事即可列出全计划；create_task 成功后由技能层把会话
  待办迁移到新任务（前端任务摘要立即可见同一清单）。
- 两者都缺失时明确失败，不静默写库。
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_building_session import TaskBuildingSession
from app.services import orchestrator_todo_service as todo_service
from app.skills.base import RESULT_EVENT, Skill, SkillContext, SkillResult

#: 需要 todo_id 的动作
_ID_ACTIONS = ("start", "complete", "update", "remove")


class OrchestratorTodoSkill(Skill):
    """维护中枢编排器自己的待办（任务摘要下段）。"""

    name = "todo_write"
    display_name = "维护中枢待办"
    description = (
        "维护**中枢编排器自己的**待办清单（显示在任务摘要下段，与 Agent 上报的待办分开）。"
        "收到执行型需求后**第一件事**就用 action=add 写入本轮编排全计划"
        "（如：生成三件套 → 创建任务 → 报告等待确认 → 派发 → 跟踪 → 验收）；"
        "**需求确认后计划细化/整体变更时必须用 action=reset 重建整份清单**"
        "（自动清掉旧清单再写入，禁止反复 add 叠加成两套混排）；"
        "每开始一项用 action=start、做完用 action=complete（都要带 todo_id）；"
        "内容需要修正用 action=update，不再需要该条用 action=remove；"
        "action=list 查看当前清单（含每项 id 与状态）。"
        "该清单只用于中枢侧跟踪与向用户展示，不会下发给节点执行；"
        "任务创建前挂在当前会话上，创建任务后自动迁移归属。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "reset", "start", "complete", "update", "remove", "list"],
                "description": (
                    "操作类型：add 追加待办；reset 清空并重建整份清单（计划细化/变更时用）；"
                    "start 标记进行中；complete 标记完成；"
                    "update 改内容；remove 删除；list 查看清单"
                ),
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "action=add/reset 时的待办内容列表（按顺序写入，单条 ≤500 字符，单清单上限 50 条；reset 为整份替换）",
            },
            "todo_id": {
                "type": "string",
                "description": "action=start/complete/update/remove 时的待办 ID（由 add 或 list 返回）",
            },
            "content": {
                "type": "string",
                "description": "action=update 时的新内容",
            },
        },
        "required": ["action"],
    }
    returns = "操作结果与更新后的待办清单（含每项 id/seq/status）"

    async def _resolve_owner(
        self, session: AsyncSession, context: SkillContext
    ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        """解析待办挂靠 owner：优先任务（会话绑定/父任务），无任务挂会话。

        Returns:
            ``(task_id, session_id)``——恰好其一非空；两者皆缺时抛错。
        """
        raw = context.session_id
        chat_session: TaskBuildingSession | None = None
        if raw:
            try:
                session_uuid = uuid.UUID(str(raw))
            except (TypeError, ValueError) as exc:
                raise todo_service.OrchestratorTodoError(
                    f"会话 ID 非法，无法定位待办归属：{raw}"
                ) from exc
            chat_session = await session.get(TaskBuildingSession, session_uuid)
            if chat_session is not None and chat_session.task_id is not None:
                return chat_session.task_id, None
        if context.parent_task_id:
            try:
                return uuid.UUID(str(context.parent_task_id)), None
            except (TypeError, ValueError) as exc:
                raise todo_service.OrchestratorTodoError(
                    f"parent_task_id 非法：{context.parent_task_id}"
                ) from exc
        if chat_session is not None:
            # ADR-067 待办先行：无任务时挂当前会话
            return None, chat_session.id
        raise todo_service.OrchestratorTodoError(
            "本会话尚未创建（无法定位待办归属）：请先发起对话再维护中枢待办。"
        )

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """执行待办操作，返回更新后的清单。"""
        action = str(context.args.get("action") or "").strip()
        task_id: uuid.UUID | None = None
        session_id: uuid.UUID | None = None
        try:
            task_id, session_id = await self._resolve_owner(session, context)
            if action == "add":
                await self._add(session, task_id, session_id, context)
            elif action == "reset":
                await self._reset(session, task_id, session_id, context)
            elif action == "list":
                pass
            elif action in _ID_ACTIONS:
                await self._mutate(session, task_id, session_id, action, context)
            else:
                raise todo_service.OrchestratorTodoError(
                    f"action 非法：{action or '(空)'}"
                    "（允许 add/reset/start/complete/update/remove/list）"
                )
        except todo_service.OrchestratorTodoError as exc:
            yield {"event": RESULT_EVENT, "data": SkillResult(success=False, summary=str(exc))}
            return

        rows = await todo_service.list_todos(session, task_id, session_id=session_id)
        rendered = todo_service.render_lines(rows) or "（空）"
        completed = sum(1 for row in rows if row.status == "completed")
        owner_label = f"任务 {task_id}" if task_id else f"会话 {session_id}（任务创建后自动迁移）"
        yield {
            "event": "status",
            "data": {"text": f"中枢待办已更新：共 {len(rows)} 项（已完成 {completed} 项）"},
        }
        yield {
            "event": RESULT_EVENT,
            "data": SkillResult(
                success=True,
                summary=(
                    f"中枢待办（{owner_label}，共 {len(rows)} 项，已完成 {completed} 项）：\n"
                    f"{rendered}"
                ),
                data={
                    "task_id": str(task_id) if task_id else None,
                    "session_id": str(session_id) if session_id else None,
                    "count": len(rows),
                    "completed": completed,
                },
            ),
        }

    async def _add(
        self,
        session: AsyncSession,
        task_id: uuid.UUID | None,
        session_id: uuid.UUID | None,
        context: SkillContext,
    ) -> None:
        """处理 action=add。"""
        items = context.args.get("items")
        if not isinstance(items, list):
            raise todo_service.OrchestratorTodoError("action=add 需要提供 items（字符串数组）。")
        await todo_service.add_todos(
            session, task_id, items, session_id=session_id
        )

    async def _reset(
        self,
        session: AsyncSession,
        task_id: uuid.UUID | None,
        session_id: uuid.UUID | None,
        context: SkillContext,
    ) -> None:
        """处理 action=reset：清空重建整份清单（计划细化/变更语义，ADR-067 修订）。"""
        items = context.args.get("items")
        if not isinstance(items, list):
            raise todo_service.OrchestratorTodoError(
                "action=reset 需要提供 items（字符串数组，整份新清单）。"
            )
        await todo_service.reset_todos(
            session, task_id, items, session_id=session_id
        )

    async def _mutate(
        self,
        session: AsyncSession,
        task_id: uuid.UUID | None,
        session_id: uuid.UUID | None,
        action: str,
        context: SkillContext,
    ) -> None:
        """处理需要 todo_id 的动作。"""
        raw_id = context.args.get("todo_id")
        if not raw_id:
            raise todo_service.OrchestratorTodoError(f"action={action} 需要提供 todo_id。")
        todo_id = todo_service._parse_todo_id(raw_id)
        if action == "remove":
            await todo_service.remove_todo(
                session, task_id, todo_id, session_id=session_id
            )
            return
        if action == "complete":
            await todo_service.update_todo(
                session, task_id, todo_id, session_id=session_id, status="completed"
            )
            return
        if action == "start":
            await todo_service.update_todo(
                session, task_id, todo_id, session_id=session_id, status="in_progress"
            )
            return
        await todo_service.update_todo(
            session,
            task_id,
            todo_id,
            session_id=session_id,
            content=context.args.get("content"),
        )
