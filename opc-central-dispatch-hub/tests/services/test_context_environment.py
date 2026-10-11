"""环境上下文服务单元测试（ADR-042）。"""

from datetime import datetime, timedelta, timezone

from app.services.context_environment_service import (
    build_environment_block,
    build_environment_update_message,
    query_cluster_summary,
)

_TZ_CN = timezone(timedelta(hours=8))


def _fixed_now() -> datetime:
    """固定测试时刻：2026-09-21（星期一）21:00 +08:00。"""
    return datetime(2026, 9, 21, 21, 30, tzinfo=_TZ_CN)


def test_environment_block_time_format() -> None:
    """UT-1：时间行含日期、星期、小时、时区。"""
    block = build_environment_block(None, None, now=_fixed_now())
    assert "<environment>" in block and "</environment>" in block
    assert "2026-09-21" in block
    assert "星期一" in block
    assert "21时" in block
    assert "UTC+08:00" in block


def test_environment_block_with_cluster_summary() -> None:
    """集群概况行正常拼接。"""
    block = build_environment_block(
        "2 个节点在线——code 类型 2 个（busy 1 / idle 1）",
        "deepseek-v4-flash",
        now=_fixed_now(),
    )
    assert "集群概况：2 个节点在线" in block
    assert "对话模型：deepseek-v4-flash" in block


def test_environment_block_degrades_without_cluster() -> None:
    """UT-3：集群概况为 None 时块仅含时间行，不抛异常。"""
    block = build_environment_block(None, None, now=_fixed_now())
    assert "集群概况" not in block
    assert "对话模型" not in block


def test_environment_block_hour_granularity() -> None:
    """时间粒度到小时：同小时内不同分钟生成相同块（增量不泛滥）。"""
    a = build_environment_block(None, None, now=datetime(2026, 9, 21, 21, 0, tzinfo=_TZ_CN))
    b = build_environment_block(None, None, now=datetime(2026, 9, 21, 21, 59, tzinfo=_TZ_CN))
    assert a == b


def test_environment_update_message() -> None:
    """UT-5：增量消息为 role=system 且含更新标签。"""
    message = build_environment_update_message("<environment>NEW</environment>")
    assert message["role"] == "system"
    assert "<environment-update>" in message["content"]
    assert "NEW" in message["content"]


async def test_query_cluster_summary_aggregates(db_session) -> None:
    """UT-2：在线节点按类型/状态聚合，离线不计。"""
    from app.models.agent_node import AgentNode

    db_session.add_all(
        [
            AgentNode(
                name="a",
                agent_type="code",
                status="idle",
                registration_token="t1",
            ),
            AgentNode(
                name="b",
                agent_type="code",
                status="busy",
                registration_token="t2",
            ),
            AgentNode(
                name="c",
                agent_type="code",
                status="offline",
                registration_token="t3",
            ),
        ]
    )
    await db_session.commit()

    summary = await query_cluster_summary(db_session)
    assert summary is not None
    assert "2 个节点在线" in summary
    assert "code 类型 2 个" in summary
    assert "offline" not in summary


async def test_query_cluster_summary_failure_degrades(db_session, monkeypatch) -> None:
    """UT-3：查询异常时返回 None（环境块降级为仅时间）。"""
    from app.services import context_environment_service as mod

    async def _boom(*_a: object, **_k: object):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "select", _boom)
    assert await mod.query_cluster_summary(db_session) is None
