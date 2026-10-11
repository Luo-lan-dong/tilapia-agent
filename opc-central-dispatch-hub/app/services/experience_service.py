"""经验记忆服务（ADR-050 第三层：事后学习）。

闭环：失败自动记录（record_failure）→ 收尾总结回填（resolve，
AI 草案 + 用户确认）→ 同签名失败命中注入（find_matches + record_hit）。
经验是增益不是依赖：调用方（技能/编排）须自行兜底异常，service 内
不做静默吞异常——降级策略由调用方决定（技能侧 catch 后跳过）。
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.experience_record import ExperienceRecord

logger = logging.getLogger(__name__)

#: 错误签名截断上限（与表字段长度一致）
_SIGNATURE_MAX = 255
#: 归一化时去除的数字（端口/IP/耗时等易变部分，避免同错误因数字不同而失配）
_DIGITS_PATTERN = re.compile(r"\d+")
#: 归一化时压平的空白
_SPACES_PATTERN = re.compile(r"\s+")


def normalize_signature(error_msg: str) -> str:
    """错误文本 → 归一化签名：去数字、压空白、截断。

    保守策略：只去数字与多余空白，保留原文语义（误命中仅多注入一段
    提示文本，无副作用）；签名不同则不命中，宁漏勿错。
    """
    text = str(error_msg or "").strip()
    text = _DIGITS_PATTERN.sub("#", text)
    text = _SPACES_PATTERN.sub(" ", text)
    return text[:_SIGNATURE_MAX]


class ExperienceService:
    """经验记忆条目的记录、检索、回填与管理。"""

    async def record_failure(
        self,
        session: AsyncSession,
        *,
        tool: str,
        error_msg: str,
        args_summary: str | None = None,
        session_id: str | None = None,
        source_node_id: str | None = None,
        environment_signature: str | None = None,
    ) -> ExperienceRecord | None:
        """失败自动记录：写入 pending 条目（同 session+tool+签名去重）。

        ADR-061 M6：同时记录**来源节点**与环境签名快照，供跨机召回按环境匹配。
        返回新建条目；命中去重（已有同键 pending）时返回 None。
        """
        signature = normalize_signature(error_msg)
        if not signature:
            return None
        dup = await session.execute(
            select(ExperienceRecord).where(
                ExperienceRecord.tool == tool,
                ExperienceRecord.error_signature == signature,
                ExperienceRecord.status == "pending",
                ExperienceRecord.session_id == (str(session_id)[:64] if session_id else None),
            )
        )
        if dup.scalar_one_or_none() is not None:
            return None
        record = ExperienceRecord(
            tool=tool or "",
            error_signature=signature,
            problem=(
                f"工具 {tool} 调用失败：{error_msg}"
                + (f"；参数摘要：{args_summary}" if args_summary else "")
            )[:2000],
            status="pending",
            session_id=str(session_id)[:64] if session_id else None,
            source_node_id=str(source_node_id)[:64] if source_node_id else None,
            environment_signature=(
                str(environment_signature).strip()[:64] if environment_signature else None
            ),
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        logger.info(
            "experience_recorded",
            extra={
                "event": "experience_recorded",
                "experience_id": str(record.id),
                "tool": tool,
                "session_id": record.session_id,
            },
        )
        return record

    async def find_matches(
        self,
        session: AsyncSession,
        *,
        signature: str,
        limit: int = 3,
    ) -> list[ExperienceRecord]:
        """按签名检索历史经验（resolved 优先，其次 hit_count/时间倒序）。"""
        normalized = normalize_signature(signature)
        if not normalized:
            return []
        result = await session.execute(
            select(ExperienceRecord)
            .where(ExperienceRecord.error_signature == normalized)
            .order_by(
                ExperienceRecord.status.desc(),  # resolved 在前（'r' > 'p'）
                ExperienceRecord.hit_count.desc(),
                ExperienceRecord.created_at.desc(),
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def recall_for_dispatch(
        self,
        session: AsyncSession,
        *,
        environment_signature: str | None,
        limit: int = 5,
    ) -> list[ExperienceRecord]:
        """派发前召回（ADR-061 M6）：按**目标节点环境**取已总结经验。

        匹配策略（保守，宁漏勿错）：
        1. 优先取 `environment_signature` **与环境签名一致**的 resolved 经验
           （同环境踩过的坑才最可能复现）；
        2. 若一条都没有，再回退取 `environment_signature IS NULL` 的旧经验
           （ADR-050 时期无环境字段，属兜底；明确不跨环境乱用）。
        排序：命中次数降序 → 更新时间降序。
        """
        signature = (str(environment_signature).strip()[:64] if environment_signature else None)
        base = (
            select(ExperienceRecord)
            .where(ExperienceRecord.status == "resolved")
            .order_by(ExperienceRecord.hit_count.desc(), ExperienceRecord.updated_at.desc())
            .limit(limit)
        )
        if signature:
            matched = list(
                (await session.execute(base.where(ExperienceRecord.environment_signature == signature)))
                .scalars()
                .all()
            )
            if matched:
                return matched
            return list(
                (
                    await session.execute(
                        base.where(ExperienceRecord.environment_signature.is_(None))
                    )
                )
                .scalars()
                .all()
            )
        return list(
            (
                await session.execute(
                    base.where(ExperienceRecord.environment_signature.is_(None))
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    def render_dispatch_doc(records: list[ExperienceRecord], environment_signature: str | None) -> str:
        """把召回结果渲染为元规则文档正文（落到 `.tilacode/rules/experiences.md`）。"""
        env_label = environment_signature or "未上报"
        lines = [
            "# 历史经验（自动召回，供参考）",
            "",
            f"> 环境签名：`{env_label}`；来源：中枢经验记忆（ADR-050/ADR-061 M6）。",
            "> 这些是**同环境**下已经踩过并确认过根因/解法的问题，遇到相似现象优先参考；",
            "> 与当前任务无关时忽略即可，不要为了「用上经验」而改变既定方案。",
            "",
        ]
        for index, record in enumerate(records, 1):
            lines.append(f"## {index}. {record.tool or '（非工具类）'} · {record.error_signature}")
            if record.problem:
                lines.append(f"- 现象：{record.problem[:500]}")
            if record.root_cause:
                lines.append(f"- 根因：{record.root_cause[:500]}")
            if record.solution:
                lines.append(f"- 解法：{record.solution[:1000]}")
            lines.append(f"- 命中次数：{record.hit_count}")
            lines.append("")
        return "\n".join(lines)

    async def record_hits(self, session: AsyncSession, records: list[ExperienceRecord]) -> int:
        """召回命中计数（衡量经验价值；失败不影响派发）。"""
        if not records:
            return 0
        for record in records:
            record.hit_count = (record.hit_count or 0) + 1
        await session.commit()
        logger.info(
            "experience_recall_hits",
            extra={"event": "experience_recall_hits", "count": len(records)},
        )
        return len(records)

    async def record_hit(self, session: AsyncSession, record: ExperienceRecord) -> None:
        """命中计数 +1（注入历史经验时调用）。"""
        record.hit_count = (record.hit_count or 0) + 1
        await session.commit()

    async def resolve(
        self,
        session: AsyncSession,
        record_id: Any,
        *,
        root_cause: str,
        solution: str,
    ) -> ExperienceRecord | None:
        """总结回填：root_cause/solution 写入、status=resolved。"""
        record = await self.get_record(session, record_id)
        if record is None:
            return None
        record.root_cause = root_cause
        record.solution = solution
        record.status = "resolved"
        record.resolved_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(record)
        logger.info(
            "experience_resolved",
            extra={"event": "experience_resolved", "experience_id": str(record.id)},
        )
        return record

    async def get_record(self, session: AsyncSession, record_id: Any) -> ExperienceRecord | None:
        """按 ID 查询条目。"""
        result = await session.execute(
            select(ExperienceRecord).where(ExperienceRecord.id == record_id)
        )
        return result.scalar_one_or_none()

    async def list_consolidation_candidates(
        self, session: AsyncSession, *, limit: int = 10
    ) -> list[ExperienceRecord]:
        """定时整理的候选：**待总结**条目，最旧的优先（ADR-061 M6 定时整理）。

        只取 `pending`——已起草（`draft`）的等人工确认，已 resolved 的不再动。
        """
        result = await session.execute(
            select(ExperienceRecord)
            .where(ExperienceRecord.status == "pending")
            .order_by(ExperienceRecord.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def start_draft(
        self,
        session: AsyncSession,
        record: ExperienceRecord,
        *,
        root_cause: str,
        solution: str,
    ) -> ExperienceRecord:
        """写入 **AI 草稿**（status=draft）：内容落库但**不生效**，等人工确认。

        确认路径复用既有 `experience_resolve` 技能（它把 status 置 resolved）；
        草稿态不会被派发前召回（召回只认 resolved），因此**草稿不会污染子 Agent**。
        """
        record.root_cause = root_cause[:4000]
        record.solution = solution[:8000]
        record.status = "draft"
        await session.commit()
        await session.refresh(record)
        logger.info(
            "experience_draft_created",
            extra={"event": "experience_draft_created", "experience_id": str(record.id)},
        )
        return record

    async def list_records(
        self,
        session: AsyncSession,
        *,
        keyword: str | None = None,
        status_filter: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ExperienceRecord], int]:
        """列表：keyword 模糊（tool/signature/problem/solution）+ status 过滤 + 分页。"""
        stmt = select(ExperienceRecord)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    ExperienceRecord.tool.ilike(pattern),
                    ExperienceRecord.error_signature.ilike(pattern),
                    ExperienceRecord.problem.ilike(pattern),
                    ExperienceRecord.root_cause.ilike(pattern),
                    ExperienceRecord.solution.ilike(pattern),
                )
            )
        if status_filter:
            stmt = stmt.where(ExperienceRecord.status == status_filter)
        total_result = await session.execute(select(func.count()).select_from(stmt.subquery()))
        total = int(total_result.scalar_one())
        result = await session.execute(
            stmt.order_by(ExperienceRecord.created_at.desc())
            .offset(max(page - 1, 0) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def list_session_pending(
        self,
        session: AsyncSession,
        session_id: str | None,
    ) -> list[ExperienceRecord]:
        """列出指定会话的 pending 条目（收尾总结钩子用）。"""
        if not session_id:
            return []
        result = await session.execute(
            select(ExperienceRecord)
            .where(
                ExperienceRecord.session_id == str(session_id)[:64],
                ExperienceRecord.status == "pending",
            )
            .order_by(ExperienceRecord.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete_record(self, session: AsyncSession, record: ExperienceRecord) -> None:
        """删除条目。"""
        await session.delete(record)
        await session.commit()
        logger.info(
            "experience_deleted",
            extra={"event": "experience_deleted", "experience_id": str(record.id)},
        )
