"""TilacodeClient 单元测试：基于 respx mock httpx 传输层。"""

import json

import httpx
import pytest
import respx

from app.executor.tilacode_client import TilacodeAPIError, TilacodeClient

BASE_URL = "http://127.0.0.1:4096"


@pytest.fixture
async def client() -> TilacodeClient:
    """提供测试用客户端实例，测试结束后关闭。"""
    c = TilacodeClient(BASE_URL, lambda: "secret-password", "/workspace/task-1")
    yield c
    await c.close()


@respx.mock
async def test_request_success_returns_full_payload(client: TilacodeClient) -> None:
    """2xx 响应应返回完整 JSON 字典（含 data 键）。"""
    route = respx.post(f"{BASE_URL}/api/session").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ses_1", "agent": "build"}})
    )
    result = await client._request("POST", "/api/session", json_body={"id": "ses_1"})
    assert route.called
    assert result == {"data": {"id": "ses_1", "agent": "build"}}
    assert "data" in result


@respx.mock
async def test_request_error_raises_with_context(client: TilacodeClient) -> None:
    """4xx 响应应抛出 TilacodeAPIError，且信息包含 error.name。"""
    respx.get(f"{BASE_URL}/api/session/ses_missing/history").mock(
        return_value=httpx.Response(
            404, json={"error": {"name": "NotFoundError", "message": "session not found"}}
        )
    )
    with pytest.raises(TilacodeAPIError) as exc_info:
        await client._request("GET", "/api/session/ses_missing/history", params={"limit": 10})
    error = exc_info.value
    assert error.status == 404
    assert error.error_name == "NotFoundError"
    assert "NotFoundError" in str(error)


@respx.mock
async def test_create_session_request_body(client: TilacodeClient) -> None:
    """create_session 请求体应包含 id/agent/model/location。"""
    route = respx.post(f"{BASE_URL}/api/session").mock(
        return_value=httpx.Response(200, json={"data": {"id": "ses_task_t1", "agent": "plan"}})
    )
    await client.create_session(
        session_id="ses_task_t1",
        agent="plan",
        directory="/workspace/task-1",
    )
    body = json.loads(route.calls.last.request.content)
    assert body["id"] == "ses_task_t1"
    assert body["agent"] == "plan"
    assert body["model"] == {"providerID": "opencode", "id": "mimo-v2.5-free"}
    assert body["location"] == {"directory": "/workspace/task-1"}
    # create_session 请求头应携带该任务目录（URL 编码），且不应污染客户端全局头
    request_headers = route.calls.last.request.headers
    assert request_headers["x-tilacode-directory"] == "%2Fworkspace%2Ftask-1"
    assert "x-tilacode-directory" not in client._client.headers


@respx.mock
async def test_switch_agent_and_prompt_and_interrupt(client: TilacodeClient) -> None:
    """switch_agent / send_prompt / interrupt 应调用对应端点。"""
    agent_route = respx.post(f"{BASE_URL}/api/session/ses_1/agent").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    prompt_route = respx.post(f"{BASE_URL}/api/session/ses_1/prompt").mock(
        return_value=httpx.Response(200, json={"data": {"sessionID": "ses_1"}})
    )
    interrupt_route = respx.post(f"{BASE_URL}/api/session/ses_1/interrupt").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    await client.switch_agent("ses_1", "build")
    assert json.loads(agent_route.calls.last.request.content) == {"agent": "build"}

    result = await client.send_prompt("ses_1", "帮我实现登录接口")
    assert result == {"data": {"sessionID": "ses_1"}}
    assert json.loads(prompt_route.calls.last.request.content) == {
        "prompt": {"text": "帮我实现登录接口"},
        "delivery": "steer",
    }

    await client.interrupt("ses_1")
    assert interrupt_route.called


@respx.mock
async def test_is_session_active(client: TilacodeClient) -> None:
    """session_id 在 active map 中返回 True，不在则 False。"""
    respx.get(f"{BASE_URL}/api/session/active").mock(
        return_value=httpx.Response(200, json={"data": {"ses_1": {"type": "running"}}})
    )
    assert await client.is_session_active("ses_1") is True
    assert await client.is_session_active("ses_other") is False


