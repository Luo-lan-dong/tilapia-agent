"""会话级插话队列注册表（ADR-045 Steering）。

chat_stream 进行中（open 与 close 之间），用户可经 steer 端点投递插话；
Orchestrator 在 Provider Turn 边界 drain 提升进 prompt。单进程部署，
模块级注册表即可；多实例部署需升级为共享存储（Redis）。
"""

import asyncio

# session_id(str) → 活跃流的插话队列；仅 open 与 close 之间存在
_queues: dict[str, asyncio.Queue[str]] = {}


def open_stream(session_id: str) -> None:
    """标记会话流开始（幂等；重复 open 清空旧队列防残留）。"""
    _queues[session_id] = asyncio.Queue()


def is_active(session_id: str) -> bool:
    """会话是否有活跃流（ADR-066 唤醒轮互斥判断用）。"""
    return session_id in _queues


def close_stream(session_id: str) -> None:
    """标记会话流结束并注销队列（正常路径收尾前已 drain，此处兜底丢弃）。"""
    _queues.pop(session_id, None)


def enqueue(session_id: str, content: str) -> bool:
    """投递插话；会话无活跃流时返回 False（前端降级为普通发送）。"""
    queue = _queues.get(session_id)
    if queue is None:
        return False
    queue.put_nowait(content)
    return True


def drain(session_id: str) -> list[str]:
    """非阻塞取空队列（按投递顺序返回）。"""
    queue = _queues.get(session_id)
    if queue is None:
        return []
    messages: list[str] = []
    while True:
        try:
            messages.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return messages
