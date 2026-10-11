"""宝塔 MCP 服务器管理服务（ADR-049）。"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bt_mcp_server import BtMcpServer
from app.schemas.bt_mcp import (
    BtMcpServerCreate,
    BtMcpServerUpdate,
    BtMcpTestResponse,
    BtMcpToolInfo,
)
from app.services.bt_mcp_client import BtMcpClient, BtMcpError
from app.services.bt_mcp_risk import get_tool_category, get_tool_risk, summarize_tools

logger = logging.getLogger(__name__)

#: 工具描述截断上限（目录清单防超长）
_DESCRIPTION_MAX = 120


class BtMcpServerService:
    """宝塔 MCP 服务器连接 CRUD 与连通性服务。"""

    async def list_servers(self, session: AsyncSession) -> list[BtMcpServer]:
        """列出全部服务器（创建时间升序）。"""
        result = await session.execute(select(BtMcpServer).order_by(BtMcpServer.created_at))
        return list(result.scalars().all())

    async def list_enabled_servers(self, session: AsyncSession) -> list[BtMcpServer]:
        """列出启用的服务器（编排技能用）。"""
        result = await session.execute(
            select(BtMcpServer)
            .where(BtMcpServer.enabled.is_(True))
            .order_by(BtMcpServer.created_at)
        )
        return list(result.scalars().all())

    async def get_server(self, session: AsyncSession, server_id) -> BtMcpServer | None:
        """按 ID 查询服务器。"""
        result = await session.execute(select(BtMcpServer).where(BtMcpServer.id == server_id))
        return result.scalar_one_or_none()

    async def create_server(self, session: AsyncSession, data: BtMcpServerCreate) -> BtMcpServer:
        """创建服务器连接（name 唯一；支持 mcp_info JSON 导入）。"""
        data.apply_mcp_info()
        data.validate_completeness()
        await self._ensure_name_available(session, data.name)
        server = BtMcpServer(
            name=data.name,
            local_url=data.local_url or "",
            public_url=data.public_url,
            address_mode=data.address_mode,
            api_token=data.api_token or "",
            ca_cert=data.ca_cert,
            enabled=data.enabled,
            remark=data.remark,
        )
        # 仅给了公网地址时地址模式自动切换
        if not server.local_url and server.public_url:
            server.local_url = server.public_url
            server.address_mode = "public"
        session.add(server)
        await session.commit()
        await session.refresh(server)
        logger.info(
            "bt_mcp_server_created",
            extra={
                "event": "bt_mcp_server_created",
                "server_id": str(server.id),
                "server_name": server.name,
                "address_mode": server.address_mode,
                "has_ca_cert": bool(server.ca_cert),
            },
        )
        return server

    async def update_server(
        self,
        session: AsyncSession,
        server: BtMcpServer,
        data: BtMcpServerUpdate,
    ) -> BtMcpServer:
        """更新服务器连接（凭据缺省保留、空串清除；支持重新导入 mcp_info）。"""
        changes = data.model_dump(exclude_unset=True, exclude={"mcp_info"})
        if data.mcp_info is not None:
            from app.schemas.bt_mcp import BtMcpServerCreate

            merged = BtMcpServerCreate(
                name=data.name or server.name,
                mcp_info=data.mcp_info,
            )
            merged.apply_mcp_info()
            if merged.api_token:
                server.api_token = merged.api_token
            if merged.local_url:
                server.local_url = merged.local_url
            if merged.public_url:
                server.public_url = merged.public_url
            if merged.ca_cert:
                server.ca_cert = merged.ca_cert
        if "name" in changes and changes["name"] != server.name:
            await self._ensure_name_available(session, changes["name"])
        for field, value in changes.items():
            setattr(server, field, value)
        await session.commit()
        await session.refresh(server)
        logger.info(
            "bt_mcp_server_updated",
            extra={"event": "bt_mcp_server_updated", "server_id": str(server.id)},
        )
        return server

    async def delete_server(self, session: AsyncSession, server: BtMcpServer) -> None:
        """删除服务器连接。"""
        await session.delete(server)
        await session.commit()
        logger.info(
            "bt_mcp_server_deleted",
            extra={"event": "bt_mcp_server_deleted", "server_id": str(server.id)},
        )

    def resolve_url(self, server: BtMcpServer) -> str:
        """按 address_mode 解析实际连接地址（public 缺失回退 local）。"""
        if server.address_mode == "public" and server.public_url:
            return server.public_url
        return server.local_url

    def build_client(self, server: BtMcpServer) -> BtMcpClient:
        """为服务器构建 MCP 客户端。"""
        return BtMcpClient(
            url=self.resolve_url(server),
            api_token=server.api_token,
            ca_cert=server.ca_cert,
        )

    async def test_connection(
        self, session: AsyncSession, server: BtMcpServer
    ) -> BtMcpTestResponse:
        """连接测试：握手 + 拉工具清单，返回服务端信息与工具统计。"""
        client = self.build_client(server)
        try:
            info = await client.initialize()
            tools = await client.list_tools()
        except BtMcpError as exc:
            logger.warning(
                "bt_mcp_test_failed",
                extra={
                    "event": "bt_mcp_test_failed",
                    "server_id": str(server.id),
                    "reason": exc.reason,
                },
            )
            return BtMcpTestResponse(success=False, message=str(exc))
        finally:
            await client.close()
        server_info = info.get("serverInfo", {})
        return BtMcpTestResponse(
            success=True,
            message=(
                f"连接成功：{server_info.get('name', '未知服务')} "
                f"v{server_info.get('version', '?')}，共 {len(tools)} 个工具"
            ),
            server_info=server_info,
            tool_count=len(tools),
            tool_summary=summarize_tools(tools),
        )

    async def list_tools(self, session: AsyncSession, server: BtMcpServer) -> list[BtMcpToolInfo]:
        """拉取服务器工具清单（含分类与风险，供前端与 AI 目录展示）。"""
        client = self.build_client(server)
        try:
            tools = await client.list_tools()
        finally:
            await client.close()
        return [
            BtMcpToolInfo(
                name=tool.get("name", ""),
                description=(tool.get("description") or "")[:_DESCRIPTION_MAX],
                category=get_tool_category(tool.get("name", "")),
                risk=get_tool_risk(tool.get("name", "")),
            )
            for tool in tools
        ]

    async def _ensure_name_available(self, session: AsyncSession, name: str) -> None:
        """校验 name 唯一（冲突抛 BusinessError 207002）。"""
        from app.core.errors import BusinessError, ErrorCode

        result = await session.execute(select(BtMcpServer).where(BtMcpServer.name == name))
        if result.scalar_one_or_none() is not None:
            raise BusinessError(
                status_code=409,
                biz_code=ErrorCode.BT_MCP_SERVER_NAME_DUPLICATE,
                detail=f"服务器名称已存在：{name}",
            )
