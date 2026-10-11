"""节点 Agent 会话轨迹镜像端点测试（ADR-063 M1）。

覆盖 HTTP 契约：①正常 POST 返回 ``accepted``/``ignored``（含幂等重复上报）；
②GET 按 ``after_seq`` 增量取回、按 ``node_id`` 过滤、``limit`` 越界夹取不报错；
③任务不存在时两个端点都 404；④``rows`` 超过 200 条由请求体校验挡下（422）。
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models.task import Task, TaskStatus
from app.models.task_agent_trace import TaskAgentTrace

TRACE_URL = "/api/v1/tasks/{task_id}/agent-trace"


async def _noop_background_loop(stop_event: asyncio.Event) -> None:
    """占位后台循环，避免真实调度器/心跳任务占用事件循环。"""
    await stop_event.wait()


def _noop_start_background_task() -> tuple[asyncio.Event, asyncio.Task[None]]:
    """创建占位后台任务，用于替换真实的 lifespan 后台任务。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(_noop_background_loop(stop_event))
    return stop_event, task


@pytest_asyncio.fixture
async def http_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP 级异步测试客户端（复用测试会话，避免命中全局连接池）。"""
    from app.api.deps import get_async_session

    async def _override_get_async_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    with (
        patch("app.main.start_scheduler", _noop_start_background_task),
        patch("app.main.start_heartbeat_monitor", _noop_start_background_task),
    ):
        app = create_app()
        app.dependency_overrides[get_async_session] = _override_get_async_session
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


@pytest.fixture
async def trace_task(db_session: AsyncSession) -> AsyncGenerator[Task, None]:
    """落库一个执行中的任务（用例后连同轨迹行一起清理）。"""
    task = Task(
        id=uuid.uuid4(),
        title="轨迹端点测试任务",
        agent_type="programming",
        status=TaskStatus.RUNNING.value,
    )
    db_session.add(task)
    await db_session.commit()
    yield task
    await db_session.execute(delete(TaskAgentTrace).where(TaskAgentTrace.task_id == task.id))
    await db_session.execute(delete(Task).where(Task.id == task.id))
    await db_session.commit()


async def test_append_agent_trace_returns_accepted_and_ignored(
    http_client: AsyncClient, trace_task: Task
) -> None:
    """①POST 正常返回 accepted/ignored（非法 kind 计入 ignored，不报 4xx）。"""
    url = TRACE_URL.format(task_id=trace_task.id)
    response = await http_client.post(
        url,
        json={
            "node_id": "node-1",
            "session_id": "sess-1",
            "rows": [
                {"seq": 1, "kind": "status", "payload": {"text": "任务已开始"}},
                {"seq": 2, "kind": "text", "payload": {"text": "读取仓库结构"}},
                # 非法 kind 属于数据问题：服务层丢弃并计入 ignored，端点不报 4xx
                {"seq": 3, "kind": "stdout", "payload": {"text": "非法类型"}},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"] == {"accepted": 2, "ignored": 1}

    # 幂等：同一 (task_id, seq) 再报一次，全部计入 ignored
    again = await http_client.post(
        url,
        json={
            "node_id": "node-1",
            "session_id": "sess-1",
            "rows": [
                {"seq": 1, "kind": "status", "payload": {"text": "任务已开始"}},
                {"seq": 2, "kind": "text", "payload": {"text": "读取仓库结构"}},
            ],
        },
    )
    assert again.status_code == 200
    assert again.json()["data"] == {"accepted": 0, "ignored": 2}


async def test_list_agent_trace_after_seq_pagination_and_node_filter(
    http_client: AsyncClient, trace_task: Task
) -> None:
    """②GET 能按 after_seq 取回（附 node_id 过滤与 limit 越界夹取）。"""
    url = TRACE_URL.format(task_id=trace_task.id)
    for seq in range(1, 6):
        posted = await http_client.post(
            url,
            json={
                "node_id": "node-1",
                "session_id": "sess-1",
                "rows": [{"seq": seq, "kind": "text", "payload": {"text": f"n1-{seq}"}}],
            },
        )
        assert posted.status_code == 200
    posted = await http_client.post(
        url,
        json={
            "node_id": "node-2",
            "session_id": "sess-2",
            "rows": [{"seq": 6, "kind": "reasoning", "payload": {"text": "n2-6"}}],
        },
    )
    assert posted.status_code == 200

    # 全量：seq 升序，payload 已反序列化为对象，created_at 可解析
    all_rows = await http_client.get(url)
    assert all_rows.status_code == 200
    data = all_rows.json()["data"]
    assert [row["seq"] for row in data["rows"]] == [1, 2, 3, 4, 5, 6]
    assert data["has_more"] is False
    assert data["rows"][0]["payload"] == {"text": "n1-1"}
    assert data["rows"][0]["created_at"]

    # after_seq 增量拉取：只返回 seq 更大的行
    delta = await http_client.get(url, params={"after_seq": 4})
    assert delta.status_code == 200
    assert [row["seq"] for row in delta.json()["data"]["rows"]] == [5, 6]
    assert delta.json()["data"]["has_more"] is False

    # node_id 过滤 + limit 分页（has_more 表示后面还有）
    scoped = await http_client.get(url, params={"node_id": "node-2"})
    assert [row["seq"] for row in scoped.json()["data"]["rows"]] == [6]

    paged = await http_client.get(url, params={"after_seq": 1, "limit": 2})
    assert paged.status_code == 200
    assert [row["seq"] for row in paged.json()["data"]["rows"]] == [2, 3]
    assert paged.json()["data"]["has_more"] is True

    # limit 越界夹取：超上限不报错；过小值夹到 1
    over = await http_client.get(url, params={"limit": 100_000})
    assert over.status_code == 200
    assert len(over.json()["data"]["rows"]) == 6
    under = await http_client.get(url, params={"limit": 0})
    assert under.status_code == 200
    assert len(under.json()["data"]["rows"]) == 1


async def test_agent_trace_endpoints_return_404_for_missing_task(
    http_client: AsyncClient,
) -> None:
    """③任务不存在时两个端点都 404（TASK_NOT_FOUND）。"""
    url = TRACE_URL.format(task_id=uuid.uuid4())

    appended = await http_client.post(
        url,
        json={
            "node_id": "node-1",
            "session_id": "sess-1",
            "rows": [{"seq": 1, "kind": "status", "payload": {"text": "无人接收"}}],
        },
    )
    assert appended.status_code == 404
    assert appended.json()["code"] == 201001  # ErrorCode.TASK_NOT_FOUND

    listed = await http_client.get(url)
    assert listed.status_code == 404
    assert listed.json()["code"] == 201001


async def test_append_agent_trace_rejects_more_than_200_rows(
    http_client: AsyncClient, trace_task: Task
) -> None:
    """④rows 超过 200 条属结构性错误 → 422（请求体校验挡下，不进入服务层）。"""
    url = TRACE_URL.format(task_id=trace_task.id)
    response = await http_client.post(
        url,
        json={
            "rows": [
                {"seq": seq, "kind": "text", "payload": {"text": f"第 {seq} 行"}}
                for seq in range(1, 202)
            ]
        },
    )
    assert response.status_code == 422

    # 200 条刚好在边界内（不被 max_length 挡下）
    boundary = await http_client.post(
        url,
        json={
            "rows": [
                {"seq": seq, "kind": "text", "payload": {"text": f"第 {seq} 行"}}
                for seq in range(1, 201)
            ]
        },
    )
    assert boundary.status_code == 200
    assert boundary.json()["data"] == {"accepted": 200, "ignored": 0}

    # seq 必须 ≥ 1（结构校验，与服务层「非法 seq 计 ignored」不冲突：这里是类型/范围错误）
    bad_seq = await http_client.post(
        url,
        json={"rows": [{"seq": 0, "kind": "text", "payload": {"text": "从 0 开始"}}]},
    )
    assert bad_seq.status_code == 422
