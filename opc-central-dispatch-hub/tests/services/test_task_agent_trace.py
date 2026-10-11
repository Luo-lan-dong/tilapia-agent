"""节点 Agent 会话轨迹镜像服务测试（ADR-063 M1）。

覆盖四条硬约束：``(task_id, seq)`` 幂等、单行 8 KB 截断（且仍是合法 JSON）、
单任务 5000 行上限、``after_seq`` 分页 + ``node_id`` 过滤；外加非法 ``kind`` 丢弃。
"""

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskStatus
from app.models.task_agent_trace import (
    AGENT_TRACE_PAYLOAD_MAX_BYTES,
    TaskAgentTrace,
)
from app.services import task_agent_trace_service as trace_module
from app.services.task_agent_trace_service import TaskAgentTraceService


@pytest.fixture
async def trace_task(db_session: AsyncSession) -> AsyncGenerator[Task, None]:
    """落库一个执行中的任务（用例后连同轨迹行一起清理）。"""
    task = Task(
        id=uuid.uuid4(),
        title="轨迹镜像测试任务",
        agent_type="programming",
        status=TaskStatus.RUNNING.value,
    )
    db_session.add(task)
    await db_session.commit()
    yield task
    await db_session.execute(delete(TaskAgentTrace).where(TaskAgentTrace.task_id == task.id))
    await db_session.execute(delete(Task).where(Task.id == task.id))
    await db_session.commit()


async def test_append_rows_is_idempotent_on_task_seq(
    db_session: AsyncSession, trace_task: Task
) -> None:
    """UT-M1-1：同一 ``(task_id, seq)`` 上报两次只落一行（第二次全部 ignored）。"""
    service = TaskAgentTraceService()
    rows = [
        {"seq": 1, "kind": "status", "payload": {"text": "任务已开始"}},
        {"seq": 2, "kind": "text", "payload": {"text": "读取仓库结构"}},
    ]

    first = await service.append_rows(db_session, trace_task.id, "node-1", "sess-1", rows)
    assert first == {"accepted": 2, "ignored": 0}

    second = await service.append_rows(db_session, trace_task.id, "node-1", "sess-1", rows)
    assert second == {"accepted": 0, "ignored": 2}

    listed = await service.list_rows(db_session, trace_task.id)
    assert [row["seq"] for row in listed["rows"]] == [1, 2]
    assert await service.count_rows(db_session, trace_task.id) == 2


async def test_append_rows_truncates_oversized_payload_to_valid_json(
    db_session: AsyncSession, trace_task: Task
) -> None:
    """UT-M1-2：单行 payload 超 8 KB 被截断为「头 + truncated 标记 + 尾」，不超上限。"""
    service = TaskAgentTraceService()
    marker = '{"truncated":true}'
    # 用 ASCII 保证字节数与字符数一致（断言阈值时不受多字节干扰）
    huge = {"text": "A" * 40_000}

    result = await service.append_rows(
        db_session,
        trace_task.id,
        "node-1",
        "sess-1",
        [{"seq": 1, "kind": "reasoning", "payload": huge}],
    )
    assert result == {"accepted": 1, "ignored": 0}

    listed = await service.list_rows(db_session, trace_task.id)
    # 落库文本 = 原 JSON 的头 6144 字节 + 标记 + 尾 1536 字节（读取侧反序列化兜底）
    assert len(listed["rows"][0]["payload"].encode("utf-8")) <= AGENT_TRACE_PAYLOAD_MAX_BYTES

    raw = await db_session.scalar(
        select(TaskAgentTrace.payload).where(TaskAgentTrace.task_id == trace_task.id)
    )
    assert raw.count(marker) == 1
    head, tail = raw.split(marker)
    # 头 6144 字节、尾 1536 字节（字节边界切分，ASCII 下等长）
    assert head.startswith('{"text": "AAAA')
    assert len(head.encode("utf-8")) == 6144
    assert tail.endswith('AA"}')
    assert len(tail.encode("utf-8")) == 1536


