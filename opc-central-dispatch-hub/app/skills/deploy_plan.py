"""部署规划技能（ADR-050 增补，用户需求：部署前先规划）。

把部署待办清单写入右侧「任务清单」面板（specification.tasks），并在
对话中展示，经用户确认后再按清单逐步执行。清单随会话持久化，恢复
会话/切换页面后仍可见。
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.base import RESULT_EVENT, Skill, SkillContext, SkillResult

_EMPTY_SPECIFICATION: dict[str, str] = {"spec": "", "tasks": "", "checklist": ""}


class DeployPlanSkill(Skill):
    """把部署待办清单写入「任务清单」面板。"""

    name = "deploy_plan"
    display_name = "列出部署待办"
    description = (
        "把部署规划待办写入右侧「任务清单」面板。读完 bt_deploy_guide 并"
        "确认目标服务器后、执行任何变更工具之前必须先调用：steps 为按顺序"
        "的待办步骤（含端口确认/防火墙放行/数据库创建等前置条件检查项），"
        "向用户展示并确认后按清单逐步执行；每完成一步可重新调用更新清单"
        "（已完成项加 [x] 标记）。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "目标服务器/环境简述（如：宝塔服务器-192.168.101.13）",
            },
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "按顺序的部署待办步骤（每步一条；已完成项前缀 [x]）",
            },
        },
        "required": ["steps"],
    }
    returns = "清单已写入「任务清单」面板（steps 计数）"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """渲染待办清单并更新 specification.tasks。"""
        steps = context.args.get("steps")
        target = str(context.args.get("target") or "").strip()
        if not isinstance(steps, list) or not steps:
            yield {
                "event": RESULT_EVENT,
                "data": SkillResult(
                    success=False,
                    summary="参数不完整：必须提供 steps（按顺序的待办步骤字符串数组）。",
                ),
            }
            return
        items = [str(step).strip() for step in steps if str(step).strip()]
        if not items:
            yield {
                "event": RESULT_EVENT,
                "data": SkillResult(success=False, summary="steps 不能全为空。"),
            }
            return

        lines = [f"{index}. {item}" for index, item in enumerate(items, 1)]
        header = "# 部署待办清单\n\n"
        if target:
            header += f"**目标**：{target}\n\n"
        tasks_md = header + "\n".join(lines) + "\n"

        # specification 事件：前端右面板「任务清单」Tab 实时更新；
        # spec/checklist 保留会话现有值（TaskSpecification 三字段必填）
        current = context.current_specification or {}
        yield {
            "event": "specification",
            "data": {
                "spec": str(current.get("spec") or _EMPTY_SPECIFICATION["spec"]),
                "tasks": tasks_md,
                "checklist": str(current.get("checklist") or _EMPTY_SPECIFICATION["checklist"]),
                # 验收判据（ADR-061 M4）：本技能不改判据，原样保留
                "verify": current.get("verify"),
            },
        }
        yield {
            "event": RESULT_EVENT,
            "data": SkillResult(
                success=True,
                summary=(
                    f"部署待办清单（共 {len(items)} 步）已写入「任务清单」面板：\n"
                    + "\n".join(lines)
                    + "\n请向用户逐项确认（尤其端口要求与防火墙放行），确认后再按清单执行。"
                ),
                data={"steps_count": len(items), "target": target},
            ),
        }
