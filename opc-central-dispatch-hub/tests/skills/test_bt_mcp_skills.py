"""宝塔 MCP 编排技能测试（ADR-049）。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bt_mcp_server import BtMcpServer
from app.services.bt_mcp_client import BtMcpError
from app.skills.base import SkillContext
from app.skills.bt_mcp import (
    BtDeployGuideSkill,
    BtMcpCallSkill,
    BtMcpSchemaSkill,
    BtMcpServersSkill,
    BtMcpSetupSkill,
    BtMcpToolsSkill,
)


@pytest.fixture(autouse=True)
async def _clean_servers(db_session: AsyncSession):
    """每个用例前后清空 bt_mcp_servers。"""
    await db_session.execute(delete(BtMcpServer))
    yield
    await db_session.execute(delete(BtMcpServer))


async def _seed_server(db_session: AsyncSession, enabled: bool = True) -> BtMcpServer:
    """写入一台测试服务器。"""
    server = BtMcpServer(
        name="测试服务器",
        local_url="https://192.168.101.13:8765/bt-mcp-x/mcp",
        address_mode="local",
        api_token="btmcp_tok",
        enabled=enabled,
    )
    db_session.add(server)
    await db_session.commit()
    await db_session.refresh(server)
    return server


async def _collect(skill, db_session: AsyncSession, args: dict | None = None):
    """执行技能并收集事件。"""
    context = SkillContext(args=args or {}, messages=[])
    return [event async for event in skill.execute(db_session, context)]


def _result(events: list[dict]):
    """取技能结果事件。"""
    return events[-1]["data"]


async def test_servers_empty_guides_to_settings(db_session: AsyncSession) -> None:
    """UT-S1：无服务器时引导去设置页。"""
    events = await _collect(BtMcpServersSkill(), db_session)
    result = _result(events)
    assert result.success is False
    assert "宝塔 MCP" in result.summary


async def test_servers_lists_enabled_only(db_session: AsyncSession) -> None:
    """UT-S2：仅列 enabled 服务器。"""
    await _seed_server(db_session, enabled=True)
    disabled = BtMcpServer(
        name="停用服务器",
        local_url="https://10.0.0.9:8765/x/mcp",
        address_mode="local",
        api_token="t",
        enabled=False,
    )
    db_session.add(disabled)
    await db_session.commit()
    result = _result(await _collect(BtMcpServersSkill(), db_session))
    assert result.success is True
    names = [item["name"] for item in result.data["servers"]]
    assert names == ["测试服务器"]


async def test_tools_lists_with_risk(db_session: AsyncSession) -> None:
    """UT-S3：工具目录含名称与风险；描述截断。"""
    server = await _seed_server(db_session)
    long_desc = "很长的描述" * 100
    with patch(
        "app.services.bt_mcp_server_service.BtMcpServerService.list_tools",
        new=AsyncMock(
            return_value=[
                type(
                    "T",
                    (),
                    {
                        "name": "SiteDelete",
                        "description": long_desc,
                        "category": "网站",
                        "risk": "high",
                    },
                )(),
                type(
                    "T",
                    (),
                    {
                        "name": "SystemInfo",
                        "description": "系统信息",
                        "category": "系统",
                        "risk": "low",
                    },
                )(),
            ]
        ),
    ):
        result = _result(
            await _collect(BtMcpToolsSkill(), db_session, {"server_id": str(server.id)})
        )
    assert result.success is True
    assert "SiteDelete" in result.summary
    assert "high" in result.summary


async def test_schema_returns_input_schema(db_session: AsyncSession) -> None:
    """UT-S4：schema 技能返回工具入参说明。"""
    server = await _seed_server(db_session)
    schema = {"type": "object", "properties": {"site_name": {"type": "string"}}}
    mock_client = AsyncMock()
    mock_client.list_tools = AsyncMock(
        return_value=[{"name": "SiteDelete", "description": "删除", "inputSchema": schema}]
    )
    mock_client.close = AsyncMock()
    with patch(
        "app.services.bt_mcp_server_service.BtMcpServerService.build_client",
        return_value=mock_client,
    ):
        result = _result(
            await _collect(
                BtMcpSchemaSkill(), db_session, {"server_id": str(server.id), "tool": "SiteDelete"}
            )
        )
    assert result.success is True
    assert "site_name" in result.summary
    assert "high" in result.summary


async def test_call_success(db_session: AsyncSession) -> None:
    """UT-S5：只读调用成功回传结果。"""
    server = await _seed_server(db_session)
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(
        return_value={"status": True, "msg": "ok", "data": {"cpu": 8}}
    )
    mock_client.close = AsyncMock()
    with (
        patch(
            "app.services.bt_mcp_server_service.BtMcpServerService.build_client",
            return_value=mock_client,
        ),
        patch(
            "app.skills.bt_mcp.timed_call",
            new=AsyncMock(return_value=({"status": True, "msg": "ok", "data": {"cpu": 8}}, 12)),
        ),
        # ADR-050 防线关闭：schema 获取降级放行、经验闭环不写库
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {"server_id": str(server.id), "tool": "SystemInfo", "arguments": {}},
            )
        )
    assert result.success is True
    assert "成功" in result.summary


async def test_call_waiting_confirm_requires_user(db_session: AsyncSession) -> None:
    """UT-S6：waiting_confirm → 停止并要求用户确认。"""
    server = await _seed_server(db_session)
    with (
        patch(
            "app.skills.bt_mcp.timed_call",
            new=AsyncMock(
                return_value=({"status": True, "msg": "等待确认", "waiting_confirm": True}, 5)
            ),
        ),
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
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
    assert "confirm=true" in result.summary
    assert "用户" in result.summary


async def test_call_tool_error_passthrough(db_session: AsyncSession) -> None:
    """UT-S7：status=false 如实回传。"""
    server = await _seed_server(db_session)
    with (
        patch(
            "app.skills.bt_mcp.timed_call",
            new=AsyncMock(return_value=({"status": False, "msg": "站点不存在"}, 8)),
        ),
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
        patch("app.skills.bt_mcp.record_and_match", new=AsyncMock(return_value="")),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {"server_id": str(server.id), "tool": "SiteDelete", "arguments": {}},
            )
        )
    assert result.success is False
    assert "站点不存在" in result.summary


async def test_call_connection_error_translated(db_session: AsyncSession) -> None:
    """UT-S8：连接失败错误翻译回传。"""
    server = await _seed_server(db_session)
    with (
        patch(
            "app.skills.bt_mcp.timed_call",
            new=AsyncMock(side_effect=BtMcpError("白名单未放行", reason="whitelist")),
        ),
        patch("app.skills.bt_mcp.fetch_tool_schemas", new=AsyncMock(return_value=None)),
        patch("app.skills.bt_mcp.record_and_match", new=AsyncMock(return_value="")),
    ):
        result = _result(
            await _collect(
                BtMcpCallSkill(),
                db_session,
                {"server_id": str(server.id), "tool": "SystemInfo", "arguments": {}},
            )
        )
    assert result.success is False
    assert "白名单" in result.summary


async def test_deploy_guide_by_type(db_session: AsyncSession) -> None:
    """UT-S9：五类型返回对应场景要点（seed 命中）；缺省返回类型概览。"""
    for project_type, marker in (
        ("python", "Python 项目部署（场景要点）"),
        ("nodejs", "NodeJS 项目部署（场景要点）"),
        ("go", "Go 项目部署（场景要点）"),
        ("html", "HTML 静态项目部署（场景要点）"),
        ("php", "PHP 网站部署（场景要点）"),
    ):
        result = _result(
            await _collect(BtDeployGuideSkill(), db_session, {"project_type": project_type})
        )
        assert result.success is True
        assert marker in result.summary
        assert "实测坑清单" in result.summary
        assert "部署约束" in result.summary
    overview = _result(await _collect(BtDeployGuideSkill(), db_session, {}))
    assert "未指定项目类型" in overview.summary


async def test_deploy_guide_html_tool_family_guard(db_session: AsyncSession) -> None:
    """UT-S9b：指引含工具族/参数名/端口三类坑（issues 2026-09-26 问题二）。"""
    result = _result(await _collect(BtDeployGuideSkill(), db_session, {"project_type": "html"}))
    assert result.success is True
    assert "工具族互斥" in result.summary
    assert "HtmlProject" in result.summary
    assert "site_name" in result.summary
    assert "端口范围不合法" in result.summary
    # 参数一致性强约束对所有类型生效
    for project_type in ("python", "nodejs", "go", "html", "php"):
        typed = _result(
            await _collect(BtDeployGuideSkill(), db_session, {"project_type": project_type})
        )
        assert "逐字一致" in typed.summary


async def test_call_missing_args_guard(db_session: AsyncSession) -> None:
    """UT-S10：缺参返回参数不完整而非异常。"""
    result = _result(await _collect(BtMcpCallSkill(), db_session, {"tool": "SystemInfo"}))
    assert result.success is False
    assert "server_id" in result.summary


async def test_schema_missing_tool_lists_available(db_session: AsyncSession) -> None:
    """UT-S4b：工具不存在时列出可用工具名。"""
    server = await _seed_server(db_session)
    mock_client = AsyncMock()
    mock_client.list_tools = AsyncMock(
        return_value=[{"name": "SystemInfo", "description": "x", "inputSchema": json.dumps({})}]
    )
    mock_client.close = AsyncMock()
    with patch(
        "app.services.bt_mcp_server_service.BtMcpServerService.build_client",
        return_value=mock_client,
    ):
        result = _result(
            await _collect(
                BtMcpSchemaSkill(), db_session, {"server_id": str(server.id), "tool": "Nope"}
            )
        )
    assert result.success is False
    assert "SystemInfo" in result.summary


# ---------------------------------------------------------------------------
# bt_mcp_setup：粘贴官方提示词自动配置（UT-SET1~6）
# ---------------------------------------------------------------------------

_SETUP_MD = (
    "# AI Agent 自配置 MCP 服务 · 指令\n\n```json\n"
    + json.dumps(
        {
            "api_token": "btmcp_setup_token_1234567890",
            "local_host": "https://192.168.101.13:8765/bt-mcp-ABC/mcp",
            "public_host": "https://112.66.55.227:8765/bt-mcp-ABC/mcp",
            "tls_required": True,
            "tls_cert": "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----",
        }
    )
    + "\n```"
)
_SETUP_PROMPT = f"请先下载并读取以下文件：\nhttps://192.168.101.13:21535/down/abc.md\n{_SETUP_MD}"


def _mock_http_get(text: str, status: int = 200):
    """patch 技能内 httpx.AsyncClient，使 get 返回指定内容。"""
    mock_response = MagicMock()
    mock_response.status_code = status
    mock_response.text = text
    if status < 400:
        mock_response.raise_for_status = MagicMock()
    else:
        import httpx as _httpx

        mock_response.raise_for_status.side_effect = _httpx.HTTPStatusError(
            "err", request=MagicMock(), response=MagicMock()
        )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return patch("app.skills.bt_mcp.httpx.AsyncClient", return_value=mock_client)


async def test_setup_success(db_session: AsyncSession) -> None:
    """UT-SET1：下载→解析→添加→测试成功，token 脱敏。"""
    with (
        _mock_http_get(_SETUP_MD),
        patch(
            "app.services.bt_mcp_server_service.BtMcpServerService.test_connection",
            new=AsyncMock(
                return_value=MagicMock(
                    success=True, message="连接成功", tool_count=98, tool_summary={"系统": 1}
                )
            ),
        ),
    ):
        events = await _collect(BtMcpSetupSkill(), db_session, {"prompt_or_url": _SETUP_PROMPT})
    result = _result(events)
    assert result.success is True
    assert "配置完成" in result.summary
    assert "btmcp_setup_token" not in result.summary  # 全 token 不出现在结果
    assert "btmcp_se***" in result.summary
    assert result.data["tool_count"] == 98
    assert result.data["name"] == "宝塔服务器-192.168.101.13"
    # 双地址与证书已入库
    from sqlalchemy import select

    rows = (await db_session.execute(select(BtMcpServer))).scalars().all()
    assert len(rows) == 1
    assert rows[0].public_url == "https://112.66.55.227:8765/bt-mcp-ABC/mcp"
    assert rows[0].ca_cert is not None


async def test_setup_download_failure(db_session: AsyncSession) -> None:
    """UT-SET2：下载失败给出指引。"""
    with _mock_http_get("x", status=404):
        result = _result(
            await _collect(BtMcpSetupSkill(), db_session, {"prompt_or_url": _SETUP_PROMPT})
        )
    assert result.success is False
    assert "下载失败" in result.summary


async def test_setup_invalid_content(db_session: AsyncSession) -> None:
    """UT-SET3：内容无 mcp_info JSON 时拒绝。"""
    with _mock_http_get("# 普通文档，没有 json"):
        result = _result(
            await _collect(BtMcpSetupSkill(), db_session, {"prompt_or_url": _SETUP_PROMPT})
        )
    assert result.success is False
    assert "不是有效的宝塔 MCP 接入说明" in result.summary


async def test_setup_missing_token(db_session: AsyncSession) -> None:
    """UT-SET4：缺 api_token 拒绝。"""
    md = "```json\n" + json.dumps({"local_host": "https://x/mcp"}) + "\n```"
    with _mock_http_get(md):
        result = _result(
            await _collect(BtMcpSetupSkill(), db_session, {"prompt_or_url": _SETUP_PROMPT})
        )
    assert result.success is False
    assert "api_token" in result.summary


async def test_setup_duplicate_name_suffix(db_session: AsyncSession) -> None:
    """UT-SET5：重名自动加 -2 后缀。"""
    existing = BtMcpServer(
        name="宝塔服务器-192.168.101.13",
        local_url="https://192.168.101.13:8765/bt-mcp-OLD/mcp",
        address_mode="local",
        api_token="t",
        enabled=True,
    )
    db_session.add(existing)
    await db_session.commit()
    with (
        _mock_http_get(_SETUP_MD),
        patch(
            "app.services.bt_mcp_server_service.BtMcpServerService.test_connection",
            new=AsyncMock(
                return_value=MagicMock(success=True, message="ok", tool_count=98, tool_summary={})
            ),
        ),
    ):
        result = _result(
            await _collect(BtMcpSetupSkill(), db_session, {"prompt_or_url": _SETUP_PROMPT})
        )
    assert result.success is True
    assert result.data["name"] == "宝塔服务器-192.168.101.13-2"


async def test_setup_test_failed_but_saved(db_session: AsyncSession) -> None:
    """UT-SET6：连接测试失败（白名单）时服务器仍保存，结果含指引。"""
    with (
        _mock_http_get(_SETUP_MD),
        patch(
            "app.services.bt_mcp_server_service.BtMcpServerService.test_connection",
            new=AsyncMock(
                return_value=MagicMock(
                    success=False, message="IP 白名单未放行", tool_count=0, tool_summary=None
                )
            ),
        ),
    ):
        result = _result(
            await _collect(BtMcpSetupSkill(), db_session, {"prompt_or_url": _SETUP_PROMPT})
        )
    assert result.success is False
    assert "已添加" in result.summary
    assert "白名单" in result.summary


async def test_setup_no_url(db_session: AsyncSession) -> None:
    """UT-SET7：提示词中无 URL 时提示检查粘贴内容。"""
    result = _result(
        await _collect(BtMcpSetupSkill(), db_session, {"prompt_or_url": "你好，帮我配一下"})
    )
    assert result.success is False
    assert "未找到指令文件 URL" in result.summary


# ---------------------------------------------------------------------------
# ADR-057：工具清单结构化（紧凑全量 + 显式恢复指令）
# ---------------------------------------------------------------------------


def _fake_tool(name: str, description: str | None, risk: str = "low"):
    """构造 list_tools 返回的工具对象。"""
    return type(
        "T",
        (),
        {"name": name, "description": description, "category": "测试", "risk": risk},
    )()


def _many_tools(count: int = 98) -> list:
    """构造 N 个带长描述的假工具（模拟真实宝塔 98 工具场景）。"""
    tools = []
    for index in range(count):
        # 每条描述都含「首句 + Args 长文」，复刻真实清单的体积来源
        desc = f"工具{index}的用途说明；\n\nArgs:\n    arg_a: " + "参数说明" * 40
        tools.append(_fake_tool(f"Tool{index:02d}", desc, risk="medium"))
    return tools


async def _tools_summary(db_session: AsyncSession, tools: list, server=None) -> str:
    """调用 BtMcpToolsSkill 并返回摘要。"""
    server = server or await _seed_server(db_session)
    with patch(
        "app.services.bt_mcp_server_service.BtMcpServerService.list_tools",
        new=AsyncMock(return_value=tools),
    ):
        result = _result(
            await _collect(BtMcpToolsSkill(), db_session, {"server_id": str(server.id)})
        )
    assert result.success is True
    return result.summary


async def test_tools_summary_covers_all_tool_names(db_session: AsyncSession) -> None:
    """UT-66（ADR-057 守卫）：98 个工具的名字**全部**出现在清单里。

    回归对象：修复前 `summary[:8000]` 只保留约 60 行，65 个工具名对模型
    完全不存在（含 SystemInfo / BashStatus / ContainerList）。
    """
    tools = _many_tools(98)
    summary = await _tools_summary(db_session, tools)

    missing = [tool.name for tool in tools if tool.name not in summary]
    assert missing == [], f"以下工具名未出现在清单中：{missing[:10]}（共 {len(missing)} 个）"
    assert "共 98 个工具" in summary


def test_tool_brief_cap_guarantees_98_tools_fit() -> None:
    """UT-74（ADR-057 守卫）：**最坏情况**下 98 工具清单也不触发兜底截断。

    算术保障：每条最坏长度 = `- ` + 名字(20) + `（` + 风险(6) + `）：` + 首句上限。
    若该乘积超过 `RESULT_SUMMARY_MAX_CHARS`，则「全量覆盖」在极端描述下会失效
    （又回到截断丢工具名的老问题）——故必须由上限取值本身保证。
    """
    from app.skills.bt_mcp import RESULT_SUMMARY_MAX_CHARS, TOOL_BRIEF_MAX_CHARS

    worst_per_tool = 2 + 20 + 1 + 6 + 2 + TOOL_BRIEF_MAX_CHARS
    assert worst_per_tool * 98 < RESULT_SUMMARY_MAX_CHARS, (
        f"最坏情况 {worst_per_tool} × 98 = {worst_per_tool * 98} 超过兜底上限 "
        f"{RESULT_SUMMARY_MAX_CHARS}，需调小 TOOL_BRIEF_MAX_CHARS"
    )


async def test_tools_summary_worst_case_desc_still_covers_all(
    db_session: AsyncSession,
) -> None:
    """UT-74b：所有工具的描述都触顶时，仍然全量覆盖且不触发兜底。"""
    from app.skills.bt_mcp import RESULT_SUMMARY_MAX_CHARS

    # 每条描述都是无标点长文本 → 必然被截到 TOOL_BRIEF_MAX_CHARS
    tools = [
        _fake_tool(f"ToolName{index:02d}ABCDEFGH", "无标点超长描述" * 20) for index in range(98)
    ]
    summary = await _tools_summary(db_session, tools)

    missing = [tool.name for tool in tools if tool.name not in summary]
    assert missing == [], f"最坏情况下仍有 {len(missing)} 个工具名消失"
    assert len(summary) < RESULT_SUMMARY_MAX_CHARS


async def test_tools_summary_stays_under_limit(db_session: AsyncSession) -> None:
    """UT-70（ADR-057 守卫）：全量覆盖的同时，长度**不劣化**（小于修复前 8000）。"""
    from app.skills.bt_mcp import RESULT_SUMMARY_MAX_CHARS

    summary = await _tools_summary(db_session, _many_tools(98))

    assert len(summary) < RESULT_SUMMARY_MAX_CHARS, (
        f"紧凑化后仍可达 {len(summary)} 字符，未达成「更短且更全」"
    )
    # 修复前的机械头截长度对照：完整描述拼接后必然超限被截到 8000
    full_len = sum(len(f"- {t.name}（{t.risk}）：{t.description}") for t in _many_tools(98))
    assert full_len > RESULT_SUMMARY_MAX_CHARS


async def test_tools_summary_first_sentence_only(db_session: AsyncSession) -> None:
    """UT-67：描述只取首句（按「。；\\n」切分），Args 长文不进清单。"""
    tools = [
        _fake_tool("Read", "读取文本文件；查看文件或读取 Grep 命中位置附近内容时使用。只读。"),
        _fake_tool("ImagePull", "拉取镜像。中风险。"),
    ]
    summary = await _tools_summary(db_session, tools)

    assert "读取文本文件" in summary
    assert "Grep 命中位置" not in summary  # 首句之后的内容被丢弃
    assert "拉取镜像" in summary
    assert "中风险" not in summary


async def test_tools_summary_clips_unpunctuated_description(db_session: AsyncSession) -> None:
    """UT-67b：无句末标点的描述按 TOOL_BRIEF_MAX_CHARS 截断并加省略号。"""
    from app.skills.bt_mcp import TOOL_BRIEF_MAX_CHARS

    tools = [_fake_tool("NoPunct", "没有标点的超长描述" * 60)]
    summary = await _tools_summary(db_session, tools)

    line = next(line for line in summary.split("\n") if line.startswith("- NoPunct"))
    assert line.endswith("…")
    assert len(line) <= len("- NoPunct（low）：") + TOOL_BRIEF_MAX_CHARS + 1


async def test_tools_summary_handles_empty_description(db_session: AsyncSession) -> None:
    """UT-68：description 为 None / 空串时不抛错，只输出名字与风险。"""
    tools = [_fake_tool("NoDesc", None), _fake_tool("BlankDesc", "")]
    summary = await _tools_summary(db_session, tools)

    assert "- NoDesc（low）" in summary
    assert "- BlankDesc（low）" in summary


async def test_tools_summary_contains_recovery_instruction(db_session: AsyncSession) -> None:
    """UT-69（ADR-057 核心）：清单必须给出「怎么取完整说明」的显式指令。"""
    summary = await _tools_summary(db_session, _many_tools(3))

    assert "bt_mcp_schema" in summary
    assert "server_id" in summary and "tool=" in summary
    assert "high 风险" in summary and "确认" in summary


async def test_tools_summary_extreme_fallback(db_session: AsyncSession) -> None:
    """UT-71：极端超长输入仍受总上限兜底保护。"""
    from app.skills.bt_mcp import RESULT_SUMMARY_MAX_CHARS

    tools = [_fake_tool("Huge", "无标点超长描述" * 5000)]
    summary = await _tools_summary(db_session, tools)

    assert len(summary) <= RESULT_SUMMARY_MAX_CHARS


async def test_tools_summary_keeps_upload_exception(db_session: AsyncSession) -> None:
    """UT-72（回归 ADR-049/issues 2026-09-26）：Upload 的空间例外说明仍在。"""
    tools = [_fake_tool("Upload", "上传文件到服务器。需要本机执行 curl。")]
    summary = await _tools_summary(db_session, tools)

    assert "中枢自动代执行" in summary


async def test_tools_summary_keeps_full_data_payload(db_session: AsyncSession) -> None:
    """UT-73：SkillResult.data.tools 仍返回完整描述（清单压缩不影响结构化数据）。"""
    server = await _seed_server(db_session)
    tools = _many_tools(3)
    with patch(
        "app.services.bt_mcp_server_service.BtMcpServerService.list_tools",
        new=AsyncMock(return_value=tools),
    ):
        result = _result(
            await _collect(BtMcpToolsSkill(), db_session, {"server_id": str(server.id)})
        )
    payload = result.data["tools"]
    assert len(payload) == 3
    assert payload[0]["description"] == tools[0].description  # 完整描述未被裁剪
    assert payload[0]["risk"] == "medium"


async def test_tools_then_schema_recovery_closed_loop(db_session: AsyncSession) -> None:
    """IT-13：清单 → 对清单**末尾**的工具走 bt_mcp_schema 可无损取回完整说明。"""
    from app.skills.bt_mcp import BtMcpSchemaSkill

    server = await _seed_server(db_session)
    tools = _many_tools(30)
    target = tools[-1]  # 修复前这个工具名会被截掉
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}

    with patch(
        "app.services.bt_mcp_server_service.BtMcpServerService.list_tools",
        new=AsyncMock(return_value=tools),
    ):
        summary = _result(
            await _collect(BtMcpToolsSkill(), db_session, {"server_id": str(server.id)})
        ).summary
    assert target.name in summary

    mock_client = MagicMock()
    mock_client.list_tools = AsyncMock(
        return_value=[
            {
                "name": target.name,
                "description": target.description,
                "inputSchema": schema,
            }
        ]
    )
    mock_client.close = AsyncMock()
    with patch(
        "app.services.bt_mcp_server_service.BtMcpServerService.build_client",
        return_value=mock_client,
    ):
        schema_result = _result(
            await _collect(
                BtMcpSchemaSkill(),
                db_session,
                {"server_id": str(server.id), "tool": target.name},
            )
        )
    assert schema_result.success is True
    assert "path" in schema_result.summary  # 入参 Schema 已取回
    assert "Args" in schema_result.summary  # 完整描述（含被清单省略的部分）已取回


async def test_tools_list_failure_unchanged(db_session: AsyncSession) -> None:
    """IT-14：list_tools 失败路径与修复前一致（不回归）。"""
    server = await _seed_server(db_session)
    with patch(
        "app.services.bt_mcp_server_service.BtMcpServerService.list_tools",
        new=AsyncMock(side_effect=BtMcpError("拉取失败", reason="network")),
    ):
        result = _result(
            await _collect(BtMcpToolsSkill(), db_session, {"server_id": str(server.id)})
        )
    assert result.success is False
    assert "拉取工具清单失败" in result.summary