@respx.mock
async def test_permission_reply(client: TilacodeClient) -> None:
    """permission_reply 应 POST {reply: once} 到权限回复端点。"""
    route = respx.post(f"{BASE_URL}/api/session/ses_1/permission/per_xxx/reply").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    await client.permission_reply("ses_1", "per_xxx")
    assert route.called
    assert json.loads(route.calls.last.request.content) == {"reply": "once"}


@respx.mock
async def test_stream_session_events_parses_data_lines(client: TilacodeClient) -> None:
    """SSE 流应解析 data: 行、跳过空行与非 data 行。"""
    sse_text = (
        ": keepalive\n"
        "\n"
        'data: {"id": 1, "type": "session.next.step.started", "data": {}}\n'
        "\n"
        "data: not-a-json\n"
        "\n"
        'data: {"id": 2, "type": "session.next.tool.called", "data": {"name": "bash"}}\n'
        "event: custom\n"
    )
    route = respx.get(f"{BASE_URL}/api/session/ses_1/event").mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, text=sse_text
        )
    )
    events = [e async for e in client.stream_session_events("ses_1")]

    assert route.called
    # Authorization 与目录头必须随流式请求携带
    request_headers = route.calls.last.request.headers
    assert request_headers["authorization"].startswith("Basic ")
    assert request_headers["x-tilacode-directory"] == "%2Fworkspace%2Ftask-1"
    # 非 data 行、空行、非法 JSON 均被跳过，仅产出两条合法事件
    assert [e["id"] for e in events] == [1, 2]
    assert events[1]["data"] == {"name": "bash"}


@respx.mock
async def test_stream_session_events_with_after_param(client: TilacodeClient) -> None:
    """after 非 None 时应携带 query 参数用于断线续传。"""
    route = respx.get(f"{BASE_URL}/api/session/ses_1/event", params={"after": "42"}).mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, text='data: {"id": 43}\n\n'
        )
    )
    events = [e async for e in client.stream_session_events("ses_1", after=42)]
    assert route.called
    assert events == [{"id": 43}]


@respx.mock
async def test_stream_instance_events(client: TilacodeClient) -> None:
    """实例级 SSE（/api/event）应可解析权限事件。"""
    permission_event = (
        'data: {"id": 9, "type": "permission.v2.asked", '
        '"data": {"id": "per_1", "sessionID": "ses_1", "action": "bash", '
        '"resources": ["rm -rf /tmp/x"]}}\n\n'
    )
    route = respx.get(f"{BASE_URL}/api/event").mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, text=permission_event
        )
    )
    events = [e async for e in client.stream_instance_events()]
    assert route.called
    assert events[0]["type"] == "permission.v2.asked"
    assert events[0]["data"]["id"] == "per_1"


@respx.mock
async def test_stream_request_error_ends_iteration(client: TilacodeClient) -> None:
    """网络异常（RequestError）时流应静默结束而非抛出。"""
    respx.get(f"{BASE_URL}/api/session/ses_1/event").mock(side_effect=httpx.ConnectError("boom"))
    events = [e async for e in client.stream_session_events("ses_1")]
    assert events == []


