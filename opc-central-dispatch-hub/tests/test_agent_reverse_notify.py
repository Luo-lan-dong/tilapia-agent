"""中枢地址注册表与重启反连服务测试（ADR-035）。

覆盖 Harness UT-7（register 带 reported_address 三列落库）、UT-8（重复注册
api_token 不覆盖，ADR-020 幂等语义）、UT-9（旧 Agent 无上报列保持 NULL）、
UT-10（启动反连任务对 offline 节点并发 ≤5 调用 notify）、UT-11（反连失败
静默不影响启动）、UT-12（中枢无 hub_public_url 时跳过反连调用）。

反连任务经独立 AsyncSessionLocal 会话读取数据，种子节点需真实提交（区别于
db_session 的 savepoint 模式），fixture 内自行清理。
"""

import asyncio
import uuid

import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy import delete, select

from app.models.agent_node import AgentNode
from app.models.base import AsyncSessionLocal, engine
from app.models.system_settings import SystemSettings
from app.schemas.agent_node import AgentNodeRegisterRequest, ReportedAddress
from app.services.agent_node_service import AgentNodeService
from app.services.agent_reverse_notify import run_reverse_notify


@pytest_asyncio.fixture(autouse=True)
async def _fresh_global_engine_pool():
    """每个用例前清空全局引擎连接池。

    本模块位于套件末尾，全局池中残留着此前模块（已关闭事件循环）的
    连接，直接复用会抛 ``'NoneType' object has no attribute 'send'``；
    ``run_reverse_notify`` 按启动安全设计会吞掉该异常导致静默跳过，
    因此必须先 dispose 让连接在当前事件循环中重建。
    """
    await engine.dispose()
    yield


def _make_node(
    client_id: str,
    status: str,
    reported_ip: str | None = None,
    reported_api_port: int | None = None,
    api_token: str | None = None,
) -> AgentNode:
    """构造测试节点（registration_token 唯一）。"""
    return AgentNode(
        id=uuid.uuid4(),
        name=f"node-{client_id}",
        agent_type="code",
        status=status,
        registration_token=f"rt-{uuid.uuid4().hex}",
        client_id=client_id,
        reported_ip=reported_ip,
        reported_api_port=reported_api_port,
        api_token=api_token,
    )


