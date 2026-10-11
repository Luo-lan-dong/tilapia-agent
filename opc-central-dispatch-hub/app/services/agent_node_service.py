"""Agent 节点业务服务层。"""

import logging
import math

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.core.security import generate_registration_token
from app.models.agent_node import AgentNode, NodeStatus
from app.schemas.agent_node import (
    AgentNodeListResponse,
    AgentNodeRegisterRequest,
    AgentNodeResponse,
)
from app.schemas.common import PaginationInfo

logger = logging.getLogger(__name__)


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


class AgentNodeService:
    """Agent 节点注册与查询业务能力封装。"""

    def _apply_reported_address(
        self,
        node: AgentNode,
        request: AgentNodeRegisterRequest,
    ) -> None:
        """将注册请求中的自报地址落库（ADR-035）。

        reported_ip / reported_api_port 跟随最新上报更新（Agent 可能改端口）；
        ``api_token`` 仅在节点尚无 token 时落库（首次注册），后续注册不覆盖
        （对齐 ADR-020 幂等语义，防止 token 被漂移请求冲掉）。旧版 Agent
        未携带 reported_address 时不动既有值。

        Args:
            node: 目标节点（新建或已存在）。
            request: 注册请求。
        """
        reported = request.reported_address
        if reported is None:
            return
        node.reported_ip = reported.ip
        node.reported_api_port = reported.api_port
        if node.api_token is None:
            node.api_token = reported.api_token

    async def register_agent_node(
        self,
        session: AsyncSession,
        request: AgentNodeRegisterRequest,
    ) -> AgentNodeResponse:
        """注册 Agent 节点（按 client_id 幂等）。

        同一物理机器重启后会重复调用注册接口：若 client_id 已存在，
        则更新 ``agent_type`` / ``capabilities``，**不覆盖** ``name``（name 属
        中枢侧管理，Agent 端自报值不应冲掉用户在 NodeDetailDrawer 改过的
        名称；如需"恢复 Agent 自报名"，走单独的 reset_name 端点
        —— 该端点后续可加，MVP 不暴露）；否则创建新记录。

        Args:
            session: 异步数据库会话。
            request: 注册请求。

        Returns:
            注册成功的节点详情响应。
        """
        if request.client_id:
            existing = await session.scalar(
                select(AgentNode).where(AgentNode.client_id == request.client_id)
            )
            if existing is not None:
                # 注意：name / status / load / registration_token 不覆盖。
                # 防止 Agent 进程重启后自报 name 冲掉用户的中枢侧改名。
                existing.agent_type = request.agent_type
                existing.capabilities = request.capabilities
                self._apply_reported_address(existing, request)
                await session.commit()
                await session.refresh(existing)

                logger.info(
                    "agent_reregistered",
                    extra={
                        "event": "agent_reregistered",
                        "client_id": request.client_id,
                        "node_id": str(existing.id),
                        "agent_type": existing.agent_type,
                        "name_preserved": existing.name,
                    },
                )
                return AgentNodeResponse.model_validate(existing)

        agent_node = AgentNode(
            name=request.name,
            agent_type=request.agent_type,
            capabilities=request.capabilities,
            status=NodeStatus.IDLE.value,
            load=0.0,
            registration_token=generate_registration_token(),
            client_id=request.client_id,
        )
        self._apply_reported_address(agent_node, request)
        session.add(agent_node)
        await session.commit()
        await session.refresh(agent_node)

        logger.info(
            "agent_registered",
            extra={
                "event": "agent_registered",
                "client_id": agent_node.client_id,
                "node_id": str(agent_node.id),
                "agent_type": agent_node.agent_type,
                "capabilities": agent_node.capabilities,
            },
        )
        return AgentNodeResponse.model_validate(agent_node)

    async def list_agent_nodes(
        self,
        session: AsyncSession,
        status_filter: str | None,
        agent_type: str | None,
        limit: int,
        offset: int,
    ) -> AgentNodeListResponse:
        """分页查询 Agent 节点列表。

        Args:
            session: 异步数据库会话。
            status_filter: 可选，按节点状态筛选。
            agent_type: 可选，按 Agent 类型筛选。
            limit: 每页返回条数。
            offset: 分页偏移量。

        Returns:
            节点列表分页响应。
        """
        # 排序规则：存活节点（非 offline）排在上方；存活节点中负载高的在前；
        # 离线节点排底部，组内按创建时间倒序。
        is_offline = AgentNode.status == NodeStatus.OFFLINE
        query = select(AgentNode).order_by(
            is_offline.asc(),
            case((is_offline, 0.0), else_=func.coalesce(AgentNode.load, 0.0)).desc(),
            AgentNode.created_at.desc(),
        )
        if status_filter:
            query = query.where(AgentNode.status == status_filter)
        if agent_type:
            query = query.where(AgentNode.agent_type == agent_type)

        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        result = await session.scalars(query.offset(offset).limit(limit))
        total_count = total or 0

        response = AgentNodeListResponse(
            items=result.all(),
            pagination=PaginationInfo(
                page=offset // limit + 1,
                page_size=limit,
                total=total_count,
                total_pages=math.ceil(total_count / limit) if limit else 0,
            ),
        )

        logger.debug(
            "agent_nodes_queried",
            extra={
                "event": "agent_nodes_queried",
                "total": total_count,
                "filter_status": status_filter,
            },
        )
        return response
