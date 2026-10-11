"""Agent 节点 WebSocket 连接管理器。

维护内存中的 ``node_id -> WebSocket`` 映射，提供线程安全的连接注册、
注销与查询能力。所有映射操作均通过 ``asyncio.Lock`` 保护，避免并发竞争。
"""

import asyncio
import logging
from typing import Any

from starlette.websockets import WebSocket
from structlog import contextvars

logger = logging.getLogger(__name__)


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


class ConnectionManager:
    """Agent 节点 WebSocket 连接管理器（单例）。

    该管理器以 ``node_id`` 为键维护活跃 WebSocket 连接，用于任务下发、
    心跳维持和节点状态追踪。同一 ``node_id`` 同时只能保持一个连接；
    重复连接时，旧连接会被静默移除并由新连接接管。

    通过模块级 ``connection_manager`` 变量对外暴露唯一实例。
    """

    def __init__(self) -> None:
        """初始化连接映射与操作锁。"""
        self._connections: dict[str, WebSocket] = {}
        self._map_lock: asyncio.Lock = asyncio.Lock()

    async def connect(self, node_id: str, websocket: WebSocket) -> None:
        """注册节点 WebSocket 连接。

        Args:
            node_id: Agent 节点唯一标识。
            websocket: 已接受的 WebSocket 连接对象。
        """
        async with self._map_lock:
            old_ws = self._connections.get(node_id)
            self._connections[node_id] = websocket
        if old_ws is not None and old_ws is not websocket:
            logger.warning(
                "agent_connection_replaced",
                extra={
                    "event": "agent_connection_replaced",
                    "client_id": node_id,
                },
            )
        logger.info(
            "agent_connection_registered",
            extra={
                "event": "agent_connection_registered",
                "client_id": node_id,
            },
        )

    async def disconnect(self, node_id: str) -> None:
        """注销节点 WebSocket 连接。

        Args:
            node_id: Agent 节点唯一标识。
        """
        async with self._map_lock:
            removed = self._connections.pop(node_id, None)
        if removed is not None:
            logger.info(
                "agent_connection_removed",
                extra={
                    "event": "agent_connection_removed",
                    "client_id": node_id,
                },
            )

    async def disconnect_if_current(
        self,
        node_id: str,
        websocket: WebSocket,
    ) -> bool:
        """仅当当前映射仍是给定 WebSocket 时才注销连接。

        用于处理节点重连场景：旧连接断开后不应误删新连接。

        Args:
            node_id: Agent 节点唯一标识。
            websocket: 期望断开的 WebSocket 对象。

        Returns:
            成功移除返回 ``True``；当前映射已不是该连接返回 ``False``。
        """
        async with self._map_lock:
            if self._connections.get(node_id) is not websocket:
                return False
            self._connections.pop(node_id, None)
        logger.info(
            "agent_connection_removed",
            extra={
                "event": "agent_connection_removed",
                "client_id": node_id,
            },
        )
        return True

    async def get_connection(self, node_id: str) -> WebSocket | None:
        """获取指定节点的活跃 WebSocket 连接。

        Args:
            node_id: Agent 节点唯一标识。

        Returns:
            活跃 WebSocket 连接；不存在时返回 ``None``。
        """
        async with self._map_lock:
            return self._connections.get(node_id)

    def is_connected(self, node_id: str) -> bool:
        """判断指定节点当前是否保持 WebSocket 连接。

        注意：该方法在锁外读取映射，仅用于非强一致性场景（如监控展示）。
        需要精确判断时，请使用 ``get_connection``。

        Args:
            node_id: Agent 节点唯一标识。

        Returns:
            节点当前是否在线。
        """
        return node_id in self._connections

    async def send_to_node(self, node_id: str, message: dict[str, Any]) -> bool:
        """向指定节点发送 JSON 消息。

        Args:
            node_id: 目标 Agent 节点 ID。
            message: 待发送的 JSON 可序列化字典。

        Returns:
            发送成功返回 ``True``；节点不在线或发送失败返回 ``False``。
        """
        websocket = await self.get_connection(node_id)
        if websocket is None:
            return False
        try:
            await websocket.send_json(message)
        except Exception:
            logger.exception(
                "agent_send_message_failed",
                extra={
                    "event": "agent_send_message_failed",
                    "client_id": node_id,
                },
            )
            return False
        return True


# 模块级导出，便于业务层统一引用同一个单例
connection_manager = ConnectionManager()
