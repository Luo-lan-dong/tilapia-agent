"""编排 AI 产物感知与文件访问技能（ADR-068）。

补齐「中枢有产物、编排 AI 看不见/摸不着」的能力断点：

- ``list_artifacts``：当前会话关联任务的产物状态（export.zip 存在性、
  文件树清单、增量同步覆盖数），支持 ``build_zip=true`` 本地打包兜底；
- ``read_file``：会话范围白名单内读文本文件——产物目录 / 会话附件 /
  会话工作目录三类来源，UTF-8 解码判定文本，超限截断。

数据全部来自中枢本地缓存（ADR-028 落盘），不触碰节点；目录白名单限定
会话 scope，禁止全盘任意路径读取。
"""

import logging
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.session_attachment import SessionAttachment
from app.models.task_building_session import TaskBuildingSession
from app.services.artifact_service import artifact_service
from app.skills.base import RESULT_EVENT, Skill, SkillContext, SkillResult

logger = logging.getLogger(__name__)

#: 清单展示条数上限（防大项目刷爆编排上下文）
MAX_LIST_ITEMS = 50
#: read_file 单次读取上限（字节），超出截断
READ_MAX_BYTES = 2 * 1024 * 1024
#: 二进制判定：内容含 NUL 字节即视为二进制
_NUL_BYTE = b"\x00"

#: read_file 失败时的可读范围说明（指引 AI 自行纠正）
_READ_SCOPE_HINT = (
    "可读范围：① 当前会话任务的产物目录内相对路径（先用 list_artifacts 查清单）；"
    "② 本会话上传的附件（按文件名）；③ 会话绑定的工作目录内路径。"
)


def _result(success: bool, summary: str, data: dict | None = None) -> dict:
    """构造技能结果事件。"""
    return {
        "event": RESULT_EVENT,
        "data": SkillResult(success=success, summary=summary, data=data or {}),
    }


async def _resolve_task_id(
    session: AsyncSession,
    context: SkillContext,
    explicit: str,
) -> str | None:
    """解析产物归属任务 ID：显式参数 > 会话关联 task_id > 任务链父任务。

    Returns:
        任务 UUID 字符串；三者皆缺时返回 None。
    """
    raw = explicit.strip()
    if raw:
        return raw
    if context.session_id:
        try:
            chat_session = await session.get(TaskBuildingSession, uuid.UUID(context.session_id))
        except ValueError:
            chat_session = None
        if chat_session is not None and chat_session.task_id is not None:
            return str(chat_session.task_id)
    if context.parent_task_id:
        return str(context.parent_task_id)
    return None


