"""SOP 固化技能（ADR-051）：跑通流程后经用户确认沉淀为可复用 SOP。

闭环：部署/运维流程跑通 → AI 询问"是否固化为 SOP"→ 用户同意 →
本技能入库（source=learned）→「记忆 → SOP」Tab 可管理 → 下次
bt_deploy_guide 同场景命中注入。用户未确认时禁止调用。
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessError
from app.services.sop_service import SopService
from app.skills.base import Skill, SkillContext, SkillResult

_SOP_SERVICE = SopService()


class SopSaveSkill(Skill):
    """把已获用户确认的流程固化为 SOP。"""

    name = "sop_save"
    display_name = "固化部署 SOP"
    description = (
        "把跑通并经用户确认的部署/运维流程固化为 SOP（存入记忆库，"
        "「记忆 → SOP」页面可管理，下次同场景部署自动命中）。前置条件："
        "先向用户展示 SOP 草案（名称/场景/要点）并获得明确同意；用户未"
        "确认时禁止调用。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "SOP 名称（唯一，如：FastAPI 项目部署到宝塔）",
            },
            "scene": {
                "type": "string",
                "description": "场景标识（如 python/html/php 或自定义小写标识，后续 bt_deploy_guide 按此命中）",
            },
            "content": {
                "type": "string",
                "description": "SOP 正文 Markdown（要点式：关键步骤/参数/坑，供下次复用）",
            },
        },
        "required": ["name", "scene", "content"],
    }
    returns = "固化结果（SOP id/场景），下次同场景 bt_deploy_guide 自动命中"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """固化 SOP（用户已确认的草案）。"""
        name = str(context.args.get("name") or "").strip()
        scene = str(context.args.get("scene") or "").strip()
        content = str(context.args.get("content") or "").strip()
        if not name or not scene or not content:
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary="参数不完整：必须提供 name、scene、content（先向用户展示草案并确认）。",
                ),
            }
            return
        try:
            record = await _SOP_SERVICE.save(
                session, name=name, scene=scene, content=content, source="learned"
            )
        except BusinessError as exc:
            yield {
                "event": "_result",
                "data": SkillResult(success=False, summary=f"固化失败：{exc.detail}"),
            }
            return
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=(
                    f"SOP 已固化：「{record.name}」（场景 {record.scene}，id {record.id}）。\n"
                    "下次同场景部署时 bt_deploy_guide 会自动命中注入；"
                    "可在「记忆 → SOP」页面查看或编辑。"
                ),
                data={"sop_id": str(record.id), "scene": record.scene},
            ),
        }
