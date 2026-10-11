"""规划方案审批服务：plan 模式任务的批准 / 驳回流转。

承接自路由层（backend-component-standards §2.1：路由只做参数转发，
业务逻辑下沉到 service），复用 :class:`TaskService.dispatch_task` 完成入队。
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.core.errors import TaskDispatchError, TaskNotFoundError
from app.models.task import Task, TaskStatus

logger = logging.getLogger(__name__)


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


async def _get_completed_plan_task(session: AsyncSession, task_id: uuid.UUID) -> Task:
    """校验任务存在、处于 plan 模式且已完成（completed/review），返回任务记录。

    Args:
        session: 异步数据库会话。
        task_id: 任务 UUID。

    Returns:
        校验通过的 Task 实例。

    Raises:
        TaskNotFoundError: 任务不存在。
        TaskDispatchError: 任务不是 plan 模式或尚未完成。
    """
    task = await session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(str(task_id))
    if task.execution_mode != "plan":
        raise TaskDispatchError(
            task_id=str(task_id),
            reason="仅 plan 模式的任务支持规划方案审批操作",
        )
    # ADR-038：新方案产出落 review；存量 completed 的 plan 任务保持兼容
    if task.status not in {TaskStatus.REVIEW.value, TaskStatus.COMPLETED.value}:
        raise TaskDispatchError(
            task_id=str(task_id),
            reason="任务尚未完成，无法执行规划方案审批操作",
        )
    return task


class PlanReviewService:
    """规划方案审批业务能力封装。"""

    async def approve_plan(self, session: AsyncSession, task_id: uuid.UUID) -> dict[str, str]:
        """批准规划方案：执行模式改为 build，方案全文拼入 change_brief 并重新入队。

        Args:
            session: 异步数据库会话（dispatch_task 内部会 commit，此处先修改
                字段即可一并落库）。
            task_id: 任务 UUID。

        Returns:
            派发结果字典（task_id / target / dispatch_type / status）。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskDispatchError: 任务不是 plan 模式 / 尚未完成 / 未上报方案内容。
        """
        _bind_request_id()
        task = await _get_completed_plan_task(session, task_id)
        if not task.plan_content:
            raise TaskDispatchError(
                task_id=str(task_id),
                reason="任务未上报规划方案内容，无法批准",
            )

        task.execution_mode = "build"
        task.change_brief = (
            f"规划方案已批准，请按以下方案在既有代码基础上开发：\n\n{task.plan_content}"
        )
        logger.info(
            "task_plan_approved",
            extra={
                "event": "task_plan_approved",
                "task_id": str(task_id),
                "root_task_id": str(task.root_task_id) if task.root_task_id else None,
                "plan_content_length": len(task.plan_content),
            },
        )

        from app.services.task_service import TaskService

        # 延迟导入避免 task_service → 本模块的潜在循环依赖
        return await TaskService().dispatch_task(
            session,
            task_id,
            target=task.execution_target or "code",
        )

    async def reject_plan(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        feedback: str,
    ) -> dict[str, str]:
        """驳回规划方案：反馈意见拼入 change_brief，以 plan 模式重新入队派发。

        Args:
            session: 异步数据库会话。
            task_id: 任务 UUID。
            feedback: 驳回反馈意见。

        Returns:
            派发结果字典（task_id / target / dispatch_type / status）。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskDispatchError: 任务不是 plan 模式或尚未完成。
        """
        _bind_request_id()
        task = await _get_completed_plan_task(session, task_id)

        task.change_brief = f"上一轮规划方案被驳回，反馈意见：{feedback}，请基于反馈修订方案"
        logger.info(
            "task_plan_rejected",
            extra={
                "event": "task_plan_rejected",
                "task_id": str(task_id),
                "root_task_id": str(task.root_task_id) if task.root_task_id else None,
                "feedback_length": len(feedback),
            },
        )

        from app.services.task_service import TaskService

        return await TaskService().dispatch_task(
            session,
            task_id,
            target=task.execution_target or "code",
            mode="plan",
        )
