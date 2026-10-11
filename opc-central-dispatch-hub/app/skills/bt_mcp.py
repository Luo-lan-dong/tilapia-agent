"""宝塔 MCP 编排技能（ADR-049）：服务器查询 / 工具代理 / 自动配置 / 部署 SOP。

采用代理入口模式（非 schema 全量注入）：6 个技能覆盖 98 个 MCP 工具
的调用能力与服务器配置，token 成本恒定；权限管控在服务器端插件（用户决策）。
"""

import json
import re
import urllib.parse
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.schemas.bt_mcp import BtMcpServerCreate
from app.services.bt_mcp_client import BtMcpError, sanitize_arguments, timed_call
from app.services.bt_mcp_risk import get_tool_risk
from app.services.bt_mcp_server_service import BtMcpServerService
from app.services.sop_service import SopService
from app.services.tool_call_log_service import record as record_tool_log
from app.skills.base import Skill, SkillContext, SkillResult
from app.skills.bt_call_guard import (
    fetch_tool_schemas,
    maybe_run_local_command,
    record_and_match,
    scene_soft_hint,
    validate_arguments,
)
from app.skills.bt_deploy_prompts import get_deploy_guide_text

_SERVICE = BtMcpServerService()

#: 工具目录单条描述截断上限
_DESC_MAX = 100

#: 工具清单里每条的「一句话用途」上限（ADR-057）。
#: 取值依据：清单总上限 8000 字符，按 98 工具计算，**最坏情况**（每条都触顶）
#: 需满足 98×(名字+风险+分隔符+上限) ≤ 8000 → 上限约 55 字符。取 50 保守兜住；
#: 实测真实描述首句平均仅约 42 字符，故 50 既不浪费预算也不损失可读性。
TOOL_BRIEF_MAX_CHARS = 50
#: 工具清单摘要总上限（ADR-057）：仅作极端场景兜底（紧凑化后正常约 4k 字符）
RESULT_SUMMARY_MAX_CHARS = 8000
#: 首句切分符：中文句号/分号/换行/英文句号
_SENTENCE_ENDINGS = ("。", "；", "\n", ". ")


def _first_sentence(text: str, limit: int) -> str:
    """取描述首句并按 ``limit`` 截断（ADR-057）。

    清单条目的用途是「让模型知道有这个工具、大概干什么」，完整语义走
    ``bt_mcp_schema``；因此这里取首个句末标点之前的内容即可，避免把
    冗长的 ``Args:`` 说明塞进清单。

    Args:
        text: 原始描述（可能为空）。
        limit: 首句最大字符数；超出时截断并追加 ``…``。

    Returns:
        首句文本；``text`` 为空时返回空串。
    """
    if not text:
        return ""
    normalized = text.strip()
    if not normalized:
        return ""
    cut = len(normalized)
    for ending in _SENTENCE_ENDINGS:
        position = normalized.find(ending)
        if position != -1:
            cut = min(cut, position)
    sentence = normalized[:cut].strip() or normalized[:limit].strip()
    if len(sentence) > limit:
        return sentence[:limit].rstrip() + "…"
    return sentence


def _fail(summary: str) -> dict[str, Any]:
    """构造失败结果事件。"""
    return {"event": "_result", "data": SkillResult(success=False, summary=summary)}