async def test_append_rows_rejects_when_task_row_limit_reached(
    db_session: AsyncSession, trace_task: Task, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UT-M1-3：单任务行数达上限后拒绝新增（全部 ignored + warning），不真插 5000 行。"""
    # 把上限压到 3（服务层读取的是模块常量，patch 即可等价覆盖 5000 行边界）
    monkeypatch.setattr(trace_module, "AGENT_TRACE_MAX_ROWS_PER_TASK", 3)
    service = TaskAgentTraceService()

    accepted = await service.append_rows(
        db_session,
        trace_task.id,
        "node-1",
        "sess-1",
        [
            {"seq": 1, "kind": "status", "payload": {"text": "a"}},
            {"seq": 2, "kind": "text", "payload": {"text": "b"}},
            {"seq": 3, "kind": "tool", "payload": {"text": "c"}},
        ],
    )
    assert accepted == {"accepted": 3, "ignored": 0}

    rejected = await service.append_rows(
        db_session,
        trace_task.id,
        "node-1",
        "sess-1",
        [
            {"seq": 4, "kind": "text", "payload": {"text": "d"}},
            {"seq": 5, "kind": "text", "payload": {"text": "e"}},
        ],
    )
    assert rejected == {"accepted": 0, "ignored": 2}
    assert await service.count_rows(db_session, trace_task.id) == 3


async def test_append_rows_stops_exactly_at_row_limit(
    db_session: AsyncSession, trace_task: Task, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UT-M1-3b：批次内跨过上限时，只接受到上限为止，其余计入 ignored。"""
    monkeypatch.setattr(trace_module, "AGENT_TRACE_MAX_ROWS_PER_TASK", 3)
    service = TaskAgentTraceService()

    result = await service.append_rows(
        db_session,
        trace_task.id,
        "node-1",
        "sess-1",
        [{"seq": seq, "kind": "text", "payload": {"text": f"第 {seq} 行"}} for seq in range(1, 6)],
    )
    assert result == {"accepted": 3, "ignored": 2}
    assert await service.count_rows(db_session, trace_task.id) == 3


async def test_list_rows_after_seq_pagination_and_node_filter(
    db_session: AsyncSession, trace_task: Task
) -> None:
    """UT-M1-4：``after_seq`` 增量分页 + ``node_id`` 过滤 + ``limit`` 上限。"""
    service = TaskAgentTraceService()
    await service.append_rows(
        db_session,
        trace_task.id,
        "node-1",
        "sess-1",
        [{"seq": seq, "kind": "text", "payload": {"text": f"n1-{seq}"}} for seq in range(1, 6)],
    )
    await service.append_rows(
        db_session,
        trace_task.id,
        "node-2",
        "sess-2",
        [
            {"seq": seq, "kind": "reasoning", "payload": {"text": f"n2-{seq}"}}
            for seq in range(6, 11)
        ],
    )

    page = await service.list_rows(db_session, trace_task.id, limit=3)
    assert [row["seq"] for row in page["rows"]] == [1, 2, 3]
    assert page["has_more"] is True

    tail = await service.list_rows(db_session, trace_task.id, after_seq=8, limit=3)
    assert [row["seq"] for row in tail["rows"]] == [9, 10]
    assert tail["has_more"] is False

    node_scoped = await service.list_rows(db_session, trace_task.id, node_id="node-2")
    assert [row["seq"] for row in node_scoped["rows"]] == [6, 7, 8, 9, 10]
    assert all(row["node_id"] == "node-2" for row in node_scoped["rows"])

    # limit 上限 1000：越界值被夹取而不是照单全收
    assert await service.list_rows(db_session, trace_task.id, limit=100_000) is not None


async def test_append_rows_drops_invalid_kind(db_session: AsyncSession, trace_task: Task) -> None:
    """UT-M1-5：非白名单 kind（含大小写/空白/非字符串）整行丢弃并计入 ignored。"""
    service = TaskAgentTraceService()

    result = await service.append_rows(
        db_session,
        trace_task.id,
        "node-1",
        "sess-1",
        [
            {"seq": 1, "kind": "status", "payload": {"text": "合法"}},
            {"seq": 2, "kind": "stdout", "payload": {"text": "非法类型"}},
            {"seq": 3, "kind": "TOOL", "payload": {"text": "大小写不匹配"}},
            {"seq": 4, "kind": None, "payload": {"text": "缺 kind"}},
            {"seq": 5, "kind": "usage", "payload": {"tokens": 12}},
        ],
    )
    assert result == {"accepted": 2, "ignored": 3}

    listed = await service.list_rows(db_session, trace_task.id)
    assert [row["kind"] for row in listed["rows"]] == ["status", "usage"]
