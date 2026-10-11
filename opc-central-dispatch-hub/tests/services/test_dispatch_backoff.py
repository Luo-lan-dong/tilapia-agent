"""派发回避记录测试（ADR-032 / ADR-059 卫生项）：TTL 语义与全局过期清收。

背景：``_rejections`` 是进程内字典，原先只有「改派成功」「任务终态（completed/
failed）」「卡死兜底」三条路径调用 ``clear_rejections``。走到终态**之外**的任务
（用户取消、拒绝后长期 pending、任务被删除）不会触发清理，条目会随运行时长
单调累积——本文件锁定新增的全局 ``prune_expired`` 兜底行为。
"""

from __future__ import annotations

import time

from app.services import dispatch_backoff
from app.services.dispatch_backoff import REJECTION_TTL_SECONDS


def setup_function() -> None:
    """每个用例前清空全局字典，避免相互影响。"""
    dispatch_backoff.reset_all()


def teardown_function() -> None:
    dispatch_backoff.reset_all()


def test_rejection_marks_node_excluded() -> None:
    """记录拒单后该节点对任务进入回避集合。"""
    dispatch_backoff.record_rejection("task-1", "node-a")

    assert dispatch_backoff.excluded_nodes("task-1") == {"node-a"}
    assert dispatch_backoff.excluded_nodes("task-2") == set()


def test_clear_rejections_removes_task_entries() -> None:
    """改派成功/终态清理后不再回避。"""
    dispatch_backoff.record_rejection("task-1", "node-a")
    dispatch_backoff.record_rejection("task-1", "node-b")

    dispatch_backoff.clear_rejections("task-1")

    assert dispatch_backoff.excluded_nodes("task-1") == set()


def test_excluded_nodes_expires_only_own_task() -> None:
    """``excluded_nodes`` 只顺带清理被查询任务自己的过期条目。"""
    stale = time.monotonic() - REJECTION_TTL_SECONDS - 1
    # 直接写入过期时间戳（绕过 record_rejection 的当前时钟）
    dispatch_backoff._rejections["task-stale"] = {"node-x": stale}
    dispatch_backoff._rejections["task-fresh"] = {"node-y": stale}

    assert dispatch_backoff.excluded_nodes("task-stale") == set()
    # 未查询的任务仍在字典里（正是需要全局兜底的原因）
    assert "task-fresh" in dispatch_backoff._rejections


def test_prune_expired_removes_stale_entries_globally() -> None:
    """全局清收：不依赖任务是否被再次查询，过期条目一律回收。"""
    now = time.monotonic()
    dispatch_backoff._rejections["task-a"] = {"node-1": now - REJECTION_TTL_SECONDS - 1}
    dispatch_backoff._rejections["task-b"] = {"node-2": now - REJECTION_TTL_SECONDS - 1}

    removed = dispatch_backoff.prune_expired(now=now)

    assert removed == 2
    assert dispatch_backoff._rejections == {}


def test_prune_expired_keeps_fresh_entries() -> None:
    """未过期条目必须保留（正在回避中的任务不能被提前放开）。"""
    now = time.monotonic()
    dispatch_backoff._rejections["task-a"] = {"node-1": now - 1.0}
    dispatch_backoff._rejections["task-b"] = {"node-2": now - REJECTION_TTL_SECONDS - 1}

    removed = dispatch_backoff.prune_expired(now=now)

    assert removed == 1
    assert dispatch_backoff.excluded_nodes("task-a") == {"node-1"}
    assert "task-b" not in dispatch_backoff._rejections


def test_prune_expired_partial_task_keeps_remaining_nodes() -> None:
    """同一任务部分条目过期时，只删过期的、保留仍在 TTL 内的节点。"""
    now = time.monotonic()
    dispatch_backoff._rejections["task-a"] = {
        "node-old": now - REJECTION_TTL_SECONDS - 5,
        "node-new": now - 1.0,
    }

    removed = dispatch_backoff.prune_expired(now=now)

    assert removed == 1
    assert dispatch_backoff.excluded_nodes("task-a") == {"node-new"}


def test_prune_expired_on_empty_dict_is_noop() -> None:
    """空字典下不发散、不报错。"""
    assert dispatch_backoff.prune_expired(now=time.monotonic()) == 0
    assert dispatch_backoff._rejections == {}


def test_prune_expired_defaults_to_current_clock() -> None:
    """缺省时钟参数时也能正确清收（调度循环的调用形态）。"""
    dispatch_backoff._rejections["task-a"] = {
        "node-1": time.monotonic() - REJECTION_TTL_SECONDS - 1
    }

    assert dispatch_backoff.prune_expired() == 1
    assert dispatch_backoff._rejections == {}
