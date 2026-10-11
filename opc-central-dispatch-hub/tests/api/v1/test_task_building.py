"""任务构建对话 API 测试。"""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.api.v1.test_tasks import _noop_start_background_task


@pytest_asyncio.fixture
async def http_client() -> AsyncClient:
    """创建 HTTP 级异步测试客户端。"""
    # asyncio_mode=auto 下每个测试都是新事件循环，而 app.models.base.engine
    # 是模块级单例：池内可能残留绑定已关闭 loop 的连接，pre_ping 写入已关闭
    # 的 proactor 会抛 'NoneType' object has no attribute 'send'。先清空连接池，
    # 确保本测试内全部连接在当前 loop 上新建。
    from app.models.base import engine as global_engine

    await global_engine.dispose()
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


def _make_chat_result(ai_reply: str, is_ready_to_create: bool) -> dict:
    """构造 SpecBuilderService.chat 的标准返回结构。"""
    return {
        "ai_reply": ai_reply,
        "specification": {
            "spec": "# Spec\n开发登录接口",
            "tasks": "# Tasks\n1. 设计数据库",
            "checklist": "# Checklist\n- 接口可访问",
        },
        "is_ready_to_create": is_ready_to_create,
        "model": "deepseek-test",
        "mode": "thinking",
    }