async def _find_session_attachment(
    session: AsyncSession, session_id: str, path: str
) -> Path | None:
    """按文件名在本会话附件中解析本地路径（宽来源②）。"""
    name = Path(path.replace("\\", "/")).name
    if not name:
        return None
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    record = (
        await session.execute(
            select(SessionAttachment)
            .where(
                SessionAttachment.session_id == sid,
                SessionAttachment.file_name == name,
            )
            .order_by(SessionAttachment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if record is None:
        return None
    candidate = Path(get_settings().attachment_storage_dir) / record.storage_path
    return candidate if candidate.is_file() else None


async def _resolve_readable_path(
    session: AsyncSession, context: SkillContext, path: str
) -> tuple[Path | None, str]:
    """三类白名单来源依序解析（ADR-068 §3.2），返回 (路径, 来源说明)。"""
    task_id = await _resolve_task_id(session, context, "")
    if task_id:
        candidate = artifact_service.get_cached_file_path(task_id, path)
        if candidate is not None:
            return candidate, "产物目录"
    if context.session_id:
        candidate = await _find_session_attachment(session, context.session_id, path)
        if candidate is not None:
            return candidate, "会话附件"
    output_dir = context.output_dir
    if output_dir:
        base = Path(output_dir).resolve()
        candidate = (base / path).resolve()
        if candidate.is_relative_to(base) and candidate.is_file():
            return candidate, "工作目录"
    return None, ""


def _decode_text(raw: bytes, truncated: bool) -> str | None:
    """文本解码：NUL/非法 UTF-8 判二进制；截断边界容错。非文本返回 None。"""
    if _NUL_BYTE in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="ignore") if truncated else None


class ListArtifactsSkill(Skill):
    """查询当前会话任务的产物状态（纯本地缓存，不触碰节点）。"""

    name = "list_artifacts"
    display_name = "查询任务产物"
    description = (
        "查询当前会话关联任务的产物清单：export.zip 打包产物（存在性/大小）、"
        "工作目录文件清单、增量同步覆盖情况。部署前核对产物、找文件名时调用；"
        "数据来自中枢本地缓存，不需要节点在线。export.zip 可直接作为宝塔 "
        "Upload 的 file_name 上传（中枢自动代执行本机上传）。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "可选，任务 UUID（缺省用当前会话关联的任务）",
            },
            "build_zip": {
                "type": "boolean",
                "description": (
                    "export.zip 缺失时是否用本地已同步文件树打包兜底"
                    "（可能缺大文件，结果会附警告）"
                ),
            },
        },
    }
    returns = "产物状态：export.zip（大小）/ 文件清单（path+size）/ 同步覆盖数"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict, None]:
        """组装产物状态摘要（zip + 清单 + 同步数 + 兜底警告）。"""
        task_id = await _resolve_task_id(
            session, context, str(context.args.get("task_id") or "")
        )
        if task_id is None:
            yield _result(
                False,
                "当前会话没有关联任务，无产物可查。请先创建/派发任务，"
                "或用 task_id 参数显式指定任务。",
            )
            return
        built_from_cache = False
        zip_path = artifact_service.get_cached_zip_path(task_id)
        if zip_path is None and bool(context.args.get("build_zip")):
            try:
                zip_path = artifact_service.build_zip_from_cache(task_id)
                built_from_cache = True
            except RuntimeError as exc:
                yield _result(False, f"本地打包兜底失败：{exc}")
                return
        yield _result(True, self._summary(task_id, zip_path, built_from_cache))

    @staticmethod
    def _summary(task_id: str, zip_path: Path | None, built_from_cache: bool) -> str:
        """渲染产物状态文本。"""
        lines = [f"任务 {task_id[:8]} 的产物状态："]
        if zip_path is not None:
            size_mb = zip_path.stat().st_size / (1024 * 1024)
            lines.append(
                f"- export.zip：存在（{size_mb:.2f} MB）。可直接作为宝塔 Upload 的"
                " file_name=export.zip 上传（中枢自动代执行本机上传）。"
            )
        else:
            lines.append(
                "- export.zip：不存在（全量同步未完成或失败）。"
                "可用 build_zip=true 从本地缓存打包兜底。"
            )
        tree = artifact_service.get_tree(task_id)
        files = artifact_service.list_cached_files(task_id)
        synced_count = len(artifact_service.get_synced_state(task_id))
        if files:
            note = "，节点侧清单被截断" if (tree or {}).get("truncated") else ""
            lines.append(f"- 文件清单：{len(files)} 项（本地已同步 {synced_count} 项{note}）")
            for item in files[:MAX_LIST_ITEMS]:
                lines.append(f"  - {item.get('path')}（{item.get('size')} B）")
            if len(files) > MAX_LIST_ITEMS:
                lines.append(
                    f"  …（其余 {len(files) - MAX_LIST_ITEMS} 项略，可用 read_file 按路径读取）"
                )
        else:
            lines.append("- 文件清单：无（未收到节点清单上报，本地也无已同步文件）")
        if built_from_cache:
            lines.append(
                "⚠️ 本次 export.zip 由本地缓存散件打包：增量同步有单文件 2MB 上限，"
                "包内可能缺大文件（依赖包/二进制），部署前建议核验；"
                "节点在线时以重新全量同步的包为准。"
            )
        return "\n".join(lines)


class ReadFileSkill(Skill):
    """在会话范围白名单内读取文本文件（产物/附件/工作目录）。"""

    name = "read_file"
    display_name = "读取文件"
    description = (
        "读取文件文本内容（像编程 Agent 的读文件工具）。路径按三类来源解析："
        "① 当前会话任务产物目录内相对路径（如 src/main.py、README.md，先用 "
        "list_artifacts 查清单）；② 本会话上传的附件文件名；③ 会话绑定的工作"
        "目录内路径。文本文件返回全文（超 2MB 截断）；二进制文件拒绝。"
        "用途：部署前看 requirements.txt/package.json 判断项目类型、读配置、"
        "回看用户上传的文档。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径：产物相对路径 / 附件文件名 / 工作目录相对路径",
            },
        },
        "required": ["path"],
    }
    returns = "文件全文（含来源与大小元信息）；二进制/越界时报错说明"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict, None]:
        """解析路径 → 读取 → 解码判定 → 回传文本。"""
        path = str(context.args.get("path") or "").strip()
        if not path:
            yield _result(False, "参数不完整：必须提供 path。")
            return
        resolved, source = await _resolve_readable_path(session, context, path)
        if resolved is None:
            yield _result(False, f"文件不在可读范围内：{path}。{_READ_SCOPE_HINT}")
            return
        with resolved.open("rb") as file_handle:
            raw = file_handle.read(READ_MAX_BYTES + 1)
        truncated = len(raw) > READ_MAX_BYTES
        raw = raw[:READ_MAX_BYTES]
        text = _decode_text(raw, truncated)
        if text is None:
            yield _result(
                False,
                f"文件 {path} 是二进制内容，不支持文本读取"
                "（如需上传到服务器，直接把文件名交给宝塔 Upload 即可）。",
            )
            return
        size = resolved.stat().st_size
        logger.info(
            "orchestrator_file_read",
            extra={
                "event": "orchestrator_file_read",
                "session_id": context.session_id,
                "source": source,
                "size": size,
                "truncated": truncated,
            },
        )
        header = (
            f"文件 {path}（来源：{source}，{size} 字节"
            + ("，超出 2MB 已截断" if truncated else "")
            + "）：\n"
        )
        yield _result(
            True,
            header + text,
            data={"path": path, "source": source, "size": size, "truncated": truncated},
        )