class BtMcpServersSkill(Skill):
    """列出启用的宝塔 MCP 服务器（部署第一步：确认目标服务器）。"""

    name = "bt_mcp_servers"
    display_name = "查询部署服务器"
    description = (
        "列出已配置并启用的宝塔 MCP 服务器（每台对应一个目标部署环境）。"
        "用户要求部署/上线/查看服务器时首先调用；多台时必须让用户确认"
        "目标服务器后再执行后续操作；列表为空时引导用户到「系统设置 → "
        "宝塔 MCP」添加服务器连接。"
    )
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    returns = "服务器清单：[{id, name, remark, address_mode}]"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """查询启用的服务器清单。"""
        servers = await _SERVICE.list_enabled_servers(session)
        if not servers:
            yield _fail(
                "当前没有已启用的宝塔 MCP 服务器。请先到「系统设置 → 宝塔 MCP」"
                "添加服务器连接（可粘贴宝塔插件生成的接入 JSON 一键导入并测试连接），"
                "然后再发起部署。"
            )
            return
        lines = [
            f"- {server.name}（id: {server.id}，地址模式: {server.address_mode}"
            f"{'，备注: ' + server.remark if server.remark else ''}）"
            for server in servers
        ]
        summary = (
            f"共 {len(servers)} 台可用的宝塔 MCP 服务器：\n"
            + "\n".join(lines)
            + "\n部署前请让用户确认目标服务器；确认后调用 bt_deploy_guide 获取部署 SOP。"
        )
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=summary,
                data={
                    "servers": [
                        {
                            "id": str(server.id),
                            "name": server.name,
                            "remark": server.remark,
                            "address_mode": server.address_mode,
                        }
                        for server in servers
                    ]
                },
            ),
        }


class BtMcpToolsSkill(Skill):
    """按服务器列出 MCP 工具目录（名称 + 一句话用途 + 风险）。"""

    name = "bt_mcp_tools"
    display_name = "查询服务器工具目录"
    description = (
        "列出目标宝塔 MCP 服务器提供的全部工具（名称/一句话用途/风险等级）。"
        "不确定用什么工具完成任务、或需要向用户介绍服务器能力时调用。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "server_id": {"type": "string", "description": "目标服务器 ID（bt_mcp_servers 返回）"}
        },
        "required": ["server_id"],
    }
    returns = "工具目录：[{name, description, risk}]（全量清单 + 按需深读指引）"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """拉取并汇总工具目录。

        ADR-057：清单必须**全量可见**。旧实现把 98 个工具的完整描述拼成
        33,781 字符后机械头截到 8,000，导致 65 个工具名对模型完全不存在、
        且截断停在半句描述上（诱发入参猜测）。现改为「单行紧凑条目 + 显式
        恢复指令」：实测 98 个工具约 2.5k 字符，**比修复前更短且覆盖 98/98**。
        """
        server_id = str(context.args.get("server_id") or "").strip()
        if not server_id:
            yield _fail("参数不完整：必须提供 server_id（可先用 bt_mcp_servers 查询）。")
            return
        server = await _SERVICE.get_server(session, server_id)
        if server is None:
            yield _fail(f"服务器不存在：{server_id}")
            return
        try:
            tools = await _SERVICE.list_tools(session, server)
        except (
            BtMcpError,
            ValueError,
        ) as exc:
            yield _fail(f"拉取工具清单失败：{exc}")
            return
        lines = []
        for tool in tools:
            # 单行紧凑条目：描述只取首句（完整描述走 bt_mcp_schema 按需深读）
            brief = _first_sentence(tool.description or "", TOOL_BRIEF_MAX_CHARS)
            line = f"- {tool.name}（{tool.risk}）" + (f"：{brief}" if brief else "")
            if tool.name == "Upload":
                # 修复（issues 2026-09-26）：宝塔原文描述「调用方必须在本机执行
                # curl」导致模型直接放弃产物上传——中枢已代执行本机上传
                line += (
                    "【当前中枢环境说明：本机 curl 由中枢自动代执行，"
                    "直接调用传 file_name/size 即可，返回含服务器落盘路径】"
                )
            lines.append(line)
        summary = (
            f"服务器「{server.name}」共 {len(tools)} 个工具"
            "（下列为**全部**工具的一句话用途，完整说明与入参按需查询）：\n"
            + "\n".join(lines)
            + "\n【要某个工具的完整说明与入参 Schema，调用 "
            "bt_mcp_schema(server_id, tool=<工具名>) —— 推荐做法，无需重新拉清单】\n"
            "【high 风险工具（删库/删项目/改防火墙等）服务端要求先向用户确认再执行。】"
        )
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=summary[:RESULT_SUMMARY_MAX_CHARS],
                data={
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "category": tool.category,
                            "risk": tool.risk,
                        }
                        for tool in tools
                    ]
                },
            ),
        }


