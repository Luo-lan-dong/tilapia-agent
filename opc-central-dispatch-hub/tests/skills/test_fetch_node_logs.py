"""节点日志拉取技能测试（ADR-047 UT-12）。"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest

from app.services.tool_call_log_service import NodeLogFetchError
from app.skills.base import RESULT_EVENT
from app.skills.fetch_node_logs import FetchNodeLogsSkill


async def _collect(
    gen: AsyncGenerator[dict[str, Any], None],
) -> tuple[Any, list[dict[str, Any]]]:
    """消费技能事件流，返回 (_result data, 其他事件)。"""
    result = None
    others = []
    async for event in gen:
        if event.get("event") == RESULT_EVENT:
            result = event.get("data")
        else:
            others.append(event)
    assert result is not None, "技能必须产出 _result 事件"
    return result, others


@pytest.fixture
def patched_request(monkeypatch):
    """替换 tool_call_log_service.request_node_logs。"""

    def _install(handler):
        async def fake_request_node_logs(client_id: str = "", **kwargs: Any) -> str:
            fake_request_node_logs.kwargs = {"client_id": client_id, **kwargs}  # type: ignore[attr-defined]
            return handler(client_id=client_id, **kwargs)

        monkeypatch.setattr(
            "app.skills.fetch_node_logs.tool_call_log_service.request_node_logs",
            fake_request_node_logs,
        )
        return fake_request_node_logs

    return _install


async def test_fetch_node_logs_success(patched_request) -> None:
    """成功回传：摘要含日志尾部文本与参数回显。"""
    fake = patched_request(
        lambda **_: "2026-09-25 [error    ] 任务执行失败\n2026-09-25 [info] done"
    )
    skill = FetchNodeLogsSkill()
    result, others = await _collect(
        skill.execute(
            None,
            type("Ctx", (), {"args": {"client_id": "abcd1234-full", "tail_lines": 100}})(),  # type: ignore[arg-type]
        )
    )
    assert others == []
    assert result.success
    assert "任务执行失败" in result.summary
    assert "abcd1234" in result.summary
    assert fake.kwargs["client_id"] == "abcd1234-full"  # type: ignore[attr-defined]
    assert fake.kwargs["tail_lines"] == 100  # type: ignore[attr-defined]
    assert fake.kwargs["level"] is None  # type: ignore[attr-defined]


async def test_fetch_node_logs_long_content_truncated(patched_request) -> None:
    """超长日志截尾保留最新部分。"""
    patched_request(lambda **_: "x" * 8000 + "最新错误行")
    skill = FetchNodeLogsSkill()
    result, _ = await _collect(
        skill.execute(
            None,
            type("Ctx", (), {"args": {"client_id": "node-1"}})(),  # type: ignore[arg-type]
        )
    )
    assert result.success
    assert result.data["truncated"] is True
    assert len(result.summary) < 8000
    assert "最新错误行" in result.summary


@pytest.mark.parametrize(
    ("code", "hint_fragment"),
    [
        ("offline", "节点不在线"),
        ("timeout", "旧版本"),
        ("send_failed", "稍后重试"),
        ("agent_error", "读取日志"),
    ],
)
async def test_fetch_node_logs_failure_hints(
    patched_request, code: str, hint_fragment: str
) -> None:
    """失败按 code 返回对应排查提示，技能不抛异常。"""
    patched_request(lambda **_: (_ for _ in ()).throw(NodeLogFetchError("触发错误", code)))
    skill = FetchNodeLogsSkill()
    result, _ = await _collect(
        skill.execute(
            None,
            type("Ctx", (), {"args": {"client_id": "node-1"}})(),  # type: ignore[arg-type]
        )
    )
    assert not result.success
    assert "节点日志拉取失败" in result.summary
    assert hint_fragment in result.summary


async def test_fetch_node_logs_missing_client_id(patched_request) -> None:
    """缺 client_id 时直接失败并提示获取方式，不发起拉取。"""
    fake = patched_request(lambda **_: "不应被调用")
    skill = FetchNodeLogsSkill()
    result, _ = await _collect(
        skill.execute(None, type("Ctx", (), {"args": {}})())  # type: ignore[arg-type]
    )
    assert not result.success
    assert "client_id" in result.summary
    assert not hasattr(fake, "kwargs")  # 未发起拉取
