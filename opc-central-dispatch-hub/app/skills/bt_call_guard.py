"""bt_mcp_call 调用防线（ADR-050）：事中参数硬校验 + 场景软提示 + 经验记录/注入。

与 BtMcpCallSkill 分离：本模块只提供无状态的防线函数，技能 execute
保持简洁。所有经验相关操作内部兜底——经验是增益不是依赖，任何异常
只 WARNING 日志、不阻断工具调用主链路。
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bt_mcp_server import BtMcpServer
from app.models.session_attachment import SessionAttachment
from app.models.task_building_session import TaskBuildingSession
from app.services.artifact_service import artifact_service
from app.services.bt_mcp_client import BtMcpClient
from app.services.bt_mcp_server_service import BtMcpServerService
from app.services.experience_service import ExperienceService, normalize_signature
from app.skills.bt_deploy_prompts import (
    PHP_SITE_MGMT_TOOLS,
    SCENE_TOOL_WHITELISTS,
    detect_scene_from_messages,
)

logger = logging.getLogger(__name__)

_EXPERIENCE = ExperienceService()
_SERVER_SERVICE = BtMcpServerService()

#: 两段式确认等宝塔服务端通用扩展参数（不在各工具 inputSchema 声明，豁免校验）
_SCHEMA_EXEMPT_ARGS = {"confirm"}


async def fetch_tool_schemas(server: BtMcpServer) -> dict[str, dict[str, Any]] | None:
    """拉取服务器全部工具 inputSchema（{tool_name: schema}）。

    返回 None 表示获取失败（调用方跳过硬校验直接放行，与 ADR-049
    现状一致）；一次 tools/list 覆盖全部工具，供本次调用校验。
    """
    client = _SERVER_SERVICE.build_client(server)
    try:
        tools = await client.list_tools()
    except Exception as exc:  # noqa: BLE001 —— 校验属增益，失败必须降级放行
        logger.warning(
            "bt_call_guard_schema_fetch_failed",
            extra={"event": "bt_call_guard_schema_fetch_failed", "reason": str(exc)[:200]},
        )
        return None
    finally:
        await client.close()
    return {
        item.get("name", ""): (item.get("inputSchema") or {}) for item in tools if item.get("name")
    }


def validate_arguments(
    schema: dict[str, Any],
    arguments: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """参数名硬校验：返回 (未知参数名, 缺失必填名)。纯代码判定。"""
    properties = schema.get("properties") or {}
    valid_names = set(properties) | _SCHEMA_EXEMPT_ARGS
    unknown = sorted(str(key) for key in arguments if str(key) not in valid_names)
    required = schema.get("required") or []
    missing = sorted(str(name) for name in required if str(name) not in arguments)
    return unknown, missing


def scene_soft_hint(messages: list[dict[str, Any]] | None, tool: str) -> str:
    """场景白名单软提示：会话已读某类 SOP 且工具出该场景白名单时提示。

    提示范围 = 各场景白名单并集 ∪ PHP 网站管理族（"有场景归属"的族工具）：
    读部署 SOP 后误用其他 Project 族或网站族工具时提示；基础设施工具
    （SystemInfo/Bash 等，无场景归属）与未读过 SOP 时不提示。
    只提示不阻断（SOP 未覆盖的场景不能被白名单卡死）。
    """
    scene = detect_scene_from_messages(messages or [])
    if scene is None:
        return ""
    known_family_tools: set[str] = set(PHP_SITE_MGMT_TOOLS)
    for tools in SCENE_TOOL_WHITELISTS.values():
        known_family_tools |= tools
    if tool not in known_family_tools:
        return ""
    if tool in SCENE_TOOL_WHITELISTS[scene]:
        return ""
    return (
        f"提示：工具 {tool} 不在当前部署场景（{scene}）的工具白名单内，"
        "注意是否串用了其他项目类型/网站族的工具（查不到本类项目），"
        "请确认工具族后再继续。"
    )


async def maybe_run_local_command(
    session: AsyncSession,
    *,
    client: BtMcpClient,
    tool: str,
    result: Any,
    arguments: dict[str, Any],
    session_id: str | None,
) -> Any:
    """宝塔工具返回 ``run_local_command`` 时由中枢代执行并推进轮询。

    背景（issues 2026-09-26）：宝塔 Upload 工具是「客户端本机文件 → 服务器」
    两段式协议——首次调用返回 next_action（curl 命令，``<LOCAL_FILE>`` 占位），
    要求 MCP 客户端（=中枢）在本机执行；AI 无法在中枢本机执行命令，导致
    产物上传链路断裂。此处自动代执行：
    1. 按 ``arguments.file_name`` 在会话附件库解析本地文件路径；
    2. 服务端签发的命令原文只替换 ``<LOCAL_FILE>`` 占位（不拼接其他内容）；
    3. 本机执行后以 file_id 轮询 Upload 至 ready，把服务器绝对路径回传 AI。

    安全边界：仅 Upload 工具；命令取自服务端 next_action 原文；本地路径
    仅允许命中附件库记录。任何失败不抛出——原样返回 result 并附加
    ``hub_note`` 说明（AI 可见后自行处理）。
    """
    if tool != "Upload" or not isinstance(result, dict):
        return result
    data: dict[str, Any] = result.get("data") if isinstance(result.get("data"), dict) else result
    next_action = data.get("next_action")
    if not isinstance(next_action, dict) or next_action.get("type") != "run_local_command":
        return result
    command = str(next_action.get("windows") or next_action.get("posix") or "")
    if "<LOCAL_FILE>" not in command:
        return result

    file_name = str(arguments.get("file_name") or "").strip()
    local_path = await _resolve_attachment_path(session, file_name, session_id)
    if local_path is None:
        # ADR-068：附件库未命中时按会话关联任务的产物缓存解析（export.zip/
        # 文件树内文件）——产物上传不再依赖用户手动传附件
        local_path = await _resolve_session_artifact_path(session, file_name, session_id)
    if local_path is None:
        result["hub_note"] = (
            f"中枢本地未找到文件「{file_name or '(空)'}」——无法代执行上传。"
            "若为任务产物，先用 list_artifacts 核对产物文件名（如 export.zip）；"
            "若为用户上传的附件，请核对附件名（大小写/后缀）后重调。"
        )
        return result
    command = command.replace("<LOCAL_FILE>", str(local_path))
    logger.info(
        "bt_upload_local_command_started",
        extra={"event": "bt_upload_local_command_started", "file_name": file_name},
    )
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except Exception as exc:  # noqa: BLE001 —— 代执行失败不阻断，回传说明
        result["hub_note"] = f"中枢代执行上传命令异常：{str(exc)[:200]}"
        return result
    if proc.returncode != 0:
        result["hub_note"] = (
            f"中枢代执行上传命令失败（退出码 {proc.returncode}）："
            f"{stdout.decode(errors='replace')[:300]}"
        )
        return result

    # 执行成功：以 file_id 轮询直至 ready（curl 返回体通常已 ready，这里兜底确认）
    file_id = str(data.get("file_id") or "")
    final = result
    for _ in range(3):
        poll = await client.call_tool("Upload", {"file_id": file_id})
        if isinstance(poll, dict) and poll.get("ready"):
            final = poll
            break
    final["hub_note"] = (
        "中枢已代执行本机上传（Upload 两段式）。文件在服务器临时目录"
        "（root:root/0600），下一步用 Bash: mv 到站点目录并按需解压部署。"
    )
    return final


async def _resolve_attachment_path(
    session: AsyncSession, file_name: str, session_id: str | None
) -> Path | None:
    """按 file_name 解析附件本地路径：优先本会话，其次全局最新。"""
    if not file_name:
        return None
    if session_id:
        scoped = await session.execute(
            select(SessionAttachment)
            .where(
                SessionAttachment.file_name == file_name,
                SessionAttachment.session_id == session_id,
            )
            .order_by(SessionAttachment.created_at.desc())
            .limit(1)
        )
        record = scoped.scalar_one_or_none()
        if record is not None:
            return _attachment_absolute_path(record)
    global_result = await session.execute(
        select(SessionAttachment)
        .where(SessionAttachment.file_name == file_name)
        .order_by(SessionAttachment.created_at.desc())
        .limit(1)
    )
    record = global_result.scalar_one_or_none()
    if record is not None:
        return _attachment_absolute_path(record)
    return None


def _attachment_absolute_path(record: SessionAttachment) -> Path | None:
    """附件记录 → 本地绝对路径（缺失文件返回 None）。"""
    from app.core.config import get_settings

    path = Path(get_settings().attachment_storage_dir) / record.storage_path
    return path if path.is_file() else None


async def _resolve_session_artifact_path(
    session: AsyncSession, file_name: str, session_id: str | None
) -> Path | None:
    """按会话关联任务解析产物缓存路径（ADR-068 Upload 文件源扩展）。

    ``file_name`` 支持产物相对路径（``export.zip``、``dist/app.tar.gz``），
    经 ``artifact_service.get_cached_file_path`` 统一清洗防越界。
    """
    if not file_name or not session_id:
        return None
    try:
        chat_session = await session.get(TaskBuildingSession, uuid.UUID(session_id))
    except ValueError:
        return None
    if chat_session is None or chat_session.task_id is None:
        return None
    return artifact_service.get_cached_file_path(str(chat_session.task_id), file_name)


async def record_and_match(
    session: AsyncSession,
    *,
    tool: str,
    error_msg: str,
    args_summary: str | None,
    session_id: str | None,
    messages: list[dict[str, Any]] | None = None,
) -> str:
    """失败经验闭环：自动记录 pending 条目 + 检索历史经验注入文本。

    返回附加到失败 summary 的经验文本（无经验/异常时为空串）。
    """
    hint_parts: list[str] = []
    try:
        record = await _EXPERIENCE.record_failure(
            session,
            tool=tool,
            error_msg=error_msg,
            args_summary=args_summary,
            session_id=session_id,
        )
        if record is not None:
            hint_parts.append("已自动记录本次踩坑（待总结）。")
    except Exception as exc:  # noqa: BLE001 —— 经验是增益不是依赖
        logger.warning(
            "experience_record_failed",
            extra={"event": "experience_record_failed", "reason": str(exc)[:200]},
        )
    try:
        matches = await _EXPERIENCE.find_matches(session, signature=normalize_signature(error_msg))
        resolved = [item for item in matches if item.status == "resolved" and item.solution]
        if resolved:
            best = resolved[0]
            await _EXPERIENCE.record_hit(session, best)
            hint_parts.append(
                f"历史经验（同类问题已解决 {best.hit_count + 1} 次）：根因——"
                f"{(best.root_cause or '')[:300]}；解法——{(best.solution or '')[:300]}"
            )
        elif matches:
            hint_parts.append("历史上出现过同类失败（尚未总结结论），可换思路排查。")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "experience_match_failed",
            extra={"event": "experience_match_failed", "reason": str(exc)[:200]},
        )
    _ = messages  # 预留：经验注入未来可结合场景上下文精简
    return ("\n" + "\n".join(hint_parts)) if hint_parts else ""
