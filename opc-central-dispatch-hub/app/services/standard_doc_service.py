"""开发规则文档服务（ADR-048）。

职责：
- ``standard_docs`` 表 CRUD（version 随更新自增）；
- 任务创建时的规则文档**内容快照**（写入 ``tasks.rule_docs``，快照后文档
  迭代/删除不影响已建任务，改派重跑一致）；
- 派发 payload 构造：规范正文（target=standard，落 docs/standards/）+
  **元规则自动附加**（target=meta-rule，落 .tilacode/rules/，含清单动态
  渲染与环境检查文档），全文不内联 WS payload，走下载 token（TTL 内可
  重复下载，支持 Agent 侧失败重试）。

目录结构（ADR-048 修订，复刻中枢两层结构）：
- ``.tilacode/rules/`` = 元规则（驱动者）：coding-standards.md（规则①）、
  decision-documents.md（规则②）、environment-check.md（环境检查）；
- ``docs/standards/`` = 规范正文（按技术栈命名，中枢编写或文档库下发）；
- ``docs/adr/``、``docs/spec/``、``docs/INDEX.md`` = 规则驱动的决策产物。
"""

import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError, ErrorCode
from app.core.rule_doc_templates import ENVIRONMENT_CHECK_TEMPLATE
from app.models.standard_doc import StandardDoc
from app.schemas.standard_doc import StandardDocCreate, StandardDocUpdate
from app.services.experience_service import ExperienceService

logger = logging.getLogger(__name__)

#: 文件名净化：保留字母数字中文与 . _ -，其余替换为下划线
_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9\u4e00-\u9fff._-]+")
#: 下载 token 有效期（秒）；覆盖 Agent 下载 + 重试窗口
_DOWNLOAD_TOKEN_TTL_SECONDS = 600.0
#: 元规则文档名（工程化任务自动附加下发，不经用户/LLM 选择）
_META_RULE_NAMES = ("coding-standards", "decision-documents")


@dataclass
class _RuleDocEntry:
    """规则文档下载注册表条目（TTL 内可重复下载）。"""

    task_id: str
    doc_id: str
    file_name: str
    content: bytes
    expires_at: float


# 进程内下载注册表（单实例部署；惰性过期清理）
_registry: dict[str, _RuleDocEntry] = {}


def sanitize_file_name(name: str) -> str:
    """把文档名净化为可安全落盘的文件名（不含扩展名）。

    非法字符替换为下划线；净化后为空时回退 ``doc-<随机8位>``。
    Agent 端落盘时还会取 basename 二次防越界（ADR-048 双重防护）。
    """
    cleaned = _SANITIZE_PATTERN.sub("_", name.strip()).strip("._")
    if not cleaned:
        cleaned = f"doc-{secrets.token_hex(4)}"
    return cleaned


def _purge_expired() -> None:
    """惰性清理过期注册条目。"""
    now = time.monotonic()
    for token in [t for t, entry in _registry.items() if entry.expires_at < now]:
        _registry.pop(token, None)


