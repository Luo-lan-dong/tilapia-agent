"""跨 Agent 任务现场迁移服务（ADR-034）。

职责：
- 任务链项目上下文记忆（project_context.md）的存储与查询（链维度覆盖更新）；
- 派发现场迁移判定与 bundle 构建：取链上最新完成任务的产物包（zip），
  注入最新 project_context.md，暂存并以一次性 token 提供下载；
- 一次性下载校验（token + TTL）。

产物来源说明：同任务链任务共用同一工作目录（ADR-028），最新完成任务的全量
产物包（export.zip，任务完成时中枢自动向在线 Agent 拉取）即该链最新现场。

排除规则无需中枢处理：export.zip 由 Agent 打包，node_modules/.git/.tilacode
等可重装目录已在 Agent 侧 file_scanner 排除（ADR-028）。
"""

import asyncio
import hashlib
import logging
import secrets
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.task import Task, TaskStatus
from app.models.task_chain_context import TaskChainContext

logger = logging.getLogger(__name__)

#: context 文件在 bundle 内的固定路径（Agent 端按此识别）
CONTEXT_FILE_NAME = "project_context.md"


@dataclass
class HandoffBundle:
    """构造完成的现场迁移信息（并入派发 payload.handoff）。"""

    bundle_path: str
    sha256: str
    size: int
    context_md: str | None


@dataclass
class _BundleEntry:
    """一次性下载注册表条目。"""

    path: Path
    expires_at: float
    task_id: str


# 进程内一次性下载注册表（单实例部署；token 即用即焚 + 惰性过期清理）
_bundle_registry: dict[str, _BundleEntry] = {}


def _bundle_dir() -> Path:
    return Path(get_settings().handoff_cache_dir)


def chain_root(task: Task) -> uuid.UUID:
    """任务链根任务 ID（根任务自身 root_task_id 为 NULL，取自身 ID）。"""
    return task.root_task_id or task.id


