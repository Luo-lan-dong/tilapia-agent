"""任务构建对话 API。"""

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.api.deps import get_async_session, get_orchestrator_service
from app.schemas.common import ApiResponse
from app.schemas.task_building import (
    TaskBuildChatRequest,
    TaskBuildChatResponse,
    TaskBuildingSessionUpdateRequest,
    TaskSpecification,
)
from app.services import steering_service
from app.services.orchestrator_service import OrchestratorService
from app.services.spec_builder_service import SpecBuilderService
from app.services.task_build_rounds import BuildRoundCollector, finalize_running_steps
from app.services.task_building_session_service import TaskBuildingSessionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["任务构建"])


def get_spec_builder_service(
    session: AsyncSession = Depends(get_async_session),
) -> SpecBuilderService:
    """获取 Spec Builder 服务实例依赖（非流式兼容入口）。"""
    return SpecBuilderService(session)


def get_session_service(
    session: AsyncSession = Depends(get_async_session),
) -> TaskBuildingSessionService:
    """获取任务构建会话服务实例依赖。"""
    return TaskBuildingSessionService(session)


@router.post(
    "/chat",
    response_model=ApiResponse[TaskBuildChatResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="任务构建对话",
    description="接收用户消息和当前三件套草案，返回 AI 回复和更新后的三件套。",
)
async def task_build_chat(
    request: TaskBuildChatRequest,
    service: SpecBuilderService = Depends(get_spec_builder_service),
) -> ApiResponse[TaskBuildChatResponse]:
    """任务构建对话入口（非流式）。"""
    logger.info(
        "task_build_chat_started",
        extra={
            "event": "task_build_chat_started",
            "session_id": str(request.session_id) if request.session_id else None,
        },
    )
    try:
        result = await service.chat(
            messages=[m.model_dump() for m in request.messages],
            current_specification=(
                request.current_specification.model_dump()
                if request.current_specification
                else None
            ),
            mode=request.mode,
        )
        logger.info(
            "task_build_chat_completed",
            extra={
                "event": "task_build_chat_completed",
                "session_id": str(request.session_id) if request.session_id else None,
                "message_count": len(request.messages),
            },
        )
        return ApiResponse(
            code=0,
            message="ok",
            data=TaskBuildChatResponse(
                ai_reply=result["ai_reply"],
                specification=result["specification"],
                is_ready_to_create=result["is_ready_to_create"],
                model=result["model"],
                mode=result["mode"],
            ),
        )
    except Exception:
        logger.exception("task_build_chat_failed")
        raise


def _format_sse_event(event: str, data: dict) -> str:
    """将事件格式化为 SSE 数据块。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "/chat/stream",
    response_class=StreamingResponse,
    status_code=status.HTTP_200_OK,
    summary="任务构建对话流式输出",
    description="以 SSE 流式返回 AI 回复、技能执行步骤、模型信息、三件套草案和创建状态（ADR-033 统一链路）。",
)
async def task_build_chat_stream(
    request: TaskBuildChatRequest,
    service: OrchestratorService = Depends(get_orchestrator_service),
    session_service: TaskBuildingSessionService = Depends(get_session_service),
) -> StreamingResponse:
    """任务构建对话流式入口（ADR-033：切换到 Orchestrator 技能链路）。"""
    logger.info(
        "task_build_stream_started",
        extra={
            "event": "task_build_stream_started",
            "session_id": str(request.session_id) if request.session_id else None,
        },
    )
    return StreamingResponse(
        _task_build_event_stream(request, service, session_service),
        media_type="text/event-stream",
    )


class TaskBuildSteerRequest(BaseModel):
    """执行中插话投递请求（ADR-045）。"""

    session_id: str = Field(..., description="进行中的构建会话 ID")
    content: str = Field(..., min_length=1, max_length=2000, description="插话内容")


@router.post(
    "/chat/steer",
    response_model=ApiResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="任务构建对话插话投递",
    description="AI 回复进行中投递插话消息，Orchestrator 在工具轮边界提升进 prompt（ADR-045）。",
)
async def task_build_chat_steer(request: TaskBuildSteerRequest) -> ApiResponse[dict]:
    """投递插话到活跃流的会话队列；无活跃流返回 409（前端降级普通发送）。"""
    accepted = steering_service.enqueue(request.session_id, request.content.strip())
    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前会话没有进行中的对话，请直接发送消息",
        )
    logger.info(
        "task_build_steer_enqueued",
        extra={
            "event": "task_build_steer_enqueued",
            "session_id": request.session_id,
        },
    )
    return ApiResponse(code=0, message="插话已提交", data={"accepted": True})


async def _task_build_event_stream(
    request: TaskBuildChatRequest,
    service: OrchestratorService,
    session_service: TaskBuildingSessionService,
) -> AsyncGenerator[str, None]:
    """任务构建流式事件生成器，转发 SSE 事件并持久化会话。

    Orchestrator 事件（step_started/step_finished/chunk/thinking/
    specification_delta/specification/status/error/done）透传前端；ADR-040
    起工具轨迹（assistant(tool_calls) + tool × N）经内部事件
    ``_history_trajectory`` 收集，随本轮 assistant 消息按序落库，下一轮
    随历史回流 LLM（多轮思考的地基），前端不透传该内部事件。
    """
    ai_reply_chunks: list[str] = []
    specification: dict | None = None
    model = ""
    mode = request.mode
    # 技能执行步骤与思考过程（ADR-033）：按回合收集——一次请求含 N 个
    # LLM 回合（思考→工具调用），回合边界 = ``_history_trajectory``（每个
    # 工具轮结束必发）；落库时按回合对齐附加到轨迹 assistant 消息，
    # 恢复会话后按「思考→工具→思考→…」层叠还原，不再压平堆叠
    collector = BuildRoundCollector()
    # 工具轨迹（ADR-040）：assistant(tool_calls) + tool × N 按序落库
    trajectory_messages: list[dict] = []
    # Steering 插话（ADR-045）：边界提升的 user 消息，按序插入轨迹与回复之间
    steered_messages: list[dict] = []

    try:
        async for event in service.chat_stream(
            messages=[m.model_dump(exclude_none=True) for m in request.messages],
            current_specification=(
                request.current_specification.model_dump()
                if request.current_specification
                else None
            ),
            mode=request.mode,
            session_id=str(request.session_id) if request.session_id else None,
            parent_task_id=str(request.parent_task_id) if request.parent_task_id else None,
            output_dir=request.output_dir,
        ):
            event_name, event_data = event["event"], event["data"]
            if event_name == "meta":
                model = event_data.get("model", "")
                mode = event_data.get("mode", mode)
            elif event_name == "chunk":
                ai_reply_chunks.append(event_data.get("delta", ""))
            elif event_name == "thinking":
                # 思考过程增量：按回合收集。当前回合已有工具步骤时收到新
                # 思考 = 开启了下一轮 LLM 回合（与前端回合分割语义一致）
                collector.add_thinking(event_data.get("delta", ""))
            elif event_name == "_steering_messages":
                # ADR-045 内部事件：边界提升的插话消息（不透传前端）
                steered_messages.extend(list(event_data.get("messages") or []))
                continue
            elif event_name == "specification":
                specification = event_data
            elif event_name == "step_started":
                collector.start_step(event_data)
            elif event_name == "step_finished":
                # 跨回合更新（问题修复）：回合分割后结束事件可能晚于新回合
                # 开启，只在当前回合查找会丢更新，步骤卡永远「执行中」
                collector.finish_step(event_data)
            elif event_name == "_history_trajectory":
                # ADR-040 内部事件：每轮全量累积，取最后一份即为完整轨迹；
                # 同时作为回合边界闭合当前回合（下个 thinking 开新回合）
                trajectory_messages = list(event_data.get("messages") or [])
                collector.close_round()
                continue
            yield _format_sse_event(event_name, event_data)

        # 自动实时保存（ADR-033）：带 session_id 的每轮流结束即持久化本轮
        # 用户消息与 AI 回复；specification 仅在触发文档生成时更新，
        # 普通技能对话（查节点/派任务等）保留会话已有三件套不覆盖
        if request.session_id:
            await _persist_chat_exchange(
                session_service=session_service,
                session_id=request.session_id,
                messages=[m.model_dump(exclude_none=True) for m in request.messages],
                ai_reply="".join(ai_reply_chunks),
                specification=specification,
                mode=mode,
                model=model,
                rounds=collector.rounds,
                trajectory_messages=trajectory_messages,
                steered_messages=steered_messages,
            )

        logger.info(
            "task_build_stream_completed",
            extra={
                "event": "task_build_stream_completed",
                "session_id": str(request.session_id) if request.session_id else None,
            },
        )
    except Exception:
        logger.exception("task_build_stream_failed")
        raise


async def _persist_chat_exchange(
    session_service: TaskBuildingSessionService,
    session_id: uuid.UUID,
    messages: list[dict[str, Any]],
    ai_reply: str,
    specification: dict | None,
    mode: str,
    model: str,
    rounds: list[dict[str, Any]] | None = None,
    trajectory_messages: list[dict] | None = None,
    steered_messages: list[dict] | None = None,
) -> None:
    """将本轮对话结果自动保存到历史会话。

    specification 为 None（本轮未触发文档生成）时不传该字段，
    service 层会跳过更新，保留会话已有三件套。
    rounds（回合拆分修复）为按 LLM 回合收集的 ``[{thinking, steps}]``，
    按顺序对齐附加到轨迹的 ``assistant(tool_calls)`` 消息上——恢复会话后
    按「思考→工具→思考→…」层叠渲染，不再把全部思考/步骤压平进一条消息；
    多余回合（最终正文轮的思考）附加到最终 assistant 消息。thinking/steps
    在 LLM 回流时被 _build_messages 剥离，不污染上下文。
    trajectory_messages（ADR-040）为本轮工具轨迹
    ``[assistant(tool_calls), tool × N]``，按序插入用户历史与最终回复
    之间，下一轮随 messages 回流 LLM。
    steered_messages（ADR-045）为边界提升的插话 user 消息，插入工具轨迹
    之后、最终回复之前（提升时序与落库顺序一致）。
    """
    updated_messages: list[dict] = list(messages)
    trajectory = list(trajectory_messages or [])
    round_index = 0
    for trajectory_message in trajectory:
        if trajectory_message.get("role") == "assistant" and trajectory_message.get("tool_calls"):
            if rounds and round_index < len(rounds):
                current = rounds[round_index]
                if current["thinking"]:
                    trajectory_message["thinking"] = current["thinking"]
                if current["steps"]:
                    trajectory_message["steps"] = current["steps"]
            round_index += 1
    updated_messages.extend(trajectory)
    if steered_messages:
        updated_messages.extend(steered_messages)
    remaining_rounds = (rounds or [])[round_index:]
    final_thinking = "".join(r["thinking"] for r in remaining_rounds)
    final_steps = [step for r in remaining_rounds for step in r["steps"]]
    if ai_reply or final_thinking or final_steps:
        assistant_message: dict = {"role": "assistant", "content": ai_reply}
        if final_thinking:
            assistant_message["thinking"] = final_thinking
        if final_steps:
            assistant_message["steps"] = final_steps
        updated_messages.append(assistant_message)
    # 孤儿 tool 清洗（修复 400）：前端回传渲染态与轨迹持久化不一致时，
    # tool 消息可能失去 assistant(tool_calls) 载体——落库前剔除，保证
    # 会话序列可安全回流 LLM
    seen_call_ids: set[str] = set()
    cleaned_messages: list[dict] = []
    for msg in updated_messages:
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or []:
                call_id = str(call.get("id") or "")
                if call_id:
                    seen_call_ids.add(call_id)
            cleaned_messages.append(msg)
        elif msg.get("role") == "tool":
            if str(msg.get("tool_call_id") or "") in seen_call_ids:
                cleaned_messages.append(msg)
            else:
                logger.warning(
                    "persist_orphan_tool_dropped",
                    extra={"event": "persist_orphan_tool_dropped"},
                )
        else:
            cleaned_messages.append(msg)
    updated_messages = cleaned_messages

    # 落库收尾（问题修复）：仍为 running 的步骤不会再收到结束事件，
    # 统一按失败收尾——否则历史会话恢复后步骤卡永远停在「执行中」
    finalized = finalize_running_steps(updated_messages)
    if finalized:
        logger.warning(
            "persist_running_steps_finalized",
            extra={"event": "persist_running_steps_finalized", "count": finalized},
        )

    update_request = TaskBuildingSessionUpdateRequest(
        messages=updated_messages,
        mode=mode,
        model=model,
    )
    if specification is not None:
        update_request.specification = TaskSpecification.model_validate(specification)

    await session_service.update(session_id, update_request)