class StandardDocService:
    """开发规则文档服务。"""

    async def list_docs(self, session: AsyncSession) -> list[StandardDoc]:
        """查询全部文档（按更新时间倒序）。"""
        result = await session.execute(select(StandardDoc).order_by(StandardDoc.updated_at.desc()))
        docs = list(result.scalars().all())
        logger.debug(
            "standard_docs_queried",
            extra={"event": "standard_docs_queried", "count": len(docs)},
        )
        return docs

    async def get_doc(self, session: AsyncSession, doc_id) -> StandardDoc | None:
        """按 ID 查询文档。"""
        return await session.get(StandardDoc, doc_id)

    async def create_doc(self, session: AsyncSession, data: StandardDocCreate) -> StandardDoc:
        """创建文档；同名冲突返回 409。"""
        existing = await session.execute(select(StandardDoc).where(StandardDoc.name == data.name))
        if existing.scalar_one_or_none() is not None:
            raise BusinessError(
                status_code=status.HTTP_409_CONFLICT,
                biz_code=ErrorCode.DOC_NAME_DUPLICATE,
                detail=f"文档名已存在: {data.name}",
            )
        doc = StandardDoc(
            name=data.name,
            category=data.category,
            description=data.description,
            apply_when=data.apply_when,
            content=data.content,
            version=1,
            is_builtin=False,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        logger.info(
            "standard_doc_created",
            extra={"event": "standard_doc_created", "doc_id": str(doc.id), "doc_name": doc.name},
        )
        return doc

    async def update_doc(
        self, session: AsyncSession, doc: StandardDoc, data: StandardDocUpdate
    ) -> StandardDoc:
        """更新文档；每次更新 version 自增（锁定语义：历史任务用旧快照不受影响）。"""
        if data.category is not None:
            doc.category = data.category
        if data.description is not None:
            doc.description = data.description
        if data.apply_when is not None:
            doc.apply_when = data.apply_when
        if data.content is not None:
            doc.content = data.content
        doc.version += 1
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        logger.info(
            "standard_doc_updated",
            extra={
                "event": "standard_doc_updated",
                "doc_id": str(doc.id),
                "doc_name": doc.name,
                "version": doc.version,
            },
        )
        return doc

    async def delete_doc(self, session: AsyncSession, doc: StandardDoc) -> None:
        """删除文档（已建任务的快照不受影响）。"""
        await session.delete(doc)
        await session.commit()
        logger.info(
            "standard_doc_deleted",
            extra={"event": "standard_doc_deleted", "doc_id": str(doc.id), "doc_name": doc.name},
        )

    async def snapshot_rule_docs(
        self, session: AsyncSession, doc_ids: list
    ) -> list[dict] | None:
        """按文档 ID 列表生成内容快照（创建任务时版本锁定）。

        元规则文档（category=meta-rule）不允许作为规范正文绑定——它们由
        派发时自动附加（build_rule_docs_payload），误选时静默过滤并告警。

        Args:
            session: 异步数据库会话。
            doc_ids: 文档 ID 列表（自动去重）。

        Returns:
            快照列表 ``[{doc_id, name, version, apply_when, content}]``；
            ``doc_ids`` 为空或全部为元规则时返回 None（任务不绑定规范正文）。

        Raises:
            BusinessError: 任一文档 ID 不存在时 404。
        """
        unique_ids = list(dict.fromkeys(doc_ids))
        if not unique_ids:
            return None
        result = await session.execute(
            select(StandardDoc).where(StandardDoc.id.in_(unique_ids))
        )
        docs_by_id = {doc.id: doc for doc in result.scalars().all()}
        missing = [str(doc_id) for doc_id in unique_ids if doc_id not in docs_by_id]
        if missing:
            raise BusinessError(
                status_code=status.HTTP_404_NOT_FOUND,
                biz_code=ErrorCode.DOC_NOT_FOUND,
                detail=f"规则文档不存在: {', '.join(missing)}",
            )
        snapshots = []
        for doc_id in unique_ids:
            doc = docs_by_id[doc_id]
            if doc.category == "meta-rule":
                logger.warning(
                    "meta_rule_as_standard_rejected",
                    extra={
                        "event": "meta_rule_as_standard_rejected",
                        "doc_id": str(doc.id),
                        "doc_name": doc.name,
                    },
                )
                continue
            snapshots.append(
                {
                    "doc_id": str(doc.id),
                    "name": doc.name,
                    "version": doc.version,
                    "apply_when": doc.apply_when,
                    "content": doc.content,
                }
            )
        if not snapshots:
            return None
        logger.info(
            "task_rule_docs_snapshotted",
            extra={
                "event": "task_rule_docs_snapshotted",
                "doc_count": len(snapshots),
                "doc_ids": [s["doc_id"] for s in snapshots],
            },
        )
        return snapshots


def _render_standards_table(standards: list[dict]) -> str:
    """把本次下发的规范清单渲染为元规则①的清单表行（docs/standards/ 路径）。"""
    if not standards:
        return (
            "| （本次未随任务下发具体规范，按 docs/standards/ 目录现有文件判断） | （按需判断） |"
        )
    return "\n".join(
        f"| docs/standards/{item['file_name']}.md | {item['apply_when'] or '（未标注，按需判断）'} |"
        for item in standards
    )


def _render_env_table(requirements: list[dict]) -> str:
    """把环境要求清单渲染为环境检查文档的清单表行。"""
    return "\n".join(
        f"| {req.get('name', '')} | `{req.get('check_cmd', '')}` "
        f"| {req.get('min_version') or '-'} | {req.get('install_hint') or '-'} |"
        for req in requirements
    )


def _register_entry(
    task_id: str,
    *,
    doc_id: str,
    name: str,
    file_name: str,
    version: int | str,
    apply_when: str | None,
    target: str,
    content: str,
) -> dict:
    """渲染内容注册下载 token 并构造 payload 条目。"""
    content_bytes = content.encode("utf-8")
    token = secrets.token_urlsafe(32)
    _registry[token] = _RuleDocEntry(
        task_id=task_id,
        doc_id=doc_id,
        file_name=file_name,
        content=content_bytes,
        expires_at=time.monotonic() + _DOWNLOAD_TOKEN_TTL_SECONDS,
    )
    return {
        "id": doc_id,
        "name": name,
        "file_name": file_name,
        "version": version,
        "apply_when": apply_when,
        "target": target,
        "url": f"/api/v1/docs/download/rule-doc?token={token}",
        "sha256": hashlib.sha256(content_bytes).hexdigest(),
        "size": len(content_bytes),
    }


async def build_rule_docs_payload(session: AsyncSession, task, node=None) -> list[dict] | None:
    """从任务快照构造派发 payload 的 rule_docs 条目并注册下载 token。

    工程化任务（绑定了规范正文或环境清单）自动附加元规则两份
    （coding-standards 元规则①的 ``{{STANDARDS_TABLE}}`` 占位符渲染为本次
    下发的规范清单；decision-documents 元规则②原样），环境清单非空时再
    附加 environment-check.md（清单渲染）。

    ADR-061 M6：若目标节点有环境签名且中枢存在**同环境**的已总结经验，
    再附加一份 ``experiences.md``（派发前召回，落 ``.tilacode/rules/``），
    并在 AGENTS.md 索引中登记——子 Agent 开工前即能看到"同环境踩过的坑"。

    Args:
        session: 异步数据库会话（读取元规则模板与经验记忆）。
        task: 待派发任务（含 rule_docs 快照与 environment_requirements）。
        node: 目标节点（可选；用于取环境签名做经验召回）。

    Returns:
        payload 条目列表（target=standard 落 docs/standards/，
        target=meta-rule 落 .tilacode/rules/）；无任何可下发内容时返回 None。
    """
    snapshots = task.rule_docs
    environment = getattr(task, "environment_requirements", None) or []
    # 派发前召回（ADR-061 M6）：经验是增益不是依赖，任何异常都不得阻断派发
    experiences: list = []
    environment_signature = getattr(node, "environment_signature", None) if node else None
    try:
        experiences = await ExperienceService().recall_for_dispatch(
            session, environment_signature=environment_signature
        )
    except Exception:  # noqa: BLE001 — 经验召回是旁路
        logger.warning(
            "experience_recall_failed",
            extra={"event": "experience_recall_failed", "task_id": str(task.id)},
            exc_info=True,
        )
        experiences = []
    if not snapshots and not environment and not experiences:
        return None
    _purge_expired()
    task_id = str(task.id)
    entries: list[dict] = []
    standards_for_render: list[dict] = []

    # 1. 规范正文（target=standard → docs/standards/）
    for snapshot in snapshots or []:
        file_name = sanitize_file_name(snapshot["name"])
        entries.append(
            _register_entry(
                task_id,
                doc_id=snapshot["doc_id"],
                name=snapshot["name"],
                file_name=file_name,
                version=snapshot["version"],
                apply_when=snapshot["apply_when"],
                target="standard",
                content=snapshot["content"],
            )
        )
        standards_for_render.append({"file_name": file_name, "apply_when": snapshot["apply_when"]})

    # 2. 元规则自动附加（target=meta-rule → .tilacode/rules/）
    result = await session.execute(
        select(StandardDoc).where(
            StandardDoc.name.in_(_META_RULE_NAMES), StandardDoc.is_builtin.is_(True)
        )
    )
    meta_docs = list(result.scalars().all())
    for doc in meta_docs:
        content = doc.content
        if doc.name == "coding-standards":
            content = content.replace(
                "{{STANDARDS_TABLE}}", _render_standards_table(standards_for_render)
            )
        entries.append(
            _register_entry(
                task_id,
                doc_id=str(doc.id),
                name=doc.name,
                file_name=sanitize_file_name(doc.name),
                version=doc.version,
                apply_when=doc.apply_when,
                target="meta-rule",
                content=content,
            )
        )
    if len(meta_docs) < len(_META_RULE_NAMES):
        logger.warning(
            "meta_rule_templates_missing",
            extra={
                "event": "meta_rule_templates_missing",
                "task_id": task_id,
                "found": [doc.name for doc in meta_docs],
            },
        )

    # 3. 环境检查文档（清单非空时附加，target=meta-rule）
    if environment:
        content = ENVIRONMENT_CHECK_TEMPLATE.replace(
            "{{ENV_TABLE}}", _render_env_table(environment)
        )
        entries.append(
            _register_entry(
                task_id,
                doc_id="environment-check",
                name="environment-check",
                file_name="environment-check",
                version=1,
                apply_when="项目首次开发时执行（.tilacode/environment.md 不存在时）",
                target="meta-rule",
                content=content,
            )
        )

    # 4. 历史经验文档（ADR-061 M6，派发前召回；仅当有同环境经验时附加）
    if experiences:
        experience_content = ExperienceService.render_dispatch_doc(
            experiences, environment_signature
        )
        entries.append(
            _register_entry(
                task_id,
                doc_id="experiences",
                name="experiences",
                file_name="experiences",
                version=1,
                apply_when="开工前浏览；遇到与「现象」相符的报错时优先按「解法」处理",
                target="meta-rule",
                content=experience_content,
            )
        )
        try:
            await ExperienceService().record_hits(session, experiences)
        except Exception:  # noqa: BLE001 — 命中计数是旁路，失败不影响派发
            logger.warning(
                "experience_hit_count_failed",
                extra={"event": "experience_hit_count_failed", "task_id": task_id},
                exc_info=True,
            )

    logger.info(
        "rule_docs_payload_ready",
        extra={
            "event": "rule_docs_payload_ready",
            "task_id": task_id,
            "doc_count": len(entries),
            "experience_count": len(experiences),
        },
    )
    return entries


def resolve_rule_doc_download(token: str) -> tuple[str, bytes] | None:
    """校验下载 token，返回 (file_name, content)；无效/过期返回 None。"""
    _purge_expired()
    entry = _registry.get(token)
    if entry is None:
        return None
    return entry.file_name, entry.content
