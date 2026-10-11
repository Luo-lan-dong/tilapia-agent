"""规范文档生成技能：包装 SpecBuilderAgent（ADR-033）。

复用现有三件套生成链路（ADR-010 / ADR-028）：对话历史、当前草案、
thinking 模式由 SkillContext 透传；``chunk`` / ``thinking`` /
``specification_delta`` / ``specification`` / ``status`` 事件原样转发，
前端文档面板流式渲染体验与升级前完全一致。
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.spec_builder import SpecBuilderAgent
from app.skills.base import Skill, SkillContext, SkillResult

_FORWARDED_EVENTS = {"chunk", "thinking", "specification_delta", "specification", "status", "meta"}


class GenerateSpecDocsSkill(Skill):
    """根据对话上下文生成任务三件套规范文档。"""

    name = "generate_spec_docs"
    display_name = "生成三件套规范文档"
    description = (
        "根据当前对话中的需求描述，生成 spec/tasks/checklist 三件套任务规范文档。"
        "当用户描述开发需求、要求创建/更新任务规范、或对话信息已足够成稿时调用。"
        "调用后右侧文档面板会流式生成三件套。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "context_note": {
                "type": "string",
                "description": "可选，补充给文档生成的要点说明",
            },
        },
    }
    returns = "三件套文档 {spec, tasks, checklist} 与 AI 回复；完成后前端面板展示全文"

    async def execute(
        self,
        session: AsyncSession,
        context: SkillContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """执行三件套生成并转发事件流。"""
        messages = list(context.messages)
        note = str(context.args.get("context_note") or "").strip()
        if note:
            messages.append({"role": "system", "content": f"补充要点：{note}"})

        agent = SpecBuilderAgent(session)
        final_spec: dict[str, Any] | None = None
        reply_chunks: list[str] = []
        ready = False
        error_message: str | None = None
        async for event in agent.chat_stream(
            messages,
            context.current_specification,
            mode=context.mode,
        ):
            event_name = event.get("event")
            if event_name == "done":
                continue
            if event_name == "specification":
                final_spec = event.get("data")
            elif event_name == "chunk":
                reply_chunks.append(event.get("data", {}).get("delta", ""))
            elif event_name == "status":
                ready = bool(event.get("data", {}).get("is_ready_to_create", ready))
            elif event_name == "error":
                error_message = str(event.get("data", {}).get("message") or "未知原因")
            # error 事件必须转发前端：此前被白名单静默吞掉，用户只看到
            # "文档已更新"的假成功；同时错误原因进入 summary 回传 LLM
            if event_name in _FORWARDED_EVENTS or event_name == "error":
                yield event

        # 生成结果回写执行上下文（ADR-033 修复）：同一轮对话内后续技能
        # （如 create_task）才能读到刚生成的三件套——否则本轮内连续调用
        # create_task 会因 context 仍是请求时的旧快照而永远失败
        if final_spec is not None:
            context.current_specification = final_spec

        if final_spec is None:
            # ADR-062 体验修复：区分「需要向用户澄清」（need_info，正常流程：
            # AI 会输出追问文本）与「真失败」——此前两者混为"生成失败：未知原因"，
            # 用户看到红色失败提示实为 AI 正常追问，误导性强
            clarification = ("".join(reply_chunks)).strip()
            if error_message is None and clarification:
                yield {
                    "event": "_result",
                    "data": SkillResult(
                        success=True,
                        summary=(
                            "AI 判定当前信息还不足以生成规范，需要先向用户确认关键点。"
                            f"请把以下问题转述给用户并等待回答：{clarification[:800]}"
                        ),
                        data={"need_clarification": True},
                    ),
                }
                return
            # 失败必须明确告知 LLM"生成失败"，防止其误以为文档已更新而
            # 继续调用 create_task 基于旧草稿误建任务
            yield {
                "event": "_result",
                "data": SkillResult(
                    success=False,
                    summary=(
                        f"三件套规范文档生成失败：{error_message or '未知原因'}。"
                        "不要调用 create_task 创建任务；请向用户如实说明失败原因，"
                        "并可建议稍后重试。"
                    ),
                    data={"error": error_message},
                ),
            }
            return

        yield {
            "event": "_result",
            "data": SkillResult(
                success=True,
                summary=(
                    f"三件套文档已生成"
                    f"{'，信息已足够创建任务' if ready else '，可继续与用户澄清细节'}。"
                    f"对用户说：{(''.join(reply_chunks))[:500] or '文档已更新，请查看右侧面板。'}"
                ),
                data={"specification": final_spec, "is_ready_to_create": ready},
            ),
        }