class BtMcpSchemaSkill(Skill):
    """查询单个工具的完整入参说明。"""

    name = "bt_mcp_schema"
    display_name = "查询工具参数"
    description = (
        "查询某个宝塔 MCP 工具的完整入参 Schema（字段/类型/必填/说明）。"
        "调用 bt_mcp_call 前不确定参数结构时使用；不要凭记忆猜测参数。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "server_id": {"type": "string", "description": "目标服务器 ID"},
            "tool": {"type": "string", "description": "工具名（bt_mcp_tools 返回的 name）"},
        },
        "required": ["server_id", "tool"],
    }
    returns = "工具入参 Schema（JSON 文本）+ 完整描述"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """返回工具 inputSchema。"""
        server_id = str(context.args.get("server_id") or "").strip()
        tool = str(context.args.get("tool") or "").strip()
        if not server_id or not tool:
            yield _fail("参数不完整：必须提供 server_id 与 tool。")
            return
        server = await _SERVICE.get_server(session, server_id)
        if server is None:
            yield _fail(f"服务器不存在：{server_id}")
            return
        client = _SERVICE.build_client(server)
        try:
            tools = await client.list_tools()
        except BtMcpError as exc:
            yield _fail(f"查询失败：{exc}")
            return
        finally:
            await client.close()
        matched = next((item for item in tools if item.get("name") == tool), None)
        if matched is None:
            names = "、".join(sorted(item.get("name", "") for item in tools)[:40])
            yield _fail(f"工具 {tool} 不存在。可用工具（前 40）：{names}")
            return
        schema_text = json.dumps(matched.get("inputSchema", {}), ensure_ascii=False)
        summary = (
            f"工具 {tool} 的入参 Schema：\n{schema_text}\n"
            f"说明：{(matched.get('description') or '')[:2000]}\n"
            f"风险等级：{get_tool_risk(tool)}。请严格按 Schema 传参调用 bt_mcp_call。"
        )
        yield {
            "event": "_result",
            "data": SkillResult(success=True, summary=summary, data={"tool": tool}),
        }


