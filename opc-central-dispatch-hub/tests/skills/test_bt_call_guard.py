"""bt_mcp_call 调用防线测试（ADR-050）：参数硬校验 / 场景软提示 / 经验闭环。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bt_mcp_server import BtMcpServer
from app.models.experience_record import ExperienceRecord
from app.models.session_attachment import SessionAttachment
from app.services.experience_service import normalize_signature
from app.skills.base import SkillContext
from app.skills.bt_call_guard import (
    maybe_run_local_command,
    record_and_match,
    scene_soft_hint,
    validate_arguments,
)
from app.skills.bt_mcp import BtMcpCallSkill


@pytest.fixture(autouse=True)
async def _clean_tables(db_session: AsyncSession):
    """每个用例前后清空 bt_mcp_servers 与 experience_records。"""
    for _phase in range(2):
        await db_session.execute(delete(ExperienceRecord))
        await db_session.execute(delete(BtMcpServer))
        await db_session.commit()
        if _phase == 0:
            yield
    await db_session.commit()


async def _seed_server(db_session: AsyncSession) -> BtMcpServer:
    """写入一台测试服务器。"""
    server = BtMcpServer(
        name="测试服务器",
        local_url="https://192.168.101.13:8765/bt-mcp-x/mcp",
        address_mode="local",
        api_token="btmcp_tok",
        enabled=True,
    )
    db_session.add(server)
    await db_session.commit()
    await db_session.refresh(server)
    return server


async def _collect(skill, db_session: AsyncSession, args: dict | None, messages=None):
    """执行技能并收集事件。"""
    context = SkillContext(args=args or {}, messages=messages or [])
    return [event async for event in skill.execute(db_session, context)]


def _result(events: list[dict]):
    """取技能结果事件。"""
    return events[-1]["data"]


_HTML_SITE_SCHEMA = {
    "type": "object",
    "properties": {"site_name": {"type": "string"}},
    "required": ["site_name"],
}


async def test_validate_unknown_param_rejected(db_session: AsyncSession) -> None:
    """UT-V1：schema 外参数名拦截——不发 MCP 请求并回显正确参数名。"""
    server = await _seed_server(db_session)
    timed_call = AsyncMock()
    with (
        patch(
            "app.skills.bt_mcp.fetch_tool_schemas",
            new=AsyncMock(return_value={"HtmlProjectInfo": _HTML_SITE_SCHEMA}),
        ),
        patch("app.skills.bt_mcp.timed_call", new=timed_call),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {
                    "server_id": str(server.id),
                    "tool": "HtmlProjectInfo",
                    "arguments": {"project_name": "192.168.101.13"},
                },
            )
        )
    assert result.success is False
    assert "未知参数" in result.summary
    assert "site_name" in result.summary
    assert "逐字一致" in result.summary
    timed_call.assert_not_awaited()


async def test_validate_missing_required_rejected(db_session: AsyncSession) -> None:
    """UT-V2：必填缺失拦截并回显必填清单。"""
    server = await _seed_server(db_session)
    with (
        patch(
            "app.skills.bt_mcp.fetch_tool_schemas",
            new=AsyncMock(return_value={"HtmlProjectInfo": _HTML_SITE_SCHEMA}),
        ),
        patch("app.skills.bt_mcp.timed_call", new=AsyncMock()),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {
                    "server_id": str(server.id),
                    "tool": "HtmlProjectInfo",
                    "arguments": {},
                },
            )
        )
    assert result.success is False
    assert "缺少必填参数" in result.summary
    assert "site_name" in result.summary


async def test_validate_confirm_exempt_and_valid_passthrough(
    db_session: AsyncSession,
) -> None:
    """UT-V3：合法参数 + confirm 豁免参数正常放行。"""
    server = await _seed_server(db_session)
    timed_call = AsyncMock(return_value=({"status": True, "msg": "ok"}, 9))
    with (
        patch(
            "app.skills.bt_mcp.fetch_tool_schemas",
            new=AsyncMock(return_value={"SiteDelete": _HTML_SITE_SCHEMA}),
        ),
        patch("app.skills.bt_mcp.timed_call", new=timed_call),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {
                    "server_id": str(server.id),
                    "tool": "SiteDelete",
                    "arguments": {"site_name": "x", "confirm": True},
                },
            )
        )
    assert result.success is True
    timed_call.assert_awaited_once()


async def test_schema_fetch_failure_degrades(db_session: AsyncSession) -> None:
    """UT-V4：schema 获取失败降级放行（与 ADR-049 现状一致）。"""
    server = await _seed_server(db_session)
    timed_call = AsyncMock(return_value=({"status": True, "msg": "ok"}, 9))
    with (
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
        patch("app.skills.bt_mcp.timed_call", new=timed_call),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {"server_id": str(server.id), "tool": "UnknownTool", "arguments": {}},
            )
        )
    assert result.success is True
    timed_call.assert_awaited_once()


async def test_scene_soft_hint_non_blocking(db_session: AsyncSession) -> None:
    """UT-V5：会话读过 HTML SOP 时调用网站族工具 → 结果附提示但不阻断。"""
    server = await _seed_server(db_session)
    sop_message = {
        "role": "tool",
        "content": "## HTML 静态项目部署 SOP\n0. 工具族严格区分……",
    }
    timed_call = AsyncMock(return_value=({"status": True, "msg": "ok"}, 9))
    with (
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
        patch("app.skills.bt_mcp.timed_call", new=timed_call),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {"server_id": str(server.id), "tool": "SiteList", "arguments": {}},
                messages=[sop_message],
            )
        )
    assert result.success is True
    assert "白名单" in result.summary


async def test_scene_soft_hint_silent_without_sop(db_session: AsyncSession) -> None:
    """UT-V5b：未读过 SOP 时无场景提示（不误报）。"""
    server = await _seed_server(db_session)
    with (
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
        patch(
            "app.skills.bt_mcp.timed_call",
            new=AsyncMock(return_value=({"status": True, "msg": "ok"}, 9)),
        ),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {"server_id": str(server.id), "tool": "SiteList", "arguments": {}},
            )
        )
    assert result.success is True
    assert "白名单" not in result.summary


async def test_failure_records_experience(db_session: AsyncSession) -> None:
    """UT-V6：status=false 自动落 pending 经验条目。"""
    server = await _seed_server(db_session)
    with (
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
        patch(
            "app.skills.bt_mcp.timed_call",
            new=AsyncMock(return_value=({"status": False, "msg": "站点不存在"}, 8)),
        ),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {
                    "server_id": str(server.id),
                    "tool": "SiteDelete",
                    "arguments": {"site_name": "x"},
                },
            )
        )
    assert result.success is False
    assert "已自动记录" in result.summary
    found = await db_session.execute(select(ExperienceRecord))
    records = list(found.scalars().all())
    assert len(records) == 1
    assert records[0].tool == "SiteDelete"
    assert records[0].status == "pending"
    assert records[0].session_id is None


async def test_resolved_experience_injected_on_failure(db_session: AsyncSession) -> None:
    """UT-V7：历史经验命中注入 + hit_count 递增。"""
    server = await _seed_server(db_session)
    error_msg = "错误: 未找到该网站 192.168.1.5"
    db_session.add(
        ExperienceRecord(
            tool="HtmlProjectInfo",
            error_signature=normalize_signature(error_msg),
            root_cause="静态站不属于网站(PHP)模块",
            solution="改用 HtmlProjectInfo(site_name=...)",
            status="resolved",
        )
    )
    await db_session.commit()
    with (
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
        patch(
            "app.skills.bt_mcp.timed_call",
            new=AsyncMock(return_value=({"status": False, "msg": error_msg}, 8)),
        ),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {
                    "server_id": str(server.id),
                    "tool": "HtmlProjectInfo",
                    "arguments": {"site_name": "192.168.101.13"},
                },
            )
        )
    assert result.success is False
    assert "历史经验" in result.summary
    assert "HtmlProjectInfo" in result.summary
    found = await db_session.execute(
        select(ExperienceRecord).where(ExperienceRecord.status == "resolved")
    )
    record = found.scalars().one()
    assert record.hit_count == 1


async def test_failure_deduplicated_by_signature(db_session: AsyncSession) -> None:
    """UT-V8：同 session 同签名重复失败不重复写。"""
    server = await _seed_server(db_session)
    with (
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
        patch(
            "app.skills.bt_mcp.timed_call",
            new=AsyncMock(return_value=({"status": False, "msg": "站点不存在"}, 8)),
        ),
    ):
        for _round in range(2):
            _result(
                await _collect(
                    BtMcpCallSkill(),
                    db_session,
                    {
                        "server_id": str(server.id),
                        "tool": "SiteDelete",
                        "arguments": {"site_name": "x"},
                    },
                )
            )
    found = await db_session.execute(select(ExperienceRecord))
    assert len(list(found.scalars().all())) == 1


async def test_record_and_match_failure_is_silent(db_session: AsyncSession) -> None:
    """UT-V4b：经验服务异常时闭环静默（返回空串不抛出）。"""
    with patch(
        "app.skills.bt_call_guard._EXPERIENCE",
        **{"record_failure.side_effect": RuntimeError("db down")},
    ):
        hint = await record_and_match(
            None,  # type: ignore[argpass] — service 已被 mock，session 不会被触达
            tool="T",
            error_msg="boom",
            args_summary=None,
            session_id=None,
        )
    assert hint == ""


def test_validate_arguments_exempts_confirm() -> None:
    """guard 纯函数：confirm 属服务端通用扩展参数，豁免校验。"""
    schema = {"properties": {"site_name": {}}, "required": ["site_name"]}
    unknown, missing = validate_arguments(schema, {"site_name": "x", "confirm": True})
    assert unknown == []
    assert missing == []


def test_scene_soft_hint_requires_sop_and_known_tool() -> None:
    """guard 纯函数：无 SOP 不提示；未知工具不提示。"""
    assert scene_soft_hint([], "SiteList") == ""
    assert scene_soft_hint([{"role": "tool", "content": "## HTML 静态项目部署 SOP"}], "Foo") == ""
    hint = scene_soft_hint(
        [{"role": "tool", "content": "## HTML 静态项目部署 SOP"}], "NodeProjectInfo"
    )
    assert "白名单" in hint


async def test_maybe_run_local_command_non_upload_passthrough() -> None:
    """UT-U1：非 Upload 工具原样返回（不代执行）。"""
    result = {"status": True}
    client = AsyncMock()
    out = await maybe_run_local_command(
        None,  # type: ignore[arg-type]
        client=client,
        tool="SystemInfo",
        result=result,
        arguments={},
        session_id=None,
    )
    assert out is result
    client.call_tool.assert_not_awaited()


async def test_maybe_run_local_command_upload_runs_and_polls(
    db_session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UT-U2：Upload 两段式——中枢代执行 curl 并轮询 ready 回传服务器路径。"""
    import json

    # 附件记录 + 本地文件
    storage_dir = tmp_path / "attachments"
    rel_path = f"{uuid.uuid4().hex}/probe.zip"
    local_file = storage_dir / rel_path
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"PK-probe")
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: MagicMock(attachment_storage_dir=str(storage_dir)),
    )
    db_session.add(
        SessionAttachment(
            session_id=uuid.uuid4(),
            file_name="probe.zip",
            mime="application/zip",
            size=8,
            kind="archive",
            storage_path=rel_path,
        )
    )
    await db_session.commit()

    first_result = {
        "status": True,
        "msg": "上传会话已创建",
        "file_id": "file_abc",
        "ready": False,
        "state": "PREPARED",
        "next_action": {
            "type": "run_local_command",
            "windows": 'curl.exe --upload-file "<LOCAL_FILE>" https://panel/mcp/upload/file_abc',
        },
    }

    class _Proc:
        returncode = 0

        async def communicate(self):
            return (
                json.dumps(
                    {
                        "status": True,
                        "ready": True,
                        "file_id": "file_abc",
                        "path": "/www/server/panel/data/mcp_uploads/probe.zip",
                    }
                ).encode(),
                None,
            )

    async def _fake_shell(*_args, **_kwargs):
        return _Proc()

    monkeypatch.setattr("app.skills.bt_call_guard.asyncio.create_subprocess_shell", _fake_shell)
    client = AsyncMock()
    client.call_tool = AsyncMock(
        return_value={
            "status": True,
            "ready": True,
            "file_id": "file_abc",
            "path": "/www/server/panel/data/mcp_uploads/probe.zip",
        }
    )
    out = await maybe_run_local_command(
        db_session,
        client=client,
        tool="Upload",
        result=first_result,
        arguments={"file_name": "probe.zip"},
        session_id=None,
    )
    assert out["ready"] is True
    assert out["path"] == "/www/server/panel/data/mcp_uploads/probe.zip"
    assert "中枢已代执行" in out["hub_note"]
    # 轮询用首次返回的 file_id（而非 file_name）
    assert client.call_tool.await_args_list[0].args[1] == {"file_id": "file_abc"}


async def test_maybe_run_local_command_missing_attachment(
    db_session: AsyncSession,
) -> None:
    """UT-U3：附件与产物均未命中时不代执行，回传 hub_note 双指引（ADR-068）。"""
    first_result = {
        "status": True,
        "file_id": "file_x",
        "ready": False,
        "next_action": {
            "type": "run_local_command",
            "windows": 'curl.exe --upload-file "<LOCAL_FILE>" https://panel/x',
        },
    }
    client = AsyncMock()
    out = await maybe_run_local_command(
        db_session,
        client=client,
        tool="Upload",
        result=first_result,
        arguments={"file_name": "不存在的文件.zip"},
        session_id=None,
    )
    assert out["ready"] is False
    assert "未找到文件" in out["hub_note"]
    assert "list_artifacts" in out["hub_note"]
    client.call_tool.assert_not_awaited()
