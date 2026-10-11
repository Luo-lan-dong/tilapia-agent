"""任务构建会话服务层。"""

import logging
import math
import uuid
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.models.task_building_session import TaskBuildingSession
from app.schemas.common import PaginationInfo
from app.schemas.task_building import (
    TaskBuildingSessionCreateRequest,
    TaskBuildingSessionListResponse,
    TaskBuildingSessionSummary,
    TaskBuildingSessionUpdateRequest,
)

logger = logging.getLogger(__name__)

DEFAULT_LIST_LIMIT = 100

#: 会话消息保留上限（与 task_event_handler._DECISION_MESSAGE_KEEP 口径一致，
#: ADR-061 M4：避免节点回复/续聊消息无限增长）
MESSAGE_KEEP_LIMIT = 500


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


class TaskBuildingSessionService:
    """任务构建会话 CRUD 服务。

    持久化 Spec Chat 的多轮对话历史和三件套草案，支持历史会话回溯。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化服务。

        Args:
            session: 异步数据库会话。
        """
        self._session = session

    async def create(
        self,
        data: TaskBuildingSessionCreateRequest,
    ) -> TaskBuildingSession:
        """创建新会话。

        Args:
            data: 会话创建数据。

        Returns:
            新建的数据库模型实例。
        """
        session_obj = TaskBuildingSession(
            title=data.title,
            # exclude_none：thinking/steps 仅 assistant 消息携带（ADR-033），
            # user 消息落库不写 None 键，保持 JSON 干净
            messages=[m.model_dump(exclude_none=True) for m in data.messages],
            specification=data.specification.model_dump() if data.specification else None,
            mode=data.mode,
            model=data.model,
            task_id=data.task_id,
        )
        self._session.add(session_obj)
        await self._session.commit()
        await self._session.refresh(session_obj)

        logger.info(
            "task_building_session_created",
            extra={
                "event": "task_building_session_created",
                "session_id": str(session_obj.id),
            },
        )
        return session_obj

    async def get(self, session_id: uuid.UUID) -> TaskBuildingSession | None:
        """根据 ID 查询会话。

        Args:
            session_id: 会话 ID。

        Returns:
            会话模型实例，不存在时返回 None。
        """
        result = await self._session.execute(
            select(TaskBuildingSession).where(TaskBuildingSession.id == session_id)
        )
        session_obj = result.scalar_one_or_none()

        logger.debug(
            "session_queried",
            extra={
                "event": "session_queried",
                "session_id": str(session_id),
            },
        )
        return session_obj

    async def list(
        self,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> TaskBuildingSessionListResponse:
        """查询会话列表，按创建时间倒序（轻量列选择）。

        列表只取元数据列，**不加载** ``messages`` / ``specification`` /
        ``context_compaction`` / ``context_environment`` 等大 JSONB 列——
        历史会话的对话历史与三件套单条可达数百 KB，整列拉取曾导致列表
        接口响应体过大、前端长时间无法加载（2026-10-02 实测）；完整数据
        由详情接口（``get``）按需提供。

        Args:
            limit: 最大返回条数。
            offset: 偏移量。

        Returns:
            会话列表分页响应（轻量项）。
        """
        total = await self._session.scalar(select(func.count()).select_from(TaskBuildingSession))
        total_count = total or 0

        result = await self._session.execute(
            select(
                TaskBuildingSession.id,
                TaskBuildingSession.title,
                TaskBuildingSession.mode,
                TaskBuildingSession.model,
                TaskBuildingSession.task_id,
                TaskBuildingSession.created_at,
                TaskBuildingSession.updated_at,
            )
            .order_by(desc(TaskBuildingSession.created_at))
            .limit(limit)
            .offset(offset)
        )
        sessions = [
            TaskBuildingSessionSummary(
                id=row.id,
                title=row.title,
                mode=row.mode,
                model=row.model,
                task_id=row.task_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in result.all()
        ]

        response = TaskBuildingSessionListResponse(
            items=sessions,
            pagination=PaginationInfo(
                page=offset // limit + 1 if limit else 1,
                page_size=limit,
                total=total_count,
                total_pages=math.ceil(total_count / limit) if limit else 0,
            ),
        )

        logger.debug(
            "sessions_queried",
            extra={
                "event": "sessions_queried",
                "count": len(sessions),
                "total": total_count,
            },
        )
        return response

    async def update(
        self,
        session_id: uuid.UUID,
        data: TaskBuildingSessionUpdateRequest,
    ) -> TaskBuildingSession | None:
        """更新会话。

        Args:
            session_id: 会话 ID。
            data: 更新数据，字段为 None 时不修改。

        Returns:
            更新后的会话模型实例，不存在时返回 None。
        """
        session_obj = await self.get(session_id)
        if session_obj is None:
            return None

        if data.title is not None:
            session_obj.title = data.title
        if data.messages is not None:
            # exclude_none：user 消息落库不写 thinking/steps 的 None 键
            session_obj.messages = [
                m.model_dump(exclude_none=True) for m in data.messages
            ]
        if data.specification is not None:
            session_obj.specification = data.specification.model_dump()
        if data.mode is not None:
            session_obj.mode = data.mode
        if data.model is not None:
            session_obj.model = data.model
        if data.task_id is not None:
            session_obj.task_id = data.task_id

        await self._session.commit()
        await self._session.refresh(session_obj)

        logger.info(
            "task_building_session_updated",
            extra={
                "event": "task_building_session_updated",
                "session_id": str(session_obj.id),
            },
        )
        return session_obj

    @staticmethod
    async def append_message(
        session: AsyncSession,
        session_id: uuid.UUID | None,
        message: dict[str, Any],
    ) -> bool:
        """向任务构建会话追加单条消息并立即提交（ADR-064 D5 消息持久化）。

        node-chat 用户消息 / steer 用户消息 / 节点回复三路共用的最小写入单元；
        显式传入数据库会话（与 ``TaskAgentTraceService`` 同风格），便于放进
        调用方事务。超长历史裁剪至最近 ``MESSAGE_KEEP_LIMIT`` 条。

        旁路原则（ADR-064 红线②）：session_id 为空、会话不存在或写库失败
        一律告警并返回 False，绝不阻塞对话主链路。

        Args:
            session: 异步数据库会话（调用方事务内的会话）。
            session_id: 任务构建会话 ID；任务无关联会话时为 None（跳过落库）。
            message: 消息字典，形如 ``{role, content, mention_node?, reply_to?, ts}``。

        Returns:
            落库成功返回 True；旁路跳过或失败返回 False。
        """
        if session_id is None:
            logger.debug(
                "task_building_message_skipped_no_session",
                extra={"event": "task_building_message_skipped_no_session"},
            )
            return False
        try:
            session_obj = await session.get(TaskBuildingSession, session_id)
            if session_obj is None:
                logger.warning(
                    "task_building_message_session_not_found",
                    extra={
                        "event": "task_building_message_session_not_found",
                        "session_id": str(session_id),
                    },
                )
                return False
            # 重新赋值整列表（JSONB 原值追踪不可靠），并裁剪至保留上限
            session_obj.messages = (
                list(session_obj.messages or [])[-(MESSAGE_KEEP_LIMIT - 1) :] + [message]
            )
            await session.commit()
            return True
        except Exception:  # noqa: BLE001 — 落库是旁路，失败不阻塞对话主链路
            await session.rollback()
            logger.warning(
                "task_building_message_append_failed",
                extra={
                    "event": "task_building_message_append_failed",
                    "session_id": str(session_id),
                },
                exc_info=True,
            )
            return False

    async def delete(self, session_id: uuid.UUID) -> bool:
        """删除会话。

        Args:
            session_id: 会话 ID。

        Returns:
            删除成功返回 True，不存在返回 False。
        """
        session_obj = await self.get(session_id)
        if session_obj is None:
            return False

        await self._session.delete(session_obj)
        await self._session.commit()

        logger.info(
            "task_building_session_deleted",
            extra={
                "event": "task_building_session_deleted",
                "session_id": str(session_id),
            },
        )
        return True