class BtMcpCallSkill(Skill):
    """统一调用入口：执行任意宝塔 MCP 工具。"""

    name = "bt_mcp_call"
    display_name = "调用宝塔面板工具"
    description = (
        "在目标宝塔 MCP 服务器上执行一个工具（部署/查询/运维统一入口）。"
        "参数：server_id（bt_mcp_servers 获取）、tool（bt_mcp_tools 获取）、"
        "arguments（对象，结构不确定先调 bt_mcp_schema）。返回 {status, msg, ...}；"
        "status=false 时如实处理不臆造成功；高危工具返回待确认状态时必须先"
        "向用户展示操作内容与影响，获得明确同意后才以相同参数+confirm=true 重调。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "server_id": {"type": "string", "description": "目标服务器 ID"},
            "tool": {"type": "string", "description": "工具名"},
            "arguments": {
                "type": "object",
                "description": "工具入参（结构不确定先调 bt_mcp_schema）",
            },
        },
        "required": ["server_id", "tool"],
    }
    returns = "宝塔统一结构：{status, msg, waiting_confirm?, data}"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """调用 MCP 工具并回传结果（含链路日志与确认指引）。"""
        server_id = str(context.args.get("server_id") or "").strip()
        tool = str(context.args.get("tool") or "").strip()
        arguments = context.args.get("arguments") or {}
        if not server_id or not tool:
            yield _fail("参数不完整：必须提供 server_id 与 tool。")
            return
        if not isinstance(arguments, dict):
            yield _fail("arguments 必须是对象（结构不确定先调 bt_mcp_schema 查询）。")
            return
        server = await _SERVICE.get_server(session, server_id)
        if server is None:
            yield _fail(f"服务器不存在：{server_id}")
            return
        # ADR-050 第二层：参数名 schema 硬校验（确定性拦截未知/缺失必填，
        # 获取失败降级放行；confirm 重调同路径校验，无副作用）
        schemas = await fetch_tool_schemas(server)
        if schemas is not None:
            schema = schemas.get(tool)
            if schema is None:
                yield _fail(
                    f"工具 {tool} 不在服务器工具清单中，先用 bt_mcp_tools 查看可用工具"
                    "（注意项目类型/网站族不要串用）。"
                )
                return
            unknown, missing = validate_arguments(schema, arguments)
            if unknown or missing:
                details: list[str] = []
                if unknown:
                    valid = "、".join(sorted(str(k) for k in (schema.get("properties") or {})))
                    details.append(
                        f"未知参数：{('、'.join(unknown))}"
                        + (f"（该工具正确参数名：{valid}）" if valid else "")
                    )
                if missing:
                    details.append(f"缺少必填参数：{('、'.join(missing))}")
                yield _fail(
                    f"参数校验未通过（本次未发起调用）：{'；'.join(details)}。"
                    "参数名必须与 bt_mcp_schema 返回的 Schema 逐字一致。"
                )
                return
        risk = get_tool_risk(tool)
        yield {
            "event": "status",
            "data": {"message": f"正在调用「{server.name}」的 {tool} 工具（风险 {risk}）…"},
        }
        client = _SERVICE.build_client(server)
        try:
            result, duration_ms = await timed_call(client, tool, arguments)
            # ADR-051 增强：Upload 两段式协议（next_action 要求客户端本机执行
            # curl）由中枢代执行——AI 无法在中枢本机跑命令，产物上传依赖此步
            result = await maybe_run_local_command(
                session,
                client=client,
                tool=tool,
                result=result,
                arguments=arguments,
                session_id=context.session_id,
            )
        except BtMcpError as exc:
            await record_tool_log(
                kind="bt_mcp_call",
                name=tool,
                status="error",
                session_id=context.session_id,
                args_summary=sanitize_arguments(arguments),
                error=str(exc),
            )
            # ADR-050 第三层：失败自动记录 + 历史经验命中注入（内部兜底）
            experience_hint = await record_and_match(
                session,
                tool=tool,
                error_msg=str(exc),
                args_summary=sanitize_arguments(arguments),
                session_id=context.session_id,
            )
            yield _fail(f"工具 {tool} 调用失败：{exc}{experience_hint}")
            return
        finally:
            await client.close()
        await record_tool_log(
            kind="bt_mcp_call",
            name=tool,
            status="success" if result.get("status") else "tool_error",
            session_id=context.session_id,
            args_summary=sanitize_arguments(arguments),
            result_summary=result,
            duration_ms=duration_ms,
        )
        # 高危两段式确认：服务端已登记确认记录但未执行
        waiting = bool(
            result.get("waiting_confirm")
            or (isinstance(result.get("data"), dict) and result["data"].get("waiting_confirm"))
        )
        if waiting:
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        f"工具 {tool} 处于待确认状态（waiting_confirm），本次未执行。"
                        "请立即停止后续步骤，向用户完整展示该操作的内容与影响范围，"
                        "询问是否继续；仅在用户明确同意后，以完全相同的业务参数加"
                        " confirm=true 重新调用本工具执行。"
                    ),
                    data={"tool": tool, "result": result},
                ),
            }
            return
        status_flag = bool(result.get("status"))
        msg = str(result.get("msg") or "")
        experience_hint = ""
        if not status_flag:
            # ADR-050 第三层：工具返回失败同样进入经验闭环
            experience_hint = await record_and_match(
                session,
                tool=tool,
                error_msg=msg or "工具返回 status=false（无 msg）",
                args_summary=sanitize_arguments(arguments),
                session_id=context.session_id,
            )
        summary = (
            f"工具 {tool} 执行{'成功' if status_flag else '失败'}"
            + (f"：{msg}" if msg else "")
            + "。"
        )
        # 数据体摘要（修复：宝塔把实际输出放 data 字段——如 Bash 的命令输出、
        # Info 工具的站点信息；此前只回传 msg，AI 永远看不到真正的数据）
        data_payload = result.get("data")
        if data_payload not in (None, {}, []):
            try:
                data_hint = json.dumps(data_payload, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                data_hint = str(data_payload)
            summary += f"\n返回数据：{data_hint[:1500]}"
        if not status_flag:
            summary += "请根据 msg 判断处置（不要臆造成功），必要时向用户说明。"
            summary += experience_hint
        # ADR-050 第一层联动：场景白名单软提示（只提示不阻断）
        soft_hint = scene_soft_hint(context.messages, tool)
        if soft_hint:
            summary += "\n" + soft_hint
        yield {
            "event": "_result",
            "data": SkillResult(
                success=status_flag,
                summary=summary,
                data={"tool": tool, "result": result},
            ),
        }


class BtDeployGuideSkill(Skill):
    """返回部署指引（约束 + 库内场景 SOP + 坑地图 + 白名单）。"""

    name = "bt_deploy_guide"
    display_name = "获取部署指引"
    description = (
        "获取宝塔面板部署指引（约束/该场景要点/实测坑清单/工具白名单）。"
        "开始部署前按项目类型调用一次（python/nodejs/go/html/php 或其他自判"
        "类型；不确定时先解压产物按特征判定，判不准问用户）；未确认类型时"
        "可不传参数获取类型概览。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "project_type": {
                "type": "string",
                "description": "项目类型/场景标识（如 python/nodejs/go/html/php 或其他，未确认时可缺省）",
            },
        },
    }
    returns = "部署指引文本（含场景要点/坑地图/白名单）"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """返回部署指引（ADR-051：场景要点从 sop_records 读库命中）。"""
        project_type = str(context.args.get("project_type") or "").strip() or None
        scene_sop: str | None = None
        if project_type:
            try:
                matched = await SopService().find_by_scene(session, project_type)
                scene_sop = matched.content if matched else None
            except Exception:  # noqa: BLE001 — SOP 是增益，读库失败降级
                scene_sop = None
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=get_deploy_guide_text(project_type, scene_sop),
                data={"project_type": project_type, "has_scene_sop": bool(scene_sop)},
            ),
        }


