"""Agent 节点管理服务层（安装脚本、详情、Token 重置、显示名更新）。"""

import logging
import uuid
from pathlib import Path

from fastapi import status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import contextvars

from app.core.config import get_settings
from app.core.errors import (
    AgentNotFoundError,
    BusinessError,
    ErrorCode,
)
from app.core.security import generate_registration_token
from app.models.agent_node import AgentNode, NodeStatus
from app.schemas.agent_node import (
    AgentNodeMetricsResponse,
    AgentNodeResponse,
    AgentNodeUpdateRequest,
    InstallScriptResponse,
    RegistrationTokenResponse,
)
from app.services.resource_sync_service import ResourceSyncService

logger = logging.getLogger(__name__)

SUPPORTED_INSTALL_SCRIPTS: set[tuple[str, str]] = {("code", "linux"), ("office", "linux")}


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


class AgentNodeManagementService:
    """Agent 节点安装脚本、详情查询与 Token 管理能力封装。"""

    async def get_install_script(
        self,
        agent_type: str,
        os: str,
        hub_url: str,
    ) -> InstallScriptResponse:
        """获取 Agent 安装脚本元数据。

        当前支持 ``code``/``office`` 类型且 ``os=linux``（ADR-025 / ADR-062）。

        Args:
            agent_type: Agent 类型。
            os: 目标操作系统。
            hub_url: 中枢公网地址，用于拼接脚本下载地址。

        Returns:
            安装脚本元数据响应。

        Raises:
            BusinessError: 不支持的 Agent 类型或操作系统时抛出。
        """
        if (agent_type, os) not in SUPPORTED_INSTALL_SCRIPTS:
            raise BusinessError(
                status_code=status.HTTP_400_BAD_REQUEST,
                biz_code=ErrorCode.INSTALL_SCRIPT_NOT_SUPPORTED,
                detail=f"暂不支持的 Agent 类型或操作系统: {agent_type}/{os}",
            )

        # ADR-025：脚本与 agent 类型绑定；本地缓存清单中该类型脚本未同步时明确报错，
        # 杜绝「安装 code 类型节点却分发其他类型脚本」的错配
        script_key = f"install-script-{agent_type}"
        script_path = f"install-scripts/{agent_type}/install-agent-{agent_type}-linux.sh"
        local_manifest = ResourceSyncService(
            cache_dir=Path(get_settings().resource_cache_dir)
        ).local_manifest()
        entries = {item.get("key"): item for item in (local_manifest or {}).get("resources", [])}
        entry = entries.get(script_key)
        if entry is None:
            raise BusinessError(
                status_code=status.HTTP_404_NOT_FOUND,
                biz_code=ErrorCode.INSTALL_SCRIPT_NOT_SUPPORTED,
                detail=(
                    f"{agent_type} 类型的安装脚本尚未发布或未同步到中枢，"
                    "请在中枢「系统设置 → 资源管理」检查更新后再试"
                ),
            )

        script_url = f"{hub_url}/static/{entry.get('path') or script_path}"
        return InstallScriptResponse(
            agent_type=agent_type,
            os=os,
            script_url=script_url,
            hub_url=hub_url,
            checksum=f"sha256:{entry.get('sha256', '')}",
            version=entry.get("version") or "",
        )

    async def get_agent_node(
        self,
        session: AsyncSession,
        node_id: uuid.UUID,
    ) -> AgentNode:
        """根据 ID 查询 Agent 节点详情。

        Args:
            session: 异步数据库会话。
            node_id: 节点 UUID。

        Returns:
            Agent 节点数据库模型。

        Raises:
            AgentNotFoundError: 节点不存在时抛出。
        """
        node = await session.get(AgentNode, node_id)
        if node is None:
            raise AgentNotFoundError(str(node_id))
        return node

    async def regenerate_registration_token(
        self,
        session: AsyncSession,
        node_id: uuid.UUID,
    ) -> RegistrationTokenResponse:
        """重新生成节点注册 Token。

        Args:
            session: 异步数据库会话。
            node_id: 节点 UUID。

        Returns:
            包含新注册 Token 的响应。

        Raises:
            AgentNotFoundError: 节点不存在时抛出。
        """
        node = await self.get_agent_node(session, node_id)
        node.registration_token = generate_registration_token()
        await session.commit()
        await session.refresh(node)

        logger.info(
            "agent_node_token_regenerated",
            extra={
                "event": "agent_node_token_regenerated",
                "client_id": str(node_id),
            },
        )
        return RegistrationTokenResponse(registration_token=node.registration_token)

    async def get_node_metrics(
        self,
        session: AsyncSession,
        node_id: uuid.UUID,
    ) -> AgentNodeMetricsResponse:
        """查询 Agent 节点监控详情。

        Args:
            session: 异步数据库会话。
            node_id: 节点 UUID。

        Returns:
            节点监控详情响应，包含在线状态、最后心跳时间及监控快照。

        Raises:
            AgentNotFoundError: 节点不存在时抛出。
        """
        node = await self.get_agent_node(session, node_id)
        is_online = node.status != NodeStatus.OFFLINE.value

        logger.info(
            "agent_node_metrics_queried",
            extra={
                "event": "agent_node_metrics_queried",
                "client_id": str(node_id),
                "is_online": is_online,
                "has_metrics": node.metrics_snapshot is not None,
            },
        )
        return AgentNodeMetricsResponse(
            node_id=node.id,
            is_online=is_online,
            last_heartbeat_at=node.last_heartbeat_at,
            metrics=node.metrics_snapshot,
        )

    async def update_node_name(
        self,
        session: AsyncSession,
        node_id: uuid.UUID,
        request: AgentNodeUpdateRequest,
    ) -> AgentNodeResponse:
        """更新节点显示名（ADR-016）。

        仅允许修改 ``name``；``agent_type`` / ``client_id`` 不在 PATCH schema 中。
        校验：name 非空、不允许仅空白、长度 1-128；同 agent_type 下唯一。

        Args:
            session: 异步数据库会话。
            node_id: 节点 UUID。
            request: PATCH 请求体（仅含 name）。

        Returns:
            更新后的 AgentNodeResponse。

        Raises:
            AgentNotFoundError: 节点不存在。
            BusinessError: name 重复（202010）或非法（202011）。
        """
        requested = request.name.strip()
        if not requested or requested != request.name:
            logger.warning(
                "node_name_update_rejected",
                extra={
                    "event": "node_name_update_rejected",
                    "node_id": str(node_id),
                    "reason": "invalid",
                    "requested_name": request.name,
                },
            )
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.AGENT_NODE_NAME_INVALID,
                detail="节点名称不能为空或仅包含空白字符",
            )

        node = await self.get_agent_node(session, node_id)
        old_name = node.name

        if requested == old_name:
            # 没变也返回，避免无意义的 UPDATE
            return AgentNodeResponse.model_validate(node)

        # 1) 名称重复由 partial unique index 保证；这里用预检 + 兜底捕获
        existing = await session.scalar(
            select(AgentNode).where(
                AgentNode.agent_type == node.agent_type,
                AgentNode.name == requested,
                AgentNode.id != node.id,
            )
        )
        if existing is not None:
            logger.warning(
                "node_name_update_rejected",
                extra={
                    "event": "node_name_update_rejected",
                    "node_id": str(node_id),
                    "reason": "duplicate",
                    "agent_type": node.agent_type,
                    "requested_name": requested,
                    "conflicting_node_id": str(existing.id),
                },
            )
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.AGENT_NODE_NAME_DUPLICATE,
                detail=(f"节点名称在同 agent_type={node.agent_type} 下重复: {requested}"),
            )

        node.name = requested
        try:
            await session.commit()
        except IntegrityError as exc:
            # 兜底：并发场景下两个请求同时改同名
            await session.rollback()
            logger.warning(
                "node_name_update_rejected",
                extra={
                    "event": "node_name_update_rejected",
                    "node_id": str(node_id),
                    "reason": "duplicate_race",
                    "agent_type": node.agent_type,
                    "requested_name": requested,
                },
            )
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.AGENT_NODE_NAME_DUPLICATE,
                detail=(f"节点名称在同 agent_type={node.agent_type} 下重复: {requested}"),
            ) from exc
        await session.refresh(node)

        logger.info(
            "node_name_updated",
            extra={
                "event": "node_name_updated",
                "node_id": str(node_id),
                "agent_type": node.agent_type,
                "old_name": old_name,
                "new_name": requested,
            },
        )
        return AgentNodeResponse.model_validate(node)
