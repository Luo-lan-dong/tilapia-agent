"""任务相关 API 端点。"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session
from app.core.errors import (
    AgentNotFoundError,
    BusinessError,
    ErrorCode,
    TaskDispatchError,
    TaskNotFoundError,
)
from app.models.task import Task
from app.schemas.common import ApiResponse
from app.schemas.task import (
    OrchestratorTodoItem,
    OrchestratorTodoListResponse,
    TaskCreateRequest,
    TaskDispatchPreferenceRequest,
    TaskEligibilityResponse,
    TaskInsightAlert,
    TaskInsightResponse,
    TaskListResponse,
    TaskNodeChatRequest,
    TaskResponse,
    TaskSteerRequest,
    TaskTimelineEntry,
    TaskUsagePoint,
    TaskUsageSummary,
    TaskVerifyDecisionRequest,
)
from app.schemas.task_agent_trace import (
    AgentTraceAppendRequest,
    AgentTraceAppendResponse,
    AgentTraceListResponse,
    AgentTraceRowOut,
)
from app.services import orchestrator_todo_service, task_insight_service
from app.services.task_agent_trace_service import TaskAgentTraceService
from app.services.task_service import TaskService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["任务管理"])


@router.post(
    "",
    response_model=ApiResponse[TaskResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_201_CREATED,
    summary="创建任务",
    description="接收用户自然语言需求，调用任务规划 Agent 解析为结构化任务后落库。",
)
async def create_task(
    request: TaskCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[TaskResponse]:
    """创建任务。

    Args:
        request: 创建任务请求，包含用户自然语言需求。
        session: 异步数据库会话。
        service: 任务服务实例。

    Returns:
        统一响应，data 为创建成功的任务详情。
    """
    logger.info(
        "task_create_started",
        extra={
            "event": "task_create_started",
            "requirement_preview": request.requirement[:80],
        },
    )
    try:
        data = await service.create_task(session, request)
    except Exception:
        logger.exception("task_create_failed", extra={"event": "task_create_failed"})
        raise
    logger.info(
        "task_created",
        extra={
            "event": "task_created",
            "task_id": str(data.id),
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.post(
    "/{task_id}/cancel",
    response_model=ApiResponse[dict],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="取消任务",
    description=(
        "用户手动取消任务（ADR-048 实测修复）：pending 直接取消；"
        "assigned/running 置 failed 并向节点推送中断指令（Agent 中断 tilacode 会话）；终态 409。"
    ),
)
async def cancel_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[dict]:
    """取消任务并通知节点中断。"""
    logger.info(
        "task_cancel_started", extra={"event": "task_cancel_started", "task_id": str(task_id)}
    )
    try:
        result = await service.cancel_task(session, task_id)
    except TaskDispatchError as exc:
        raise BusinessError(
            status_code=status.HTTP_409_CONFLICT,
            biz_code=ErrorCode.TASK_STATE_NOT_ALLOWED,
            detail=exc.reason,
        ) from exc
    return ApiResponse(code=0, message="ok", data=result)


@router.post(
    "/{task_id}/steer",
    response_model=ApiResponse[dict],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="向执行中任务定向发言",
    description=(
        "ADR-061 M5a-steer：把一段话以 steer 方式送进该任务正在跑的 tilacode 会话，"
        "**不改变任务状态**（任务继续执行）。仅 assigned/running 可发言；"
        "节点离线时 409 明确失败，不静默丢弃。"
    ),
)
async def steer_task(
    task_id: uuid.UUID,
    payload: TaskSteerRequest,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[dict]:
    """向执行中任务的节点会话发言。"""
    logger.info(
        "task_steer_started",
        extra={"event": "task_steer_started", "task_id": str(task_id)},
    )
    try:
        result = await service.steer_task(session, task_id, payload.text)
    except TaskDispatchError as exc:
        raise BusinessError(
            status_code=status.HTTP_409_CONFLICT,
            biz_code=ErrorCode.TASK_STATE_NOT_ALLOWED,
            detail=exc.reason,
        ) from exc
    return ApiResponse(code=0, message="ok", data=result)


@router.post(
    "/{task_id}/node-chat",
    response_model=ApiResponse[dict],
    response_model_exclude_unset=True,
    status_code=status.HTTP_202_ACCEPTED,
    summary="@空闲节点：节点续聊对话",
    description=(
        "ADR-064 D1/D2：任务空闲时向指定节点续聊——中枢不建任务、不走规划/派发，"
        "把文本经 hub.node_chat 下发到节点对任务链既有会话续发，回复经"
        " agent.node_chat_reply 回流并落会话消息。请求体 {node_id, text}；"
        "成功返回 202 {reply_to}（异步等待节点回复）。"
        "任务执行中（assigned/running）返回 409，该场景应走 `/steer` 补充发言；"
        "目标节点不存在/离线返回 422；用户 @消息在下发前已落库，失败不丢失。"
    ),
)
async def node_chat(
    task_id: uuid.UUID,
    payload: TaskNodeChatRequest,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[dict]:
    """`@空闲节点` 空闲分支：续聊转发（ADR-064），返回 reply_to 供前端关联回复。"""
    logger.info(
        "task_node_chat_requested",
        extra={"event": "task_node_chat_requested", "task_id": str(task_id)},
    )
    try:
        result = await service.node_chat(session, task_id, payload.node_id, payload.text)
    except TaskDispatchError as exc:
        raise BusinessError(
            status_code=status.HTTP_409_CONFLICT,
            biz_code=ErrorCode.TASK_STATE_NOT_ALLOWED,
            detail=exc.reason,
        ) from exc
    except TaskNotFoundError as exc:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return ApiResponse(code=0, message="accepted", data=result)


@router.get(
    "",
    response_model=ApiResponse[TaskListResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="查询任务列表",
    description="分页查询任务列表，支持按状态和 Agent 类型筛选。",
)
async def list_tasks(
    status: str | None = Query(None, description="任务状态筛选"),
    agent_type: str | None = Query(None, description="Agent 类型筛选"),
    limit: int = Query(20, ge=1, le=100, description="每页数量，默认 20，最大 100"),
    offset: int = Query(0, ge=0, description="偏移量，默认 0"),
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
    # Annotated 模式：默认值保持 None，避免直接调用（测试）时拿到 Query 对象
    keyword: Annotated[
        str | None, Query(max_length=100, description="按任务标题模糊搜索（ADR-038）")
    ] = None,
) -> ApiResponse[TaskListResponse]:
    """分页查询任务列表。

    Args:
        status: 可选，按任务状态筛选。
        agent_type: 可选，按 Agent 类型筛选。
        limit: 每页返回条数，范围 1~100。
        offset: 分页偏移量，必须大于等于 0。
        session: 异步数据库会话。
        service: 任务服务实例。
        keyword: 可选，按任务标题模糊搜索。

    Returns:
        统一响应，data 包含任务列表和总条数。
    """
    logger.info(
        "task_list_started",
        extra={"event": "task_list_started"},
    )
    data = await service.list_tasks(
        session,
        status_filter=status,
        agent_type=agent_type,
        limit=limit,
        offset=offset,
        keyword=keyword,
    )
    logger.info(
        "task_list_completed",
        extra={
            "event": "task_list_completed",
            "count": data.pagination.total,
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.get(
    "/{task_id}",
    response_model=ApiResponse[TaskResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="查询任务",
    description="根据任务 ID 查询任务详情。",
)
async def get_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[TaskResponse]:
    """根据 ID 查询任务。

    Args:
        task_id: 任务 UUID。
        session: 异步数据库会话。
        service: 任务服务实例。

    Returns:
        统一响应，data 为任务详情。
    """
    logger.info(
        "task_get_started",
        extra={
            "event": "task_get_started",
            "task_id": str(task_id),
        },
    )
    data = await service.get_task(session, task_id)
    logger.info(
        "task_get_completed",
        extra={
            "event": "task_get_completed",
            "task_id": str(task_id),
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除任务",
    description="删除任务；running/assigned 状态禁止删除。构建会话的关联引用会被解除（会话保留）。",
)
async def delete_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> None:
    """删除任务（204 无返回体；错误经统一异常处理器返回）。"""
    logger.info(
        "task_delete_started",
        extra={"event": "task_delete_started", "task_id": str(task_id)},
    )
    try:
        await service.delete_task(session, task_id)
    except Exception:
        logger.exception(
            "task_delete_failed",
            extra={"event": "task_delete_failed", "task_id": str(task_id)},
        )
        raise
    logger.info(
        "task_delete_completed",
        extra={"event": "task_delete_completed", "task_id": str(task_id)},
    )


@router.put(
    "/{task_id}/dispatch-preference",
    response_model=ApiResponse[TaskResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="设置派发偏好（指定节点 / 强行派送 / 失败改派）",
    description=(
        "ADR-059 D2/D5：用户或中枢 AI 表达派发偏好，**偏好参与并决定最终选择**。"
        "target_node_id 非空 = 任务排队只等该节点（不经 AI 决策，达标当轮派送）；"
        "force=true 是**唯一**允许越过准入门槛的开关（审计记 forced_dispatch=true）；"
        "force=false 且指定节点不达标时保持排队并提示缺口。"
        "**failed 任务可借此改派**（回 pending 重新入队，复用既有文档与产物，"
        "不重新生成三件套）；completed 需走任务链迭代；running/assigned 需先取消。"
    ),
)
async def set_dispatch_preference(
    task_id: uuid.UUID,
    request: TaskDispatchPreferenceRequest,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[TaskResponse]:
    """设置派发偏好并让任务回到调度队列。"""
    logger.info(
        "task_dispatch_preference_requested",
        extra={
            "event": "task_dispatch_preference_requested",
            "task_id": str(task_id),
            "target_node_id": str(request.target_node_id) if request.target_node_id else None,
            "force": request.force,
        },
    )
    try:
        data = await service.set_dispatch_preference(
            session,
            task_id,
            target_node_id=request.target_node_id,
            force=request.force,
        )
    except AgentNotFoundError as exc:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.AGENT_NODE_NOT_FOUND,
            detail=f"目标节点不存在：{exc}",
        ) from exc
    except TaskDispatchError as exc:
        raise BusinessError(
            status_code=status.HTTP_409_CONFLICT,
            biz_code=ErrorCode.TASK_STATE_NOT_ALLOWED,
            detail=exc.reason,
        ) from exc
    except TaskNotFoundError as exc:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return ApiResponse(code=0, message="ok", data=data)


@router.get(
    "/{task_id}/insight",
    response_model=ApiResponse[TaskInsightResponse],
    status_code=status.HTTP_200_OK,
    summary="获取任务只读洞察（用量曲线 + 告警 + 时间线）",
    description=(
        "ADR-061 M5c：只读会话镜像——动作级摘要时间线（可离线回看「做过什么」）、"
        "用量曲线与汇总（含与 95.2% 缓存命中率基线的对比）、按需计算的告警"
        "（命中率跌破基线 / 连续同签名动作 / 长时间无文件写入）。"
        "**不返回原始工具输出全文**（Q12 裁决）。"
    ),
)
async def get_task_insight(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[TaskInsightResponse]:
    """读取任务洞察（任务不存在 404）。"""
    task = await session.get(Task, task_id)
    if task is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=f"任务不存在：{task_id}",
        )
    insight = await task_insight_service.collect_task_insight(session, task_id)
    return ApiResponse(
        code=0,
        message="ok",
        data=TaskInsightResponse(
            task_id=task_id,
            usage=[TaskUsagePoint.model_validate(point) for point in insight["points"]],
            summary=TaskUsageSummary.model_validate(insight["summary"]),
            alerts=[TaskInsightAlert.model_validate(alert) for alert in insight["alerts"]],
            timeline=[
                TaskTimelineEntry.model_validate(entry) for entry in insight["timeline"]
            ],
        ),
    )


@router.post(
    "/{task_id}/verify-decision",
    response_model=ApiResponse[TaskResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="验收阻断决策（接受现状 / 继续修复）",
    description=(
        "ADR-061 M4：任务因必过验收未通过或无法验证停在 `verify_blocked` 时，"
        "用户在此决定——`accept` 标记完成（并记录验收未通过），"
        "`retry` 回到 running 并把失败摘要 steer 给节点会话继续修复。"
        "非 `verify_blocked` 状态返回 409。24h 无决策由调度器兜底置 failed。"
    ),
)
async def decide_verify_blocked(
    task_id: uuid.UUID,
    request: TaskVerifyDecisionRequest,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[TaskResponse]:
    """处理验收阻断决策。"""
    logger.info(
        "task_verify_decision_requested",
        extra={
            "event": "task_verify_decision_requested",
            "task_id": str(task_id),
            "decision": request.decision,
        },
    )
    try:
        task = await service.decide_verify_blocked(
            session, task_id, request.decision, request.note
        )
    except TaskDispatchError as exc:
        raise BusinessError(
            status_code=status.HTTP_409_CONFLICT,
            biz_code=ErrorCode.TASK_STATE_NOT_ALLOWED,
            detail=exc.reason,
        ) from exc
    except TaskNotFoundError as exc:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return ApiResponse(code=0, message="ok", data=TaskResponse.model_validate(task))


@router.get(
    "/{task_id}/orchestrator-todos",
    response_model=ApiResponse[OrchestratorTodoListResponse],
    status_code=status.HTTP_200_OK,
    summary="获取中枢编排器待办（任务摘要下段）",
    description=(
        "ADR-061 M5d：返回中枢编排器自己的待办清单（任务级），供任务摘要**下段**只读展示。"
        "与节点上报的 `todos`（上段子 Agent 待办）**物理分离**；中枢尚未写待办时返回空列表。"
    ),
)
async def list_orchestrator_todos(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> ApiResponse[OrchestratorTodoListResponse]:
    """读取中枢编排器待办（按 seq 升序；任务不存在返回 404）。"""
    task = await session.get(Task, task_id)
    if task is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=f"任务不存在：{task_id}",
        )
    rows = await orchestrator_todo_service.list_todos(session, task_id)
    completed = sum(1 for row in rows if row.status == "completed")
    return ApiResponse(
        code=0,
        message="ok",
        data=OrchestratorTodoListResponse(
            task_id=task_id,
            items=[OrchestratorTodoItem.model_validate(row) for row in rows],
            pending_count=len(rows) - completed,
            completed_count=completed,
        ),
    )


@router.post(
    "/{task_id}/confirm-redispatch",
    response_model=ApiResponse[TaskResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="确认改派（待确认改派挂起态）",
    description=(
        "ADR-059 D5：节点离线且 AI 智能调度**关闭**时，任务停在 `requeue_pending`"
        "（待确认改派）等待用户答复——不会悄悄改派。本端点即用户的「确认」："
        "任务回到 `pending` 交调度器重新派发，**复用既有文档与产物**，不重新生成三件套。"
        "非 `requeue_pending` 状态返回 409（幂等保护）。"
    ),
)
async def confirm_redispatch(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[TaskResponse]:
    """确认把待确认改派的任务交回调度器。"""
    logger.info(
        "task_redispatch_confirm_requested",
        extra={"event": "task_redispatch_confirm_requested", "task_id": str(task_id)},
    )
    try:
        data = await service.confirm_redispatch(session, task_id)
    except TaskDispatchError as exc:
        raise BusinessError(
            status_code=status.HTTP_409_CONFLICT,
            biz_code=ErrorCode.TASK_STATE_NOT_ALLOWED,
            detail=exc.reason,
        ) from exc
    except TaskNotFoundError as exc:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return ApiResponse(code=0, message="ok", data=data)


@router.get(
    "/{task_id}/eligibility",
    response_model=ApiResponse[TaskEligibilityResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="查询派发准入诊断",
    description=(
        "ADR-059 D3：返回该任务全部候选节点的准入明细（可用内存、综合负载、"
        "剩余槽位、缺口描述）与下一步建议——把「排队中 0%」变成可解释的等待。"
    ),
)
async def get_task_eligibility(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
    service: TaskService = Depends(),
) -> ApiResponse[TaskEligibilityResponse]:
    """诊断任务为何未被派发。"""
    try:
        data = await service.get_dispatch_eligibility(session, task_id)
    except TaskNotFoundError as exc:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return ApiResponse(code=0, message="ok", data=data)


#: 轨迹读取默认返回条数（与 ``TaskAgentTraceService.list_rows`` 口径一致）
_AGENT_TRACE_LIST_DEFAULT_LIMIT = 200
#: 轨迹读取单次上限（越界值夹取，不报错）
_AGENT_TRACE_LIST_MAX_LIMIT = 1000


@router.post(
    "/{task_id}/agent-trace",
    response_model=ApiResponse[AgentTraceAppendResponse],
    status_code=status.HTTP_200_OK,
    summary="上报节点 Agent 会话轨迹（批量）",
    description=(
        "ADR-063 M1：节点侧批量上报**视图行**（status/reasoning/text/tool/todo/usage），"
        "中枢只落库、不做语义处理。`(task_id, seq)` 是幂等键——重复 seq、非法 kind、"
        "单行超 8 KB（截断）等**数据问题不报 4xx**，统一计入 `ignored`；"
        "只有任务不存在（404）与请求体结构错误（如单批超过 200 行 → 422）才会失败。"
    ),
)
async def append_task_agent_trace(
    task_id: uuid.UUID,
    request: AgentTraceAppendRequest,
    session: AsyncSession = Depends(get_async_session),
    service: TaskAgentTraceService = Depends(),
) -> ApiResponse[AgentTraceAppendResponse]:
    """批量追加轨迹行（任务不存在 404）。"""
    task = await session.get(Task, task_id)
    if task is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=f"任务不存在：{task_id}",
        )
    result = await service.append_rows(
        session,
        task_id,
        request.node_id,
        request.session_id,
        [row.model_dump() for row in request.rows],
    )
    logger.info(
        "task_agent_trace_appended",
        extra={
            "event": "task_agent_trace_appended",
            "task_id": str(task_id),
            "accepted": result["accepted"],
            "ignored": result["ignored"],
        },
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=AgentTraceAppendResponse(
            accepted=result["accepted"],
            ignored=result["ignored"],
        ),
    )


@router.get(
    "/{task_id}/agent-trace",
    response_model=ApiResponse[AgentTraceListResponse],
    status_code=status.HTTP_200_OK,
    summary="读取节点 Agent 会话轨迹（增量分页）",
    description=(
        "ADR-063 M1：按 `seq` 升序读取轨迹行，供前端折叠块只读显示/离线回看。"
        "`after_seq` 做增量拉取（只返回 seq 更大的行），`node_id` 可按节点过滤；"
        "`limit` 默认 200、上限 1000（**越界自动夹取，不报错**）；任务不存在 404。"
    ),
)
async def list_task_agent_trace(
    task_id: uuid.UUID,
    node_id: str | None = Query(None, description="按执行节点 ID 过滤"),
    after_seq: int | None = Query(None, description="只返回 seq 大于该值的行（增量拉取）"),
    limit: int = Query(
        _AGENT_TRACE_LIST_DEFAULT_LIMIT,
        description=(
            f"单次返回条数，默认 {_AGENT_TRACE_LIST_DEFAULT_LIMIT}，"
            f"上限 {_AGENT_TRACE_LIST_MAX_LIMIT}（越界自动夹取）"
        ),
    ),
    session: AsyncSession = Depends(get_async_session),
    service: TaskAgentTraceService = Depends(),
) -> ApiResponse[AgentTraceListResponse]:
    """按 seq 升序读取轨迹行（任务不存在 404；limit 越界夹取）。"""
    task = await session.get(Task, task_id)
    if task is None:
        raise BusinessError(
            status_code=status.HTTP_404_NOT_FOUND,
            biz_code=ErrorCode.TASK_NOT_FOUND,
            detail=f"任务不存在：{task_id}",
        )
    safe_limit = max(1, min(limit, _AGENT_TRACE_LIST_MAX_LIMIT))
    result = await service.list_rows(
        session,
        task_id,
        node_id=node_id,
        after_seq=after_seq,
        limit=safe_limit,
    )
    return ApiResponse(
        code=0,
        message="ok",
        data=AgentTraceListResponse(
            rows=[AgentTraceRowOut.model_validate(row) for row in result["rows"]],
            has_more=bool(result["has_more"]),
        ),
    )
