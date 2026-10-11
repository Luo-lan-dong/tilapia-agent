"""任务构建会话 API 测试。"""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.models.task_building_session import TaskBuildingSession
from app.schemas.common import PaginationInfo
from app.schemas.task_building import TaskBuildingSessionListResponse
from app.services.task_building_session_service import TaskBuildingSessionService
from tests.api.v1.test_tasks import _noop_start_background_task


@pytest_asyncio.fixture
async def http_client() -> AsyncClient:
    """创建 HTTP 级异步测试客户端。"""
    with (
        patch("app.main.start_scheduler", _noop_start_background_task),
        patch("app.main.start_heartbeat_monitor", _noop_start_background_task),
    ):
        app = create_app()
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


def _sample_session(session_id: uuid.UUID | None = None) -> TaskBuildingSession:
    """构造一个标准会话模型实例（与 service 真实返回类型一致）。"""
    return TaskBuildingSession(
        id=session_id or uuid.uuid4(),
        title="开发登录接口",
        messages=[{"role": "user", "content": "开发一个登录接口"}],
        specification={
            "spec": "# Spec",
            "tasks": "# Tasks",
            "checklist": "# Checklist",
        },
        mode="thinking",
        model="deepseek-test",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def test_create_session(http_client: AsyncClient) -> None:
    """测试创建会话接口。"""
    session_id = uuid.uuid4()

    with patch.object(
        TaskBuildingSessionService,
        "create",
        return_value=_sample_session(session_id),
    ):
        response = await http_client.post(
            "/api/v1/tasks/build/sessions",
            json={
                "title": "开发登录接口",
                "messages": [{"role": "user", "content": "开发一个登录接口"}],
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["title"] == "开发登录接口"
    assert body["data"]["mode"] == "thinking"


async def test_list_sessions(http_client: AsyncClient) -> None:
    """测试查询会话列表接口。"""
    with patch.object(
        TaskBuildingSessionService,
        "list",
        return_value=TaskBuildingSessionListResponse(
            items=[_sample_session()],
            pagination=PaginationInfo(
                page=1,
                page_size=20,
                total=1,
                total_pages=1,
            ),
        ),
    ):
        response = await http_client.get("/api/v1/tasks/build/sessions")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert len(body["data"]["items"]) == 1
    assert body["data"]["pagination"]["total"] == 1


async def test_get_session(http_client: AsyncClient) -> None:
    """测试查询单个会话详情。"""
    session_id = uuid.uuid4()

    with patch.object(
        TaskBuildingSessionService,
        "get",
        return_value=_sample_session(session_id),
    ):
        response = await http_client.get(f"/api/v1/tasks/build/sessions/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["id"] == str(session_id)


async def test_get_session_not_found(http_client: AsyncClient) -> None:
    """测试查询不存在的会话返回 404。"""
    with patch.object(
        TaskBuildingSessionService,
        "get",
        return_value=None,
    ):
        response = await http_client.get(f"/api/v1/tasks/build/sessions/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] != 0


async def test_update_session(http_client: AsyncClient) -> None:
    """测试更新会话。"""
    session_id = uuid.uuid4()

    with patch.object(
        TaskBuildingSessionService,
        "update",
        return_value=_sample_session(session_id),
    ):
        response = await http_client.patch(
            f"/api/v1/tasks/build/sessions/{session_id}",
            json={
                "title": "新标题",
                "mode": "fast",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0


async def test_delete_session(http_client: AsyncClient) -> None:
    """测试删除会话。"""
    session_id = uuid.uuid4()

    with patch.object(
        TaskBuildingSessionService,
        "delete",
        return_value=True,
    ):
        response = await http_client.delete(f"/api/v1/tasks/build/sessions/{session_id}")

    assert response.status_code == 204


# ---------------------------------------------------------------------------
# ADR-040：工具轨迹落库协议（_persist_chat_exchange）
# ---------------------------------------------------------------------------


def _make_capture_service() -> tuple[Any, Any]:
    """构造捕获 update 调用的 session service mock。"""
    captured: dict = {}

    class _CaptureService:
        async def update(self, session_id: uuid.UUID, request: Any) -> Any:
            captured["session_id"] = session_id
            captured["request"] = request
            return _sample_session(session_id)

    return _CaptureService(), captured


async def test_persist_chat_exchange_trajectory_order() -> None:
    """UT-17（ADR-040）：工具轨迹按序插入用户历史与最终回复之间。"""
    from app.api.v1.endpoints.task_building import _persist_chat_exchange

    service, captured = _make_capture_service()
    session_id = uuid.uuid4()
    trajectory = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "function": {"name": "find_idle_nodes", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "节点 code 空闲"},
    ]

    await _persist_chat_exchange(
        session_service=service,  # type: ignore[arg-type]
        session_id=session_id,
        messages=[{"role": "user", "content": "看下空闲节点"}],
        ai_reply="当前空闲节点是 code",
        specification=None,
        mode="fast",
        model="test-model",
        rounds=[{"thinking": "思考", "steps": [{"step_id": "s1"}]}],
        trajectory_messages=trajectory,
    )

    messages = captured["request"].messages
    roles = [m.role for m in messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    # 轨迹消息字段原样保留
    assert messages[1].tool_calls[0]["id"] == "call-1"
    assert messages[2].tool_call_id == "call-1"
    assert messages[2].content == "节点 code 空闲"
    # 回合拆分（会话恢复层叠修复）：思考/步骤按回合附加到轨迹 assistant 消息
    assert messages[1].thinking == "思考"
    assert messages[1].steps == [{"step_id": "s1"}]
    # 最终回复不再携带回合内容
    assert messages[3].content == "当前空闲节点是 code"
    assert messages[3].thinking is None
    assert messages[3].steps is None


async def test_persist_chat_exchange_multi_round_alignment() -> None:
    """UT-17b（回合拆分修复）：多回合 thinking/steps 按顺序对齐到各工具轮。"""
    from app.api.v1.endpoints.task_building import _persist_chat_exchange

    service, captured = _make_capture_service()
    trajectory = [
        {"role": "assistant", "content": "先侦察", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "assistant", "content": "再建站", "tool_calls": [{"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c2", "content": "r2"},
    ]

    await _persist_chat_exchange(
        session_service=service,  # type: ignore[arg-type]
        session_id=uuid.uuid4(),
        messages=[{"role": "user", "content": "建站"}],
        ai_reply="站点已创建",
        specification=None,
        mode="thinking",
        model="test-model",
        rounds=[
            {"thinking": "第一轮思考", "steps": [{"step_id": "s1"}]},
            {"thinking": "第二轮思考", "steps": [{"step_id": "s2"}, {"step_id": "s3"}]},
        ],
        trajectory_messages=trajectory,
    )

    messages = captured["request"].messages
    assert [m.role for m in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    assert messages[1].thinking == "第一轮思考"
    assert [s["step_id"] for s in messages[1].steps] == ["s1"]
    assert messages[3].thinking == "第二轮思考"
    assert [s["step_id"] for s in messages[3].steps] == ["s2", "s3"]
    # 最终回复轮无剩余回合 → 无 thinking/steps
    assert messages[5].content == "站点已创建"
    assert messages[5].thinking is None


async def test_persist_chat_exchange_remaining_round_to_final() -> None:
    """UT-17c（回合拆分修复）：正文轮的思考（无工具）附加到最终 assistant 消息。"""
    from app.api.v1.endpoints.task_building import _persist_chat_exchange

    service, captured = _make_capture_service()
    trajectory = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
    ]

    await _persist_chat_exchange(
        session_service=service,  # type: ignore[arg-type]
        session_id=uuid.uuid4(),
        messages=[{"role": "user", "content": "hi"}],
        ai_reply="最终回复",
        specification=None,
        mode="thinking",
        model="test-model",
        rounds=[
            {"thinking": "工具轮思考", "steps": [{"step_id": "s1"}]},
            {"thinking": "正文轮思考", "steps": []},
        ],
        trajectory_messages=trajectory,
    )

    messages = captured["request"].messages
    assert messages[1].thinking == "工具轮思考"
    assert messages[1].steps == [{"step_id": "s1"}]
    assert messages[3].content == "最终回复"
    assert messages[3].thinking == "正文轮思考"


async def test_persist_chat_exchange_specification_not_overwritten() -> None:
    """UT-18（ADR-040）：本轮未触发文档生成时三件套不被覆盖（回归）。"""
    from app.api.v1.endpoints.task_building import _persist_chat_exchange

    service, captured = _make_capture_service()

    await _persist_chat_exchange(
        session_service=service,  # type: ignore[arg-type]
        session_id=uuid.uuid4(),
        messages=[{"role": "user", "content": "hi"}],
        ai_reply="回复",
        specification=None,
        mode="fast",
        model="test-model",
        trajectory_messages=[
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c"}]},
            {"role": "tool", "tool_call_id": "c", "content": "r"},
        ],
    )

    assert captured["request"].specification is None


async def test_persist_chat_exchange_no_trajectory_compatible() -> None:
    """UT-19（ADR-040）：纯文本轮（无轨迹）落库结构与现状一致（回归）。"""
    from app.api.v1.endpoints.task_building import _persist_chat_exchange

    service, captured = _make_capture_service()

    await _persist_chat_exchange(
        session_service=service,  # type: ignore[arg-type]
        session_id=uuid.uuid4(),
        messages=[{"role": "user", "content": "hi"}],
        ai_reply="回复",
        specification=None,
        mode="fast",
        model="test-model",
    )

    messages = captured["request"].messages
    assert [(m.role, m.content) for m in messages] == [("user", "hi"), ("assistant", "回复")]