async def test_task_build_chat_success(http_client: AsyncClient) -> None:
    """测试 /tasks/build/chat 返回正确的三件套结构。"""
    mock_result = _make_chat_result("请补充技术栈信息", False)

    with patch(
        "app.api.v1.endpoints.task_building.SpecBuilderService.chat",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_chat:
        response = await http_client.post(
            "/api/v1/tasks/build/chat",
            json={
                "messages": [{"role": "user", "content": "开发一个登录接口"}],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["ai_reply"] == "请补充技术栈信息"
    assert body["data"]["specification"]["spec"] == "# Spec\n开发登录接口"
    assert body["data"]["is_ready_to_create"] is False
    assert body["data"]["model"] == "deepseek-test"
    assert body["data"]["mode"] == "thinking"
    assert mock_chat.await_args.kwargs["mode"] == "thinking"


async def test_task_build_chat_with_fast_mode(http_client: AsyncClient) -> None:
    """测试 fast 模式会正确透传给服务层。"""
    mock_result = _make_chat_result("信息已足够，可以创建任务", True)

    with patch(
        "app.api.v1.endpoints.task_building.SpecBuilderService.chat",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_chat:
        response = await http_client.post(
            "/api/v1/tasks/build/chat",
            json={
                "messages": [{"role": "user", "content": "开发一个登录接口"}],
                "mode": "fast",
            },
        )

    assert response.status_code == 200
    assert mock_chat.await_args.kwargs["mode"] == "fast"


async def test_task_build_chat_stream_returns_sse_events(http_client: AsyncClient) -> None:
    """测试 /tasks/build/chat/stream 返回 SSE 事件流。

    ADR-033 起流式端点走 OrchestratorService 统一链路，mock 相应服务层。
    """

    async def mock_stream(*args, **kwargs):
        yield {"event": "meta", "data": {"model": "deepseek-test", "mode": "thinking"}}
        yield {"event": "chunk", "data": {"delta": "好的"}}
        yield {
            "event": "specification",
            "data": {
                "spec": "# Spec",
                "tasks": "# Tasks",
                "checklist": "# Checklist",
            },
        }
        yield {"event": "status", "data": {"is_ready_to_create": True}}
        yield {"event": "done", "data": {}}

    with patch(
        "app.api.v1.endpoints.task_building.OrchestratorService.chat_stream",
        side_effect=mock_stream,
    ):
        response = await http_client.post(
            "/api/v1/tasks/build/chat/stream",
            json={
                "messages": [{"role": "user", "content": "开发一个登录接口"}],
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: meta" in body
    assert "event: chunk" in body
    assert "deepseek-test" in body
    assert "event: specification" in body
    assert "event: done" in body


async def test_stream_persists_steps_and_thinking(http_client: AsyncClient) -> None:
    """ADR-033：技能步骤与思考内容随会话持久化，恢复后仍可展示。

    回归背景：ChatMessage 此前仅有 role/content，Pydantic 验证丢弃 steps，
    thinking 增量从未收集——中枢重启后步骤卡片与思考内容全部消失。
    """

    async def mock_stream(*args, **kwargs):
        yield {"event": "meta", "data": {"model": "deepseek-test", "mode": "thinking"}}
        yield {"event": "thinking", "data": {"delta": "正在思考..."}}
        yield {
            "event": "step_started",
            "data": {
                "step_id": "s1",
                "seq": 1,
                "skill_name": "generate_spec_docs",
                "skill_display_name": "生成三件套规范文档",
                "args_summary": "{}",
            },
        }
        yield {
            "event": "step_finished",
            "data": {
                "step_id": "s1",
                "status": "success",
                "result_summary": "三件套已生成",
                "duration_ms": 27200,
            },
        }
        yield {"event": "chunk", "data": {"delta": "已生成三件套"}}
        yield {"event": "done", "data": {}}

    create_resp = await http_client.post(
        "/api/v1/tasks/build/sessions",
        json={"title": "登录页", "messages": [{"role": "user", "content": "开发登录页"}]},
    )
    assert create_resp.status_code == 201
    session_id = create_resp.json()["data"]["id"]

    with patch(
        "app.api.v1.endpoints.task_building.OrchestratorService.chat_stream",
        side_effect=mock_stream,
    ):
        stream_resp = await http_client.post(
            "/api/v1/tasks/build/chat/stream",
            json={
                "messages": [{"role": "user", "content": "开发登录页"}],
                "session_id": session_id,
            },
        )
    assert stream_resp.status_code == 200

    detail_resp = await http_client.get(f"/api/v1/tasks/build/sessions/{session_id}")
    assert detail_resp.status_code == 200
    messages = detail_resp.json()["data"]["messages"]
    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    assert assistant["content"] == "已生成三件套"
    assert assistant["thinking"] == "正在思考..."
    assert len(assistant["steps"]) == 1
    step = assistant["steps"][0]
    assert step["skill_name"] == "generate_spec_docs"
    assert step["status"] == "success"
    assert step["result_summary"] == "三件套已生成"
    # user 消息不应携带 thinking/steps 键（未设置即不输出）
    user = messages[0]
    assert "thinking" not in user
    assert "steps" not in user


async def test_step_finished_after_new_round_still_marks_success(
    http_client: AsyncClient,
) -> None:
    """问题修复：回合分割后步骤结束事件仍要更新对应步骤卡。

    回归背景：步骤之后到达的思考增量会把收集器切到新回合，
    ``step_finished`` 只查当前回合 → 旧回合步骤永远 ``running``，
    历史会话恢复后步骤卡一直「执行中」。
    """

    async def mock_stream(*args, **kwargs):
        yield {"event": "meta", "data": {"model": "deepseek-test", "mode": "thinking"}}
        yield {"event": "thinking", "data": {"delta": "第一轮思考"}}
        yield {
            "event": "step_started",
            "data": {
                "step_id": "s1",
                "seq": 1,
                "skill_name": "generate_spec_docs",
                "skill_display_name": "生成三件套规范文档",
                "args_summary": "{}",
            },
        }
        # 步骤未结束时收到新的思考增量：开启下一回合并闭合旧回合
        yield {"event": "thinking", "data": {"delta": "第二轮思考"}}
        yield {
            "event": "step_finished",
            "data": {
                "step_id": "s1",
                "status": "success",
                "result_summary": "三件套已生成",
                "duration_ms": 27200,
            },
        }
        yield {"event": "chunk", "data": {"delta": "已生成三件套"}}
        yield {"event": "done", "data": {}}

    create_resp = await http_client.post(
        "/api/v1/tasks/build/sessions",
        json={"title": "回合分割", "messages": [{"role": "user", "content": "开发登录页"}]},
    )
    session_id = create_resp.json()["data"]["id"]

    with patch(
        "app.api.v1.endpoints.task_building.OrchestratorService.chat_stream",
        side_effect=mock_stream,
    ):
        stream_resp = await http_client.post(
            "/api/v1/tasks/build/chat/stream",
            json={
                "messages": [{"role": "user", "content": "开发登录页"}],
                "session_id": session_id,
            },
        )
    assert stream_resp.status_code == 200

    detail_resp = await http_client.get(f"/api/v1/tasks/build/sessions/{session_id}")
    messages = detail_resp.json()["data"]["messages"]
    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    step = assistant["steps"][0]
    assert step["status"] == "success"
    assert step["result_summary"] == "三件套已生成"
    assert step["duration_ms"] == 27200


async def test_stream_persist_finalizes_running_steps(http_client: AsyncClient) -> None:
    """问题修复：流结束时仍未收到 step_finished 的步骤按失败收尾。"""

    async def mock_stream(*args, **kwargs):
        yield {"event": "meta", "data": {"model": "deepseek-test", "mode": "thinking"}}
        yield {"event": "thinking", "data": {"delta": "正在生成..."}}
        yield {
            "event": "step_started",
            "data": {
                "step_id": "s-lost",
                "seq": 1,
                "skill_name": "generate_spec_docs",
                "skill_display_name": "生成三件套规范文档",
                "args_summary": "{}",
            },
        }
        yield {"event": "chunk", "data": {"delta": "本轮结束"}}
        yield {"event": "done", "data": {}}

    create_resp = await http_client.post(
        "/api/v1/tasks/build/sessions",
        json={"title": "丢失结束事件", "messages": [{"role": "user", "content": "开发登录页"}]},
    )
    session_id = create_resp.json()["data"]["id"]

    with patch(
        "app.api.v1.endpoints.task_building.OrchestratorService.chat_stream",
        side_effect=mock_stream,
    ):
        stream_resp = await http_client.post(
            "/api/v1/tasks/build/chat/stream",
            json={
                "messages": [{"role": "user", "content": "开发登录页"}],
                "session_id": session_id,
            },
        )
    assert stream_resp.status_code == 200

    detail_resp = await http_client.get(f"/api/v1/tasks/build/sessions/{session_id}")
    messages = detail_resp.json()["data"]["messages"]
    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    step = assistant["steps"][0]
    assert step["status"] == "failed"
    assert "流中断" in step["result_summary"]


_FAKE_SPEC = {
    "spec": "# 任务规范\n\n开发静态登录页面。",
    "tasks": "# 任务清单\n\n1. 编写 login.html",
    "checklist": "# 验收清单\n\n- 页面可打开",
    # ADR-061 M4：会话持久化会补齐 verify 字段（未生成时为 None）
    "verify": None,
}


class _FakeSpecBuilderAgentForCommand:
    """模拟 SpecBuilderAgent：/spec 命令集成测试用。"""

    def __init__(self, _session: object) -> None:
        pass

    async def chat_stream(self, messages, current_specification, mode="thinking"):  # noqa: ANN001, ANN202
        yield {"event": "chunk", "data": {"delta": "已生成三件套"}}
        yield {"event": "specification", "data": _FAKE_SPEC}
        yield {"event": "status", "data": {"is_ready_to_create": True}}
        yield {"event": "done", "data": {}}


async def test_spec_command_round_persists_specification_and_steps(
    http_client: AsyncClient,
) -> None:
    """ADR-036：/spec 显式命令走真实 Orchestrator 强制生成链路（不经 LLM
    决策轮），specification 与技能步骤随会话持久化。"""
    create_resp = await http_client.post(
        "/api/v1/tasks/build/sessions",
        json={"title": "命令测试", "messages": [{"role": "user", "content": "开发登录页"}]},
    )
    assert create_resp.status_code == 201
    session_id = create_resp.json()["data"]["id"]

    with patch("app.skills.spec_generation.SpecBuilderAgent", _FakeSpecBuilderAgentForCommand):
        stream_resp = await http_client.post(
            "/api/v1/tasks/build/chat/stream",
            json={
                "messages": [{"role": "user", "content": "/spec"}],
                "session_id": session_id,
            },
        )
    assert stream_resp.status_code == 200
    body = stream_resp.text
    assert "event: step_started" in body
    assert "generate_spec_docs" in body
    assert "event: specification" in body
    assert "event: done" in body

    detail_resp = await http_client.get(f"/api/v1/tasks/build/sessions/{session_id}")
    assert detail_resp.status_code == 200
    data = detail_resp.json()["data"]
    assistant = [m for m in data["messages"] if m["role"] == "assistant"][-1]
    # 可见回复 = 技能自身转发的 chunk（无 chunk 时才以结果摘要兜底）
    assert assistant["content"] == "已生成三件套"
    assert len(assistant["steps"]) == 1
    assert assistant["steps"][0]["skill_name"] == "generate_spec_docs"
    assert data["specification"] == _FAKE_SPEC
