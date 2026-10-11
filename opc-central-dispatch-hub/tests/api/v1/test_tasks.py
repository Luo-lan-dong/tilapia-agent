"""任务 API 接口测试。"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planning import DeepSeekAPIError, TaskPlanResult
from app.api.v1.endpoints.tasks import create_task, get_task, list_tasks
from app.core.errors import BusinessError, TaskDispatchError, TaskNotFoundError
from app.main import create_app
from app.models.agent_node import AgentNode
from app.models.task import Task, TaskStatus
from app.models.task_building_session import TaskBuildingSession
from app.models.task_orchestrator_todo import TaskOrchestratorTodo
from app.schemas.task import TaskCreateRequest
from app.services import orchestrator_todo_service
from app.services.task_service import TaskService


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
    """创建 HTTP 级异步测试客户端。

    通过 dependency_overrides 将 ``get_async_session`` 替换为测试会话，
    避免 HTTP 请求命中全局 ``AsyncSessionLocal`` 的连接池，防止跨测试的
    事件循环污染。
    """
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
def mock_planning_agent() -> AsyncMock:
    """提供模拟的任务规划 Agent。"""
    mock_agent = AsyncMock()
    mock_agent.plan.return_value = TaskPlanResult(
        task_name="官网落地页开发",
        description="使用 React 开发响应式官网落地页",
        agent_type="code",
    )
    return mock_agent


async def test_create_task_success(
    db_session: AsyncSession,
    mock_planning_agent: AsyncMock,
) -> None:
    """测试创建任务成功，数据正确落库。"""
    request = TaskCreateRequest(requirement="帮我开发一个官网落地页")

    with patch(
        "app.services.task_service.TaskPlanningAgent",
        return_value=mock_planning_agent,
    ):
        response = await create_task(request, db_session, TaskService())

    assert response.code == 0
    assert response.message == "ok"
    assert response.data is not None
    assert response.data.title == "官网落地页开发"
    assert response.data.agent_type == "code"
    assert response.data.status == "pending"
    assert response.data.progress == 0
    assert response.data.source == "manual"

    task = await db_session.get(Task, response.data.id)
    assert task is not None
    assert task.title == "官网落地页开发"
    assert task.status == "pending"
    assert task.progress == 0
    assert task.source == "manual"


async def test_create_task_with_specification(db_session: AsyncSession) -> None:
    """测试携带 specification 时直接保存为 spec_chat 来源任务。"""
    request = TaskCreateRequest(
        requirement="开发一个登录接口",
        specification={
            "spec": "# Spec\n开发登录接口",
            "tasks": "# Tasks\n1. 设计数据库",
            "checklist": "# Checklist\n- 接口可访问",
        },
    )

    response = await create_task(request, db_session, TaskService())

    assert response.code == 0
    assert response.data is not None
    assert response.data.title == "开发一个登录接口"
    assert response.data.agent_type == "code"
    assert response.data.source == "spec_chat"
    assert response.data.specification is not None
    assert response.data.specification["spec"] == "# Spec\n开发登录接口"

    task = await db_session.get(Task, response.data.id)
    assert task is not None
    assert task.source == "spec_chat"
    assert task.specification is not None


async def test_create_task_missing_requirement() -> None:
    """测试缺少 requirement 字段触发 Pydantic 校验错误（对应接口 422）。"""
    with pytest.raises(ValidationError) as exc_info:
        TaskCreateRequest(requirement="")

    assert "requirement" in str(exc_info.value)


async def test_create_task_deepseek_failure(db_session: AsyncSession) -> None:
    """测试 DeepSeek 调用失败返回 503。"""
    mock_agent = AsyncMock()
    mock_agent.plan.side_effect = DeepSeekAPIError(
        status_code=503,
        message="Service Unavailable",
    )

    request = TaskCreateRequest(requirement="帮我开发一个官网落地页")
    with (
        patch(
            "app.services.task_service.TaskPlanningAgent",
            return_value=mock_agent,
        ),
        pytest.raises(BusinessError) as exc_info,
    ):
        await create_task(request, db_session, TaskService())

    assert exc_info.value.status_code == 503
    assert "不可用" in str(exc_info.value.detail)


async def test_get_task_success(db_session: AsyncSession) -> None:
    """测试查询已存在任务返回 200。"""
    task = Task(
        title="测试任务",
        description="测试描述",
        agent_type="programming",
        status="pending",
        progress=0,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    response = await get_task(task.id, db_session, TaskService())

    assert response.code == 0
    assert response.data is not None
    assert response.data.id == task.id
    assert response.data.title == "测试任务"
    assert response.data.status == "pending"


async def test_get_task_not_found(db_session: AsyncSession) -> None:
    """测试查询不存在任务抛出 TaskNotFoundError。"""
    task_id = uuid.uuid4()

    with pytest.raises(TaskNotFoundError) as exc_info:
        await get_task(task_id, db_session, TaskService())

    assert str(task_id) in str(exc_info.value)


async def test_http_create_task_missing_requirement(http_client: AsyncClient) -> None:
    """HTTP 层测试：缺少 requirement 返回 422。"""
    response = await http_client.post("/api/v1/tasks", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] != 0
    assert body["data"] is None
    assert "请求参数校验失败" in body["message"]


async def test_http_create_task_deepseek_failure(http_client: AsyncClient) -> None:
    """HTTP 层测试：DeepSeek 调用失败返回 503。"""
    mock_agent = AsyncMock()
    mock_agent.plan.side_effect = DeepSeekAPIError(
        status_code=503,
        message="Service Unavailable",
    )

    with patch(
        "app.services.task_service.TaskPlanningAgent",
        return_value=mock_agent,
    ):
        response = await http_client.post(
            "/api/v1/tasks",
            json={"requirement": "帮我开发一个官网落地页"},
        )

    assert response.status_code == 503
    body = response.json()
    assert body["code"] != 0
    assert body["data"] is None
    assert "不可用" in body["message"]


async def test_http_get_task_not_found(http_client: AsyncClient) -> None:
    """HTTP 层测试：查询不存在任务返回 404。"""
    task_id = uuid.uuid4()

    response = await http_client.get(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] != 0
    assert body["data"] is None
    assert "不存在" in body["message"]


async def _seed_tasks(
    db_session: AsyncSession,
    specs: list[tuple[str, str, int]],
) -> None:
    """批量创建测试任务。"""
    for title, agent_type, progress in specs:
        status = "completed" if progress == 100 else "pending"
        db_session.add(
            Task(
                title=title,
                agent_type=agent_type,
                status=status,
                progress=progress,
            )
        )
    await db_session.commit()


async def test_list_tasks_pagination(db_session: AsyncSession) -> None:
    """测试任务列表分页返回正确结构与总数。"""
    agent_type = "test-task-list-programming"
    specs = [(f"任务 {i}", agent_type, 0) for i in range(3)]
    await _seed_tasks(db_session, specs)

    response = await list_tasks(None, agent_type, 2, 0, db_session, TaskService())

    assert response.code == 0
    assert response.data is not None
    assert response.data.pagination.total == 3
    assert len(response.data.items) == 2


async def test_list_tasks_filters(db_session: AsyncSession) -> None:
    """测试按状态与 Agent 类型筛选任务列表。"""
    agent_type = "test-task-list-copywriting"
    await _seed_tasks(
        db_session,
        [
            ("待处理任务", agent_type, 0),
            ("已完成任务", agent_type, 100),
        ],
    )

    status_response = await list_tasks("completed", agent_type, 20, 0, db_session, TaskService())
    assert status_response.data is not None
    assert status_response.data.pagination.total == 1
    assert status_response.data.items[0].status == "completed"

    type_response = await list_tasks(None, agent_type, 20, 0, db_session, TaskService())
    assert type_response.data is not None
    assert type_response.data.pagination.total == 2


async def test_list_tasks_limit_and_offset(db_session: AsyncSession) -> None:
    """测试 limit 与 offset 组合分页。"""
    agent_type = "test-task-list-offset"
    specs = [(f"任务 {i}", agent_type, 0) for i in range(5)]
    await _seed_tasks(db_session, specs)

    first_page = await list_tasks(None, agent_type, 2, 0, db_session, TaskService())
    second_page = await list_tasks(None, agent_type, 2, 2, db_session, TaskService())

    assert first_page.data is not None
    assert second_page.data is not None
    assert len(first_page.data.items) == 2
    assert len(second_page.data.items) == 2
    assert first_page.data.items[0].id != second_page.data.items[0].id


async def test_create_task_with_parent_task_root(db_session: AsyncSession) -> None:
    """测试迭代链创建：父任务即链根时 root_task_id 解析为父任务 ID。"""
    parent = Task(
        title="根任务",
        description="初始开发",
        agent_type="programming",
        status="completed",
        progress=100,
        specification={"spec": "# Spec"},
        source="spec_chat",
    )
    db_session.add(parent)
    await db_session.commit()
    await db_session.refresh(parent)

    request = TaskCreateRequest(
        requirement="给登录页加一个记住我功能",
        specification={
            "spec": "# Spec\n登录页修改",
            "tasks": "# Tasks",
            "checklist": "# Checklist",
        },
        parent_task_id=parent.id,
        change_brief="登录页新增记住我复选框",
        execution_mode="plan",
    )
    response = await create_task(request, db_session, TaskService())

    assert response.code == 0
    assert response.data is not None
    assert response.data.parent_task_id == parent.id
    assert response.data.execution_mode == "plan"
    assert response.data.change_brief == "登录页新增记住我复选框"

    task = await db_session.get(Task, response.data.id)
    assert task is not None
    assert task.parent_task_id == parent.id
    # 父任务即链根：root_task_id 解析为父任务 ID
    assert task.root_task_id == parent.id
    assert task.execution_mode == "plan"


async def test_create_task_with_parent_task_inherits_root(
    db_session: AsyncSession,
) -> None:
    """测试迭代链创建：父任务自身在链上时继承其 root_task_id。"""
    root = Task(
        title="链根任务",
        agent_type="programming",
        status="completed",
        specification={"spec": "# Spec"},
        source="spec_chat",
    )
    db_session.add(root)
    await db_session.commit()
    await db_session.refresh(root)

    parent = Task(
        title="第二轮任务",
        agent_type="programming",
        status="completed",
        specification={"spec": "# Spec"},
        source="spec_chat",
        parent_task_id=root.id,
        root_task_id=root.id,
    )
    db_session.add(parent)
    await db_session.commit()
    await db_session.refresh(parent)

    request = TaskCreateRequest(
        requirement="第三轮迭代",
        specification={
            "spec": "# Spec\n第三轮",
            "tasks": "# Tasks",
            "checklist": "# Checklist",
        },
        parent_task_id=parent.id,
    )
    response = await create_task(request, db_session, TaskService())

    assert response.code == 0
    assert response.data is not None
    assert response.data.parent_task_id == parent.id
    # execution_mode 缺省按 build 处理
    assert response.data.execution_mode == "build"

    task = await db_session.get(Task, response.data.id)
    assert task is not None
    # 父任务自身在链上：root_task_id 继承父任务的链根
    assert task.root_task_id == root.id


async def test_create_task_parent_not_found(db_session: AsyncSession) -> None:
    """测试迭代链创建：父任务不存在时抛出 TaskNotFoundError。"""
    request = TaskCreateRequest(
        requirement="挂靠不存在父任务的迭代",
        specification={
            "spec": "# Spec",
            "tasks": "# Tasks",
            "checklist": "# Checklist",
        },
        parent_task_id=uuid.uuid4(),
    )

    with pytest.raises(TaskNotFoundError) as exc_info:
        await create_task(request, db_session, TaskService())

    assert str(request.parent_task_id) in str(exc_info.value)


# -------------------------- 任务删除 -------------------------- #


async def _make_task_for_delete(
    db_session: AsyncSession,
    *,
    status_value: str = TaskStatus.PENDING.value,
) -> Task:
    """创建用于删除测试的任务。"""
    task = Task(
        title=f"del-{status_value}-{uuid.uuid4().hex[:6]}",
        description="desc",
        agent_type="code",
        status=status_value,
        progress=0,
        source="manual",
        specification={
            "spec": "# Spec",
            "tasks": "# Tasks",
            "checklist": "# Checklist",
        },
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


async def test_delete_task_pending_success_unbinds_session(
    db_session: AsyncSession,
) -> None:
    """删除 pending 任务成功，且解除任务构建会话的 task_id 关联。"""
    task = await _make_task_for_delete(db_session)
    building_session = TaskBuildingSession(title="会话", messages=[], task_id=task.id)
    db_session.add(building_session)
    await db_session.commit()

    await TaskService().delete_task(db_session, task.id)

    assert await db_session.get(Task, task.id) is None
    fresh_session = await db_session.get(TaskBuildingSession, building_session.id)
    assert fresh_session is not None
    assert fresh_session.task_id is None


async def test_delete_task_completed_success(db_session: AsyncSession) -> None:
    """终态（completed）任务允许删除。"""
    task = await _make_task_for_delete(db_session, status_value=TaskStatus.COMPLETED.value)

    await TaskService().delete_task(db_session, task.id)

    assert await db_session.get(Task, task.id) is None


async def test_delete_task_running_rejected(db_session: AsyncSession) -> None:
    """running 状态任务禁止删除。"""
    task = await _make_task_for_delete(db_session, status_value=TaskStatus.RUNNING.value)

    with pytest.raises(TaskDispatchError, match="禁止删除"):
        await TaskService().delete_task(db_session, task.id)

    # 任务仍在，未被误删
    assert await db_session.get(Task, task.id) is not None


async def test_delete_task_assigned_rejected(db_session: AsyncSession) -> None:
    """assigned 状态任务禁止删除。"""
    task = await _make_task_for_delete(db_session, status_value=TaskStatus.ASSIGNED.value)

    with pytest.raises(TaskDispatchError, match="禁止删除"):
        await TaskService().delete_task(db_session, task.id)


async def test_delete_task_not_found(db_session: AsyncSession) -> None:
    """删除不存在的任务抛 TaskNotFoundError。"""
    with pytest.raises(TaskNotFoundError):
        await TaskService().delete_task(db_session, uuid.uuid4())


async def test_steer_task_api_contract(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """UT-M5a-9（API 级，ADR-061 M5a-steer）：发言端点契约——200/409/422/404。

    回归对象：此前只有**服务级**用例（`test_task_steer.py`），HTTP 层状态码与请求体
    校验没有被端到端断言过。
    """
    task = Task(
        id=uuid.uuid4(),
        title="发言端点测试",
        agent_type="programming",
        status=TaskStatus.RUNNING.value,
    )
    db_session.add(task)
    await db_session.commit()
    try:
        # 200：服务成功时返回送达结果（此处只验 HTTP 契约，节点推送由服务级用例覆盖）
        with patch.object(
            TaskService,
            "steer_task",
            AsyncMock(return_value={"task_id": str(task.id), "delivered": True, "text_length": 5}),
        ):
            ok = await http_client.post(
                f"/api/v1/tasks/{task.id}/steer", json={"text": "先跑测试"}
            )
        assert ok.status_code == 200
        assert ok.json()["data"]["delivered"] is True

        # 422：空文本被请求体校验挡下（不进入服务层）
        empty = await http_client.post(f"/api/v1/tasks/{task.id}/steer", json={"text": ""})
        assert empty.status_code == 422

        # 409：任务不在可发言状态（未分配节点/非执行态）→ 明确的业务错误，而非静默成功
        conflict = await http_client.post(
            f"/api/v1/tasks/{task.id}/steer", json={"text": "再补一句"}
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == 201002  # TASK_STATE_NOT_ALLOWED

        # 404：任务不存在
        missing = await http_client.post(
            f"/api/v1/tasks/{uuid.uuid4()}/steer", json={"text": "无人可发"}
        )
        assert missing.status_code == 404
    finally:
        await db_session.execute(delete(Task).where(Task.id == task.id))
        await db_session.commit()


async def test_node_chat_api_contract(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """UT-H1（ADR-064 M1）：node-chat 端点契约——202/409/404/422。

    202 由 mock 服务返回 {reply_to}；409/404/422 走真实服务分支
    （running 拒绝 / 任务不存在 / 节点离线）。
    """
    node = AgentNode(
        name="离线节点",
        agent_type="programming",
        status="offline",
        client_id="nc1",
        registration_token=uuid.uuid4().hex,
    )
    running_task = Task(
        id=uuid.uuid4(),
        title="续聊端点测试",
        agent_type="programming",
        status=TaskStatus.RUNNING.value,
    )
    idle_task = Task(
        id=uuid.uuid4(),
        title="续聊端点空闲任务",
        agent_type="programming",
        status=TaskStatus.COMPLETED.value,
    )
    db_session.add_all([node, running_task, idle_task])
    await db_session.commit()
    try:
        # 202：空闲 + 服务成功（此处只验 HTTP 契约与 reply_to 透传，WS 下发由服务级用例覆盖）
        reply_to = str(uuid.uuid4())
        with patch.object(
            TaskService,
            "node_chat",
            AsyncMock(return_value={"reply_to": reply_to}),
        ):
            accepted = await http_client.post(
                f"/api/v1/tasks/{idle_task.id}/node-chat",
                json={"node_id": str(node.id), "text": "继续聊"},
            )
        assert accepted.status_code == 202
        assert accepted.json()["data"]["reply_to"] == reply_to

        # 422：请求体空文本被校验挡下（不进入服务层）
        empty = await http_client.post(
            f"/api/v1/tasks/{idle_task.id}/node-chat",
            json={"node_id": str(node.id), "text": ""},
        )
        assert empty.status_code == 422

        # 409：任务执行中（running）→ 真实服务拒绝，应走 steer
        conflict = await http_client.post(
            f"/api/v1/tasks/{running_task.id}/node-chat",
            json={"node_id": str(node.id), "text": "在吗"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == 201002  # TASK_STATE_NOT_ALLOWED

        # 422：节点离线（agent_nodes.status=offline）
        offline = await http_client.post(
            f"/api/v1/tasks/{idle_task.id}/node-chat",
            json={"node_id": str(node.id), "text": "在吗"},
        )
        assert offline.status_code == 422
        assert offline.json()["code"] == 202002  # AGENT_NODE_OFFLINE

        # 404：任务不存在
        missing = await http_client.post(
            f"/api/v1/tasks/{uuid.uuid4()}/node-chat",
            json={"node_id": str(node.id), "text": "在吗"},
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == 201001  # TASK_NOT_FOUND
    finally:
        await db_session.execute(
            delete(Task).where(Task.id.in_([running_task.id, idle_task.id]))
        )
        await db_session.execute(delete(AgentNode).where(AgentNode.id == node.id))
        await db_session.commit()


async def test_list_orchestrator_todos_endpoint(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """UT-M5d-5（ADR-061 M5d）：下段端点按 seq 升序返回，并给出待办/完成计数。"""
    task = Task(
        id=uuid.uuid4(),
        title="中枢待办端点测试",
        agent_type="programming",
        status=TaskStatus.PENDING.value,
    )
    db_session.add(task)
    await db_session.commit()
    try:
        rows = await orchestrator_todo_service.add_todos(db_session, task.id, ["先规划", "再实现"])
        await orchestrator_todo_service.update_todo(
            db_session, task.id, rows[0].id, status="completed"
        )

        response = await http_client.get(f"/api/v1/tasks/{task.id}/orchestrator-todos")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["task_id"] == str(task.id)
        assert [item["content"] for item in data["items"]] == ["先规划", "再实现"]
        assert [item["status"] for item in data["items"]] == ["completed", "pending"]
        assert data["pending_count"] == 1
        assert data["completed_count"] == 1
    finally:
        await db_session.execute(
            delete(TaskOrchestratorTodo).where(TaskOrchestratorTodo.task_id == task.id)
        )
        await db_session.execute(delete(Task).where(Task.id == task.id))
        await db_session.commit()


async def test_list_orchestrator_todos_empty_and_not_found(
    http_client: AsyncClient, db_session: AsyncSession
) -> None:
    """UT-M5d-5b：中枢尚未写待办 → 空列表（非 404）；任务不存在 → 404。"""
    task = Task(
        id=uuid.uuid4(),
        title="中枢待办空态测试",
        agent_type="programming",
        status=TaskStatus.PENDING.value,
    )
    db_session.add(task)
    await db_session.commit()
    try:
        response = await http_client.get(f"/api/v1/tasks/{task.id}/orchestrator-todos")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["items"] == []
        assert data["pending_count"] == 0
        assert data["completed_count"] == 0
    finally:
        await db_session.execute(delete(Task).where(Task.id == task.id))
        await db_session.commit()

    missing = await http_client.get(f"/api/v1/tasks/{uuid.uuid4()}/orchestrator-todos")
    assert missing.status_code == 404
    assert missing.json()["code"] == 201001