#: 从提示词中提取下载 URL（宝塔官方提示词形如"请先下载并读取以下文件：https://ip:port/down/xx.md"）
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
#: mcp_info JSON 代码块（```json ... ```）
_JSON_BLOCK_PATTERN = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
#: 指令文件下载超时
_SETUP_DOWNLOAD_TIMEOUT = 30.0


def _extract_url(text: str) -> str | None:
    """提取提示词中的第一个 http(s) URL。"""
    match = _URL_PATTERN.search(text or "")
    return match.group(0).rstrip(".,;，。；") if match else None


def _extract_mcp_info(md_text: str) -> dict[str, Any]:
    """从指令文件 Markdown 中解析 mcp_info JSON（无代码块时尝试全文解析）。"""
    match = _JSON_BLOCK_PATTERN.search(md_text)
    raw = match.group(1) if match else md_text.strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("mcp_info 不是 JSON 对象")
    return parsed


def _derive_server_name(info: dict[str, Any]) -> str:
    """从接入信息推导默认服务器名（host 取 local_host 优先）。"""
    url_text = info.get("local_host") or info.get("url") or info.get("public_host") or ""
    host = urllib.parse.urlparse(str(url_text)).hostname or "未知服务器"
    return f"宝塔服务器-{host}"


class BtMcpSetupSkill(Skill):
    """粘贴宝塔官方安装提示词，自动下载接入信息并添加服务器连接。"""

    name = "bt_mcp_setup"
    display_name = "自动配置宝塔服务器"
    description = (
        "用户粘贴宝塔面板「宝塔 MCP 服务」插件生成的安装提示词（含指令文件"
        "下载 URL）时调用：自动下载该文件、解析接入信息（令牌/双地址/证书链）、"
        "添加为新的服务器连接并立即测试连接。执行完成后向用户汇报结果；"
        "若测试提示 IP 白名单问题，引导用户把中枢出口 IP 加入面板白名单。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompt_or_url": {
                "type": "string",
                "description": "用户粘贴的宝塔安装提示词全文，或其中的指令文件 URL",
            },
            "name": {
                "type": "string",
                "description": "可选，服务器显示名（缺省按服务器地址自动命名）",
            },
        },
        "required": ["prompt_or_url"],
    }
    returns = "配置结果：服务器 id/名称 + 连接测试结果（工具数量）"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """下载指令文件 → 解析 → 添加 → 测试连接。"""
        prompt = str(context.args.get("prompt_or_url") or "").strip()
        custom_name = str(context.args.get("name") or "").strip()
        if not prompt:
            yield _fail("参数不完整：请提供宝塔安装提示词全文或其中的指令文件 URL。")
            return
        url = _extract_url(prompt)
        if url is None:
            yield _fail(
                "提示词中未找到指令文件 URL（应为 https://…/down/….md 形式），请检查粘贴内容。"
            )
            return

        yield {"event": "status", "data": {"message": f"正在下载接入信息文件：{url}"}}
        # 面板 21535 下载口为自签证书：仅临时下载插件生成的接入说明，跳过校验
        async with httpx.AsyncClient(verify=False, timeout=_SETUP_DOWNLOAD_TIMEOUT) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                md_text = response.text
            except httpx.HTTPError as exc:
                yield _fail(
                    f"接入信息文件下载失败：{exc}。请确认面板可访问（浏览器打开该 URL 验证），"
                    "或在插件中重新生成安装提示词后再试。"
                )
                return

        yield {"event": "status", "data": {"message": "正在解析接入信息（令牌/地址/证书链）…"}}
        try:
            info = _extract_mcp_info(md_text)
        except (ValueError, json.JSONDecodeError):
            yield _fail(
                "该 URL 内容不是有效的宝塔 MCP 接入说明（未找到 mcp_info JSON）。"
                "请确认复制的是插件【接入与体验】页生成的完整安装提示词。"
            )
            return
        token = str(info.get("api_token") or "")
        if not token:
            yield _fail("接入信息中缺少 api_token，无法配置。请在插件中重新生成安装提示词。")
            return

        base_name = custom_name or _derive_server_name(info)
        create = BtMcpServerCreate(
            name=base_name,
            mcp_info=info,
            address_mode="local" if info.get("local_host") else "public",
        )
        for suffix in ("", "-2", "-3"):
            try:
                create.name = base_name + suffix
                server = await _SERVICE.create_server(session, create)
                break
            except BusinessError as exc:
                if exc.biz_code != ErrorCode.BT_MCP_SERVER_NAME_DUPLICATE or suffix == "-3":
                    yield _fail(f"服务器添加失败：{exc.detail}")
                    return
        yield {
            "event": "status",
            "data": {"message": f"已添加「{server.name}」，正在测试连接…"},
        }
        test = await _SERVICE.test_connection(session, server)
        token_prefix = token[:8] + "***"
        if test.success:
            summary = (
                f"宝塔 MCP 服务器配置完成：{server.name}（id: {server.id}）\n"
                f"- 连接测试：{test.message}\n"
                f"- 令牌：{token_prefix}（已脱敏入库）\n"
                f"现在可以对该服务器发起部署或查询（bt_mcp_servers 查看全部）。"
            )
        else:
            summary = (
                f"服务器「{server.name}」已添加（id: {server.id}），但连接测试未通过：\n"
                f"- {test.message}\n"
                f"按提示处理后可在设置页重试测试连接；令牌 {token_prefix} 已保存。"
            )
        yield {
            "event": "_result",
            "data": SkillResult(
                success=test.success,
                summary=summary,
                data={
                    "server_id": str(server.id),
                    "name": server.name,
                    "test_success": test.success,
                    "test_message": test.message,
                    "tool_count": test.tool_count,
                },
            ),
        }
