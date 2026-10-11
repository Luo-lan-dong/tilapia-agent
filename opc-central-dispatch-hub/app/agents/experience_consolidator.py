"""经验定时整理（ADR-061 M6）：把 pending 现象整理成**草稿**，等人工确认。

**为什么是草稿而不是直接生效**（用户已定）：AI 提炼的根因/解法可能有误，
而经验一旦 `resolved` 就会在派发前注入到子 Agent 上下文里——错了会持续误导。
因此本模块只写 `status="draft"`：召回只认 `resolved`，草稿**不会**进入任何任务的上下文；
用户在对话里确认后由既有 `experience_resolve` 技能置为 `resolved`。

**触发节奏**：由调度循环按日触发（与链路事件清理同一守卫模式），单次最多处理
`batch_size` 条；LLM 不可用/解析失败都只记日志，**不抛错、不影响调度主循环**。
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.deepseek import DeepSeekAPIError, DeepSeekClient
from app.models.experience_record import ExperienceRecord
from app.services.experience_service import ExperienceService
from app.services.llm_provider_service import LLMProviderService

logger = logging.getLogger(__name__)

#: 单次整理上限（控制 LLM 开销）
DEFAULT_BATCH_SIZE = 10
#: 草稿提示词：要求两段式输出，便于容错解析
_SYSTEM_PROMPT = (
    "你是 Tilapia 中枢的「故障复盘助手」。用户会给你一条**工具调用失败的现象快照**，"
    "请推断最可能的根因与可操作解法。要求：\n"
    "1. 只输出两行，格式严格为：\n根因：<一句话>\n解法：<一到三句话，必须是可执行的动作>\n"
    "2. 不确定时给出最可能的假设并明确写「（待确认）」；不要编造未出现的错误码或文件路径。\n"
    "3. 不要输出任何额外解释、标题或 Markdown 代码块。"
)


def parse_draft(text: str) -> tuple[str, str] | None:
    """从 LLM 输出解析「根因/解法」；缺任一段返回 None（保持 pending，下次再试）。"""
    root_cause: str | None = None
    solution_lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().lstrip("-*# ").strip()
        if not line:
            continue
        if line.startswith("根因：") or line.startswith("根因:"):
            root_cause = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("解法：") or line.startswith("解法:"):
            solution_lines.append(line.split("：", 1)[-1].split(":", 1)[-1].strip())
        elif solution_lines:
            # 解法可能被折成多行：续行归入解法
            solution_lines.append(line)
    solution = " ".join(part for part in solution_lines if part).strip()
    if not root_cause or not solution:
        return None
    return root_cause, solution


class ExperienceConsolidator:
    """把 pending 经验整理成草稿（可注入 client 便于测试）。"""

    def __init__(self, client: DeepSeekClient | None = None) -> None:
        self._client = client

    async def _get_client(self, session: AsyncSession) -> DeepSeekClient:
        """获取 DeepSeek 客户端（未注入时按当前使用中的模型配置创建）。"""
        if self._client is None:
            config = await LLMProviderService().get_active_provider(session)
            self._client = DeepSeekClient(config, scene="experience_consolidation")
        return self._client

    async def draft_for(
        self, session: AsyncSession, record: ExperienceRecord
    ) -> tuple[str, str] | None:
        """为单条 pending 记录生成草稿；失败返回 None（不破坏原记录）。"""
        user_prompt = (
            f"工具：{record.tool or '（非工具类）'}\n"
            f"错误签名：{record.error_signature}\n"
            f"现象快照：{(record.problem or '')[:1500]}"
        )
        try:
            client = await self._get_client(session)
            text = await client.chat_completion(_SYSTEM_PROMPT, user_prompt)
        except (DeepSeekAPIError, ValueError, OSError) as exc:
            logger.warning(
                "experience_draft_llm_failed",
                extra={
                    "event": "experience_draft_llm_failed",
                    "experience_id": str(record.id),
                    "error": str(exc),
                },
            )
            return None
        parsed = parse_draft(text)
        if parsed is None:
            logger.warning(
                "experience_draft_parse_failed",
                extra={
                    "event": "experience_draft_parse_failed",
                    "experience_id": str(record.id),
                    "length": len(text or ""),
                },
            )
        return parsed

    async def consolidate(
        self, session: AsyncSession, *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> dict[str, int]:
        """整理一批 pending 记录为草稿。

        Returns:
            ``{"candidates": 候选数, "drafted": 起草成功数, "failed": 失败数}``。
        """
        service = ExperienceService()
        candidates = await service.list_consolidation_candidates(session, limit=batch_size)
        drafted = 0
        failed = 0
        for record in candidates:
            parsed = await self.draft_for(session, record)
            if parsed is None:
                failed += 1
                continue
            await service.start_draft(
                session, record, root_cause=parsed[0], solution=parsed[1]
            )
            drafted += 1
        if candidates:
            logger.info(
                "experience_consolidation_completed",
                extra={
                    "event": "experience_consolidation_completed",
                    "candidates": len(candidates),
                    "drafted": drafted,
                    "failed": failed,
                },
            )
        return {"candidates": len(candidates), "drafted": drafted, "failed": failed}


def format_draft_hint(records: list[ExperienceRecord]) -> str:
    """把草稿渲染成对话提示（供编排器在会话里请用户确认）。"""
    if not records:
        return ""
    lines = ["以下经验已由中枢**起草**（尚未生效，请确认或修改）："]
    for record in records:
        lines.append(
            f"- [{record.id}] {record.tool or '（非工具类）'} · {record.error_signature}\n"
            f"  根因：{record.root_cause}\n  解法：{record.solution}"
        )
    lines.append("确认后调用 experience_resolve 回填（或告诉我要修改的地方）。")
    return "\n".join(lines)