@pytest.fixture
async def seeded_reverse_notify_env():
    """提交反连测试种子数据：settings 行 + 7 个 offline 有地址节点 + 排除项。

    排除项：1 个 online 有地址节点（已自愈不通知）+ 1 个 offline 无地址节点
    （旧版 Agent 跳过）。清理时恢复 settings 原值并删除种子节点。
    """
    targets = [
        _make_node(f"cid-off-{i}", "offline", "192.168.1.10", 18080 + i, f"tok-{i}")
        for i in range(7)
    ]
    excluded_online = _make_node("cid-online", "idle", "192.168.1.99", 18080, "tok-online")
    excluded_no_addr = _make_node("cid-noaddr", "offline")

    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
        created_settings = False
        if row is None:
            row = SystemSettings(id=uuid.uuid4())
            session.add(row)
            created_settings = True
        original_url = row.hub_public_url
        row.hub_public_url = "http://10.0.0.1:8021"
        session.add_all(targets)
        session.add_all([excluded_online, excluded_no_addr])
        await session.commit()
        node_ids = [n.id for n in (*targets, excluded_online, excluded_no_addr)]

    yield targets

    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentNode).where(AgentNode.id.in_(node_ids)))
        row = (await session.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
        if row is not None:
            if created_settings:
                await session.delete(row)
            else:
                row.hub_public_url = original_url
        await session.commit()


# ---------------- UT-7 / UT-8 / UT-9：注册上报落库（savepoint 会话） ----------------


async def test_register_with_reported_address_persists_columns(db_session):
    """UT-7：register 带 reported_address → 三列完整落库。"""
    request = AgentNodeRegisterRequest(
        name="节点A",
        agent_type="code",
        client_id="cid-ut7",
        reported_address=ReportedAddress(ip="192.168.1.50", api_port=18080, api_token="tok-ut7"),
    )

    response = await AgentNodeService().register_agent_node(db_session, request)

    node = (
        await db_session.execute(select(AgentNode).where(AgentNode.client_id == "cid-ut7"))
    ).scalar_one()
    assert node.id == response.id
    assert node.reported_ip == "192.168.1.50"
    assert node.reported_api_port == 18080
    assert node.api_token == "tok-ut7"


async def test_reregister_keeps_first_api_token(db_session):
    """UT-8：重复注册（同 client_id）→ api_token 不被覆盖，ip/port 跟随更新。"""
    service = AgentNodeService()
    first = AgentNodeRegisterRequest(
        name="节点A",
        agent_type="code",
        client_id="cid-ut8",
        reported_address=ReportedAddress(
            ip="192.168.1.50", api_port=18080, api_token="first-token"
        ),
    )
    await service.register_agent_node(db_session, first)

    second = AgentNodeRegisterRequest(
        name="节点A",
        agent_type="code",
        client_id="cid-ut8",
        reported_address=ReportedAddress(
            ip="192.168.1.51", api_port=18081, api_token="rogue-token"
        ),
    )
    await service.register_agent_node(db_session, second)

    node = (
        await db_session.execute(select(AgentNode).where(AgentNode.client_id == "cid-ut8"))
    ).scalar_one()
    assert node.api_token == "first-token"  # 仅首次落库
    assert node.reported_ip == "192.168.1.51"  # 地址跟随最新上报
    assert node.reported_api_port == 18081


async def test_register_without_reported_address_keeps_null(db_session):
    """UT-9：旧版 Agent 注册（无 reported_address）→ 列保持 NULL，注册成功。"""
    request = AgentNodeRegisterRequest(name="旧节点", agent_type="code", client_id="cid-ut9")

    response = await AgentNodeService().register_agent_node(db_session, request)

    node = (
        await db_session.execute(select(AgentNode).where(AgentNode.client_id == "cid-ut9"))
    ).scalar_one()
    assert node.id == response.id
    assert node.reported_ip is None
    assert node.reported_api_port is None
    assert node.api_token is None


# ---------------- UT-10 / UT-11 / UT-12：启动反连任务 ----------------


def _notify_route(ip: str, port: int) -> str:
    """构造 Agent notify 端点 URL。"""
    return f"http://{ip}:{port}/internal/hub-notify"


async def _assert_bearer_token(
    request: httpx.Request, expected_tokens: set[str], seen_tokens: list[str]
) -> None:
    """校验反连请求携带 Bearer token 并记录。"""
    auth = request.headers.get("Authorization", "")
    assert auth.startswith("Bearer ")
    seen_tokens.append(auth.removeprefix("Bearer "))
    assert seen_tokens[-1] in expected_tokens


@respx.mock
async def test_reverse_notify_targets_offline_nodes_with_concurrency_limit(
    seeded_reverse_notify_env, monkeypatch
):
    """UT-10：仅 offline 且有地址节点被通知；并发不超过上限（默认 5）。"""
    monkeypatch.setattr("app.services.agent_reverse_notify._STARTUP_DELAY_SECONDS", 0.01)
    seen_urls: list[str] = []
    seen_tokens: list[str] = []
    expected_tokens = {f"tok-{i}" for i in range(7)}
    active = {"count": 0, "max": 0}
    lock = asyncio.Lock()

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录调用并追踪并发峰值。"""
        seen_urls.append(str(request.url))
        await _assert_bearer_token(request, expected_tokens, seen_tokens)
        async with lock:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        await asyncio.sleep(0.05)
        async with lock:
            active["count"] -= 1
        return httpx.Response(200, json={"code": 0, "message": "ok"})

    for i in range(7):
        respx.post(_notify_route("192.168.1.10", 18080 + i)).mock(side_effect=handler)

    await run_reverse_notify()

    # 7 个 offline 有地址节点全部收到通知；online 与无地址节点被排除
    assert len(seen_urls) == 7
    assert all("192.168.1.99" not in url for url in seen_urls)
    # 并发峰值不超过 REVERSE_NOTIFY_CONCURRENCY（默认 5）
    assert active["max"] <= 5


@respx.mock
async def test_reverse_notify_failure_is_silent(seeded_reverse_notify_env, monkeypatch):
    """UT-11：部分节点 notify 失败（不可达）→ 静默继续，其余节点正常通知。"""
    monkeypatch.setattr("app.services.agent_reverse_notify._STARTUP_DELAY_SECONDS", 0.01)
    ok_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        """记录成功的调用。"""
        ok_urls.append(str(request.url))
        return httpx.Response(200, json={"code": 0, "message": "ok"})

    for i in range(1, 7):
        respx.post(_notify_route("192.168.1.10", 18080 + i)).mock(side_effect=handler)
    # 首个节点端口拒绝连接（模拟防火墙未放行）
    respx.post(_notify_route("192.168.1.10", 18080)).mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    await run_reverse_notify()  # 不抛异常

    assert len(ok_urls) == 6


@pytest.mark.asyncio
async def test_reverse_notify_skipped_without_hub_url(monkeypatch):
    """UT-12：system_settings 无 hub_public_url → 跳过全部反连调用（零开销）。"""
    monkeypatch.setattr(
        "app.services.agent_reverse_notify._STARTUP_DELAY_SECONDS", 0.01
    )
    called: list[tuple] = []

    async def _spy_notify_agent(*args, **kwargs):
        """记录任何 notify 调用（不应发生）。"""
        called.append(args)
        return True

    monkeypatch.setattr(
        "app.services.agent_reverse_notify._notify_agent", _spy_notify_agent
    )

    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
        created = False
        if row is None:
            row = SystemSettings(id=uuid.uuid4())
            session.add(row)
            created = True
        original_url = row.hub_public_url
        row.hub_public_url = None
        await session.commit()

    try:
        await run_reverse_notify()
        assert called == []
    finally:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
            if row is not None:
                if created:
                    await session.delete(row)
                else:
                    row.hub_public_url = original_url
            await session.commit()
