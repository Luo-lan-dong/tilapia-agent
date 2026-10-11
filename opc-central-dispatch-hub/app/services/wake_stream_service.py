"""唤醒轮事件流广播注册表（ADR-066 唤醒可视化，2026-10-07 用户裁决）。

任务终态自动唤醒轮在后台执行，本服务把编排事件流（thinking/chunk/step 等）
按会话实时广播给前端订阅端点（SSE），使唤醒后的中枢工作像正常对话一样
逐字实时显示在对话区。

单进程部署，模块级注册表即可（与 steering_service 同款模式）；多实例部署
需升级为共享存储（Redis）。订阅晚于唤醒开始时只能看到剩余事件——完整
历史由落库 + 前端 useWakeMessagesSync 兜底补齐。
"""

import asyncio
from collections.abc import AsyncGenerator

# session_id(str) → 活跃唤醒轮的事件队列；仅 open 与 close 之间存在。
# 队列元素为事件 dict（{"event": str, "data": dict}）或 None（结束哨兵）。
_queues: dict[str, asyncio.Queue[dict | None]] = {}


def open_stream(session_id: str) -> None:
    """标记唤醒轮开始（幂等；重复 open 清空旧队列防残留）。"""
    _queues[session_id] = asyncio.Queue()


def is_active(session_id: str) -> bool:
    """会话是否有活跃唤醒轮。"""
    return session_id in _queues


def publish(session_id: str, event: dict) -> None:
    """向订阅者广播一个事件；无订阅会话时静默丢弃（唤醒照常执行）。"""
    queue = _queues.get(session_id)
    if queue is not None:
        queue.put_nowait(event)


def close_stream(session_id: str) -> None:
    """标记唤醒轮结束：置入结束哨兵唤醒订阅者，并注销队列。"""
    queue = _queues.pop(session_id, None)
    if queue is not None:
        queue.put_nowait(None)


async def subscribe(session_id: str) -> AsyncGenerator[dict, None]:
    """订阅唤醒事件流（SSE 生成器）。

    - 会话无活跃唤醒轮：立即产出 done 结束（前端快速收尾，避免悬挂连接）；
    - 有活跃轮：转发事件直到 close 哨兵，随后补一个 done 保证前端收尾。
    """
    queue = _queues.get(session_id)
    if queue is None:
        yield {"event": "done", "data": {}}
        return
    while True:
        event = await queue.get()
        if event is None:
            break
        yield event
    yield {"event": "done", "data": {}}
