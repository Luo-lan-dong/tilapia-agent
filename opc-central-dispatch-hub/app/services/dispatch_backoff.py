"""派发回避记录（ADR-032）：节点拒绝任务后的短时回避与改派。

进程内字典实现：``task_id → {node_id: rejected_at_epoch}``。中枢为单进程
部署，无需跨实例共享；记录随任务改派成功清除，或超过 TTL（默认 5 分钟）
自动过期——过期后节点重新可被该任务选中（资源/槽位可能已恢复）。
"""

from __future__ import annotations

import time

# 回避记录 TTL（秒）：超过该时长的拒绝记录失效
REJECTION_TTL_SECONDS: float = 300.0

# task_id → {node_id: rejected_at_epoch}
_rejections: dict[str, dict[str, float]] = {}


def record_rejection(task_id: str, node_id: str) -> None:
    """记录一次节点拒绝：该任务在本 TTL 内回避此节点。"""
    _rejections.setdefault(task_id, {})[node_id] = time.monotonic()


def excluded_nodes(task_id: str) -> set[str]:
    """返回任务当前应回避的节点集合（自动清理过期记录）。

    注意：这里只清理**本任务**的过期条目。任务在不活跃后（终态、或长期无人
    再过问）不会再被调用，其条目不会被本函数回收——由调度循环周期性调用
    :func:`prune_expired` 做全局兜底，避免进程内字典长期膨胀。
    """
    entries = _rejections.get(task_id)
    if not entries:
        return set()
    now = time.monotonic()
    expired = [node_id for node_id, ts in entries.items() if now - ts > REJECTION_TTL_SECONDS]
    for node_id in expired:
        del entries[node_id]
    if not entries:
        _rejections.pop(task_id, None)
        return set()
    return set(entries)


def prune_expired(now: float | None = None) -> int:
    """全局清理所有任务的过期回避记录，返回删除的条目数。

    由调度循环每轮调用（见 ``scheduler.run_scheduler``）：回避记录的生命周期
    与任务是否仍在被调度无关，若不全局清理，已进入终态且不再被扫描的任务会
    把条目永久留在进程内字典里（``task_id → node_id`` 单调累积）。

    Args:
        now: 当前单调时钟（测试可注入）；缺省取 ``time.monotonic()``。

    Returns:
        被删除的（任务, 节点）条目数。
    """
    reference = time.monotonic() if now is None else now
    removed = 0
    for task_id in list(_rejections):
        entries = _rejections[task_id]
        for node_id in [
            nid for nid, ts in entries.items() if reference - ts > REJECTION_TTL_SECONDS
        ]:
            del entries[node_id]
            removed += 1
        if not entries:
            _rejections.pop(task_id, None)
    return removed


def clear_rejections(task_id: str) -> None:
    """任务改派成功（或终态）后清除其回避记录。"""
    _rejections.pop(task_id, None)


def reset_all() -> None:
    """清空全部记录（测试隔离用）。"""
    _rejections.clear()