@respx.mock
async def test_ensure_integration_key_success(
    client: TilacodeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """注入凭据应 POST key 到 connect/key 端点，成功后不重试。"""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.executor.tilacode_client.asyncio.sleep", fake_sleep)
    route = respx.post(f"{BASE_URL}/api/integration/deepseek/connect/key").mock(
        return_value=httpx.Response(204)
    )
    await client.ensure_integration_key("deepseek", "sk-secret")

    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {"key": "sk-secret"}
    assert delays == []


@respx.mock
async def test_ensure_integration_key_retries_on_500(
    client: TilacodeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """location 初始化竞态返回 500 时应按退避重试，恢复后注入成功。"""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.executor.tilacode_client.asyncio.sleep", fake_sleep)
    route = respx.post(f"{BASE_URL}/api/integration/deepseek/connect/key").mock(
        side_effect=[
            httpx.Response(
                500, json={"error": {"name": "Error", "message": "Key method not found: deepseek"}}
            ),
            httpx.Response(
                500, json={"error": {"name": "Error", "message": "Key method not found: deepseek"}}
            ),
            httpx.Response(204),
        ]
    )
    await client.ensure_integration_key("deepseek", "sk-secret")

    assert route.call_count == 3
    assert delays == [2.0, 4.0]


@respx.mock
async def test_ensure_integration_key_raises_after_retries_exhausted(
    client: TilacodeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连续 500 超过最大尝试次数后应抛出 TilacodeAPIError。"""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.executor.tilacode_client.asyncio.sleep", fake_sleep)
    route = respx.post(f"{BASE_URL}/api/integration/deepseek/connect/key").mock(
        return_value=httpx.Response(500, json={"error": {"name": "Error", "message": "boom"}})
    )
    with pytest.raises(TilacodeAPIError) as exc_info:
        await client.ensure_integration_key("deepseek", "sk-secret")

    assert exc_info.value.status == 500
    assert route.call_count == 4
    assert delays == [2.0, 4.0, 8.0]


@respx.mock
async def test_ensure_integration_key_does_not_retry_non_500(
    client: TilacodeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非 500 错误（如 400 认证失败）不应重试，立即抛出。"""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("app.executor.tilacode_client.asyncio.sleep", fake_sleep)
    route = respx.post(f"{BASE_URL}/api/integration/deepseek/connect/key").mock(
        return_value=httpx.Response(
            400, json={"error": {"name": "InvalidRequestError", "message": "Authentication failed"}}
        )
    )
    with pytest.raises(TilacodeAPIError) as exc_info:
        await client.ensure_integration_key("deepseek", "sk-secret")

    assert exc_info.value.status == 400
    assert route.call_count == 1
    assert delays == []


@respx.mock
async def test_provider_snapshot_parses_model_list(client: TilacodeClient) -> None:
    """provider_snapshot 应读 GET /api/model 并按 providerID 分组（2026-10-03 契约勘误）。

    回归对象：初版实现读 GET /api/provider 且期望 ``{data: {providers: [...]}}``，
    与真实响应（``{location, data: [Provider.Info]}``，数组、无 models 字段）不符，
    解析恒落空导致节点上「模型目录为空」假阴性。
    """
    route = respx.get(f"{BASE_URL}/api/model").mock(
        return_value=httpx.Response(
            200,
            json={
                "location": {"directory": "/workspace/task-1", "project": {"id": "global"}},
                "data": [
                    {
                        "id": "deepseek-chat",
                        "providerID": "hub-deepseek-t1",
                        "name": "DeepSeek Chat",
                    },
                    {
                        "id": "deepseek-reasoner",
                        "providerID": "hub-deepseek-t1",
                        "name": "DeepSeek R1",
                    },
                    {"id": "mimo-v2.5-free", "providerID": "opencode", "name": "MiMo Free"},
                    {"id": "bad-item"},  # 缺 providerID → 忽略
                ],
            },
        )
    )
    snapshot = await client.provider_snapshot(directory="/workspace/task-1")
    assert route.called
    request_headers = route.calls.last.request.headers
    assert request_headers["x-tilacode-directory"] == "%2Fworkspace%2Ftask-1"
    assert snapshot == {
        "hub-deepseek-t1": ["deepseek-chat", "deepseek-reasoner"],
        "opencode": ["mimo-v2.5-free"],
    }


@respx.mock
async def test_provider_snapshot_unexpected_shape_returns_empty(client: TilacodeClient) -> None:
    """响应结构不符合预期（data 非数组）时应返回空字典，由调用方判「目录为空」。"""
    respx.get(f"{BASE_URL}/api/model").mock(
        return_value=httpx.Response(200, json={"location": {}, "data": None})
    )
    snapshot = await client.provider_snapshot(directory="/workspace/task-1")
    assert snapshot == {}
