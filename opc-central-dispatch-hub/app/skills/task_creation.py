"""任务创建技能：把对话生成的三件套规范创建为正式任务（ADR-033）。

补全 AI 自主派发的流程断层：三件套生成后只是"草案"，旧链路依赖用户
手动点击「创建任务」按钮落库；本技能让 AI 在用户说"开始执行/派发给
某节点"时自主完成任务创建（pending 入队），随后由 LLM 继续调用
``smart_dispatch`` 完成派发。

规则包绑定（ADR-048）：参数 ``rule_doc_names`` 指定要绑定的规则文档名
（来自 list_standard_docs 查询结果），创建时锁定内容快照随派发下发；
用户未要求工程化时不绑定，节点直接按三件套实施。
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.standard_doc import StandardDoc
from app.schemas.task import TaskCreateRequest
from app.schemas.task_building import TaskSpecification
from app.services.task_service import TaskService
from app.services.verify_plan import normalize_verify_plan
from app.skills.base import Skill, SkillContext, SkillResult

# 前端 useTaskBuildingChat 的初始空模板（EMPTY_SPECIFICATION），
# 与其完全一致视为"尚未生成实质内容"
_EMPTY_SPEC_TEMPLATES = {
    "spec": "# 任务规范\n\n在左侧输入你的需求，AI 会通过追问帮你澄清，并逐步完善这份规范。",
    "tasks": "# 任务清单\n\n等待 AI 生成。",
    "checklist": "# 验收清单\n\n等待 AI 生成。",
}


def _spec_is_substantive(specification: dict[str, Any] | None) -> bool:
    """判断三件套是否有实质内容（缺失或仍是初始空模板视为无）。"""
    if not specification:
        return False
    for key, empty_text in _EMPTY_SPEC_TEMPLATES.items():
        value = (specification.get(key) or "").strip()
        if not value or value == empty_text:
            return False
    return True


class CreateTaskSkill(Skill):
    """将当前对话生成的三件套规范创建为正式任务。"""

    name = "create_task"
    display_name = "创建任务"
    description = (
        "把当前对话已生成的三件套规范文档（spec/tasks/checklist）创建为正式任务"
        "（pending 入队，并自动关联当前对话）。当用户要求创建任务、开始执行、"
        "派发给某个节点，而当前还没有已创建的任务时，先调用本技能；"
        "创建成功后向用户报告，经用户确认派发后再用 smart_dispatch。"
        "注意：同一会话内再次创建会自动挂接"
        "上一任务形成迭代链（Agent 在原项目上增量修改），因此 requirement"
        " 请写清本轮的修改点。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "requirement": {
                "type": "string",
                "description": "可选，任务需求标题；缺省取对话首条用户消息",
            },
            "rule_doc_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "可选，要绑定到任务的规则文档名列表（名称须来自 "
                    "list_standard_docs 的查询结果）。仅在用户确认需要工程化/"
                    "代码规范约束时传入；用户未要求时不传，节点将直接按三件套实施"
                ),
            },
            "environment": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "环境名，如 Python"},
                        "check_cmd": {
                            "type": "string",
                            "description": "检查命令，如 python3 --version",
                        },
                        "min_version": {
                            "type": "string",
                            "description": "可选，最低版本，如 3.10",
                        },
                        "install_hint": {
                            "type": "string",
                            "description": "可选，缺失时的安装提示，如 'apt install python3.11 或用 uv 安装'",
                        },
                    },
                    "required": ["name", "check_cmd"],
                },
                "description": (
                    "可选，环境要求清单。技术栈已确认时按技术栈生成（如后端项目要求 "
                    "Python/数据库、前端项目要求 Node）。子节点 AI 在项目首次开发时"
                    "逐条检查、缺失项自行安装；同项目迭代不重复检查"
                ),
            },
        },
    }
    returns = "创建结果：任务 ID 与状态（pending）+ 绑定的规则文档清单；失败时说明原因"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """基于会话三件套创建任务并产出结果事件。"""
        if context.spec_generation_failed:
            # 硬性守卫：本轮 generate_spec_docs 最近一次执行失败时，即使
            # 会话仍有旧草稿也拒绝创建——防止 LLM 被误导后基于过时规范
            # 误建任务、派发必然失败（修复：生成失败仍创建任务）
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        "本轮三件套规范文档刚刚生成失败，不能创建任务"
                        "（现有文档是旧草稿，不含本轮需求）。"
                        "请向用户说明生成失败原因，待重新生成成功后再创建任务。"
                    ),
                ),
            }
            return

        specification = context.current_specification
        if not _spec_is_substantive(specification):
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        "当前对话还没有可执行的三件套规范文档。"
                        "请先调用 generate_spec_docs 根据用户需求生成，再创建任务。"
                    ),
                ),
            }
            return

        requirement = str(context.args.get("requirement") or "").strip()
        if not requirement:
            requirement = next(
                (
                    msg.get("content", "").strip()
                    for msg in context.messages
                    if msg.get("role") == "user" and msg.get("content", "").strip()
                ),
                "未命名任务",
            )

        # 规则包解析（ADR-048）：按文档名精确匹配，不存在的跳过并在结果中提示
        rule_doc_names = [
            str(name).strip()
            for name in (context.args.get("rule_doc_names") or [])
            if str(name).strip()
        ]
        bound_names: list[str] = []
        missing_names: list[str] = []
        rule_doc_ids: list[uuid.UUID] = []
        if rule_doc_names:
            result = await session.execute(
                select(StandardDoc).where(StandardDoc.name.in_(rule_doc_names))
            )
            docs = {doc.name: doc.id for doc in result.scalars().all()}
            for name in dict.fromkeys(rule_doc_names):
                if name in docs:
                    rule_doc_ids.append(docs[name])
                    bound_names.append(name)
                else:
                    missing_names.append(name)

        request = TaskCreateRequest(
            requirement=requirement[:200],
            specification=TaskSpecification(
                spec=str(specification.get("spec") or ""),
                tasks=str(specification.get("tasks") or ""),
                checklist=str(specification.get("checklist") or ""),
                # 验收判据（ADR-061 M4）：随任务保存并在派发时下发；无判据 → None
                verify=normalize_verify_plan(specification.get("verify")),
            ),
            session_id=uuid.UUID(context.session_id) if context.session_id else None,
            parent_task_id=(uuid.UUID(context.parent_task_id) if context.parent_task_id else None),
            output_dir=context.output_dir or None,
            # 本轮修改说明：服务层自动挂链后作为迭代增量输入随派发下发（ADR-036）
            change_brief=requirement[:500],
            execution_mode="build",
            rule_doc_ids=rule_doc_ids or None,
            # 环境要求清单（ADR-048 P2）：技术栈确认后按技术栈生成
            environment_requirements=context.args.get("environment") or None,
            # ADR-067 派发确认门禁：编排创建的任务挂起自动派发，等用户确认
            dispatch_hold=True,
        )
        try:
            created = await TaskService().create_task(session, request)
        except Exception as exc:  # noqa: BLE001 — 创建失败如实回传给 LLM 处理
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=f"任务创建失败：{exc}",
                ),
            }
            return

        success_summary = (
            f"任务「{created.title}」已创建（ID {created.id}，状态 pending，并已关联当前对话）。"
        )
        if bound_names:
            success_summary += (
                f"已绑定规则文档：{', '.join(bound_names)}（创建时快照，随派发下发）。"
            )
        if missing_names:
            success_summary += (
                f"注意：规则文档 {', '.join(missing_names)} 在文档库中不存在，已跳过；"
                f"可用 generate_standard_doc 生成后再创建任务。"
            )
        success_summary += (
            "请先向用户报告任务已创建，待用户明确确认派发后再调用 smart_dispatch"
            "（用户未确认前不要派发）。"
        )
        # ADR-067 派发确认门禁：编排创建的任务默认挂起自动派发——调度器跳过，
        # 用户确认后 smart_dispatch → dispatch_task 清除门禁放行
        # ADR-067 待办先行：把会话名下待办迁移到新任务（旁路，失败不阻塞创建）
        try:
            if context.session_id:
                from app.services.orchestrator_todo_service import migrate_session_todos

                migrated = await migrate_session_todos(
                    session, uuid.UUID(context.session_id), created.id
                )
                if migrated:
                    success_summary += (
                        f"已把会话待办清单（{migrated} 项）迁移归属到本任务。"
                    )
        except Exception:  # noqa: BLE001 — 迁移是旁路，失败不影响任务创建
            import logging

            logging.getLogger(__name__).warning(
                "orchestrator_todos_migrate_failed",
                extra={"event": "orchestrator_todos_migrate_failed"},
                exc_info=True,
            )
        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=success_summary,
                data={
                    "task_id": str(created.id),
                    "status": created.status,
                    "rule_docs": bound_names,
                    "rule_docs_missing": missing_names,
                },
            ),
        }