async def upsert_chain_context(
    session: AsyncSession,
    root_task_id: uuid.UUID,
    context_md: str,
    source_task_id: uuid.UUID,
) -> None:
    """按任务链覆盖更新项目上下文记忆（completed 事件处理调用）。"""
    result = await session.execute(
        select(TaskChainContext).where(TaskChainContext.root_task_id == root_task_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = TaskChainContext(
            root_task_id=root_task_id,
            context_md=context_md,
            source_task_id=source_task_id,
        )
        session.add(record)
    else:
        record.context_md = context_md
        record.source_task_id = source_task_id
    await session.commit()
    logger.info(
        "task_chain_context_upserted",
        extra={
            "event": "task_chain_context_upserted",
            "root_task_id": str(root_task_id),
            "source_task_id": str(source_task_id),
            "size": len(context_md),
        },
    )


async def get_chain_context(session: AsyncSession, root_task_id: uuid.UUID) -> str | None:
    """查询任务链最新项目上下文记忆。"""
    result = await session.execute(
        select(TaskChainContext).where(TaskChainContext.root_task_id == root_task_id)
    )
    record = result.scalar_one_or_none()
    return record.context_md if record is not None else None


def _task_artifact_dir(task_id: uuid.UUID) -> Path:
    return Path(get_settings().artifact_cache_dir) / str(task_id)


async def _resolve_source_bundle(
    session: AsyncSession,
    task: Task,
) -> tuple[Path, uuid.UUID] | None:
    """解析链上可用的现场来源：最新完成任务的 export.zip 或落盘文件目录。

    Returns:
        (bundle 源, 来源任务 ID)；链上无历史现场时返回 None。
    """
    root = chain_root(task)
    result = await session.execute(
        select(Task)
        .where(
            Task.status == TaskStatus.COMPLETED.value,
            Task.id != task.id,
            or_(Task.root_task_id == root, Task.id == root),
        )
        .order_by(Task.updated_at.desc())
    )
    completed_tasks = list(result.scalars().all())

    # 优先取最新完成的、带 export.zip 的任务（全量现场）
    for completed in completed_tasks:
        candidate = _task_artifact_dir(completed.id) / "export.zip"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate, completed.id

    # 回退：取最新完成任务目录下已落盘的零散文件（曾预览过的文件），
    # 有内容才可作为现场来源
    for completed in completed_tasks:
        task_dir = _task_artifact_dir(completed.id)
        if task_dir.is_dir() and any(task_dir.rglob("*")):
            return task_dir, completed.id
    return None


def _pack_directory(source_dir: Path, target_zip: Path) -> int:
    """把落盘文件目录打包为 zip（同步，调用方放线程池）。"""
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(source_dir.rglob("*")):
            if file_path.is_file() and file_path.name != ".manifest.json":
                archive.write(file_path, file_path.relative_to(source_dir).as_posix())
    return target_zip.stat().st_size


def _inject_context(source_zip: Path, target_zip: Path, context_md: str | None) -> int:
    """复制产物 zip 并注入 project_context.md（同步，调用方放线程池）。

    context_md 为 None 时仅复制（保持 zip 结构不变）。
    """
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(source_zip, "r") as src,
        zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for item in src.infolist():
            if item.filename == CONTEXT_FILE_NAME:
                continue
            dst.writestr(item, src.read(item.filename))
        if context_md:
            dst.writestr(CONTEXT_FILE_NAME, context_md)
    return target_zip.stat().st_size


async def build_handoff_payload(session: AsyncSession, task: Task) -> dict | None:
    """构造派发现场迁移 payload（非链任务/无现场/超上限时返回 None）。

    返回结构（并入 ``hub.task_dispatch.payload.handoff``）：
    ``{bundle_path, sha256, size, context_md}``——bundle_path 为中枢相对
    下载路径，Agent 以当前连接的中枢地址拼接下载（不依赖 hub_public_url 配置）。
    """
    settings = get_settings()
    root = chain_root(task)
    context_md = await get_chain_context(session, root)

    resolved = await _resolve_source_bundle(session, task)
    if resolved is None and context_md is None:
        return None

    bundle_path = _bundle_dir() / f"{task.id}.zip"

    def _build() -> int:
        if resolved is not None:
            source, _source_task_id = resolved
            if source.is_file():
                # 全量现场包：复制并注入上下文
                return _inject_context(source, bundle_path, context_md)
            # 零散落盘文件：目录打包后注入上下文
            size = _pack_directory(source, bundle_path)
            if context_md:
                with zipfile.ZipFile(bundle_path, "a", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr(CONTEXT_FILE_NAME, context_md)
            return size
        # 无产物仅有上下文记忆：仅注入 md 的最小 bundle
        return _write_context_only(bundle_path, context_md or "")

    try:
        size = await asyncio.to_thread(_build)
    except Exception:
        logger.exception(
            "handoff_bundle_build_failed",
            extra={"event": "handoff_bundle_build_failed", "task_id": str(task.id)},
        )
        return None

    if size > settings.handoff_max_bundle_bytes:
        logger.warning(
            "handoff_bundle_too_large",
            extra={
                "event": "handoff_bundle_too_large",
                "task_id": str(task.id),
                "size": size,
            },
        )
        bundle_path.unlink(missing_ok=True)
        return None

    digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    token = secrets.token_urlsafe(32)
    _bundle_registry[token] = _BundleEntry(
        path=bundle_path,
        expires_at=time.monotonic() + settings.handoff_bundle_ttl_seconds,
        task_id=str(task.id),
    )
    logger.info(
        "handoff_bundle_ready",
        extra={
            "event": "handoff_bundle_ready",
            "task_id": str(task.id),
            "size": size,
            "has_context": context_md is not None,
        },
    )
    return {
        "bundle_path": (f"/api/v1/tasks/{task.id}/handoff-bundle?token={token}"),
        "sha256": digest,
        "size": size,
        "context_md": context_md,
    }


def _write_context_only(bundle_path: Path, context_md: str) -> int:
    """无产物仅有上下文记忆时的最小 bundle（同步，调用方放线程池）。"""
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(CONTEXT_FILE_NAME, context_md)
    return bundle_path.stat().st_size


def resolve_bundle_download(task_id: str, token: str) -> Path | None:
    """校验一次性下载 token，返回 bundle 文件路径；无效/过期返回 None。"""
    _purge_expired()
    entry = _bundle_registry.get(token)
    if entry is None or entry.task_id != task_id:
        return None
    _bundle_registry.pop(token, None)  # 一次性：下载即焚
    if not entry.path.is_file():
        return None
    return entry.path


def _purge_expired() -> None:
    """惰性清理过期注册与暂存文件。"""
    now = time.monotonic()
    expired = [token for token, entry in _bundle_registry.items() if entry.expires_at < now]
    for token in expired:
        entry = _bundle_registry.pop(token)
        entry.path.unlink(missing_ok=True)
