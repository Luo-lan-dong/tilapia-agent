"""任务业务服务层。

注：本文件超过 300 行上限（见 docs/standards/coding-standards.md 4.3）。
任务创建（spec_chat / planning 两条链路）与派发查询共享状态机校验、
任务链字段解析（_resolve_chain_fields）与响应组装，拆分会割裂同构的
校验与装配流程，故按豁免场景保留并在此说明。
"""

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from structlog import contextvars

from app.agents.planning import DeepSeekAPIError, TaskPlanningAgent
from app.core.config import get_settings
from app.core.errors import (
    AgentNotFoundError,
    BusinessError,
    ErrorCode,
    TaskDispatchError,
    TaskNotFoundError,
    TaskPlanningError,
)
from app.models.agent_node import AgentNode, NodeStatus
from app.models.task import Task, TaskStatus
from app.models.task_agent_trace import TaskAgentTrace
from app.models.task_building_session import TaskBuildingSession
from app.schemas.common import PaginationInfo
from app.schemas.task import (
    TaskCreateRequest,
    TaskEligibilityCandidate,
    TaskEligibilityResponse,
    TaskListResponse,
    TaskResponse,
    normalize_executor_model,
)
from app.services import budget_policy_service, permission_policy_service, tool_call_log_service
from app.services.attachment_service import AttachmentService
from app.services.standard_doc_service import StandardDocService
from app.services.task_building_session_service import TaskBuildingSessionService

logger = logging.getLogger(__name__)

# 可读目录名中标题片段的最大字符数（ADR-036）
_DIR_TITLE_MAX_CHARS = 20
# 文件系统/跨平台非法目录字符（POSIX 保留 + Windows 保留，取交集防御）
_ILLEGAL_DIR_CHARS = set('/\\:*?"<>|')


def _sanitize_title_for_dir(title: str) -> str:
    """将需求标题净化为可作目录名的片段（ADR-036 可读命名）。

    规则：非法字符与空白统一转 ``-``、压缩连续 ``-``、去除首尾 ``-.``、
    截断到 ``_DIR_TITLE_MAX_CHARS``。
    """
    cleaned = "".join(
        "-" if ch.isspace() or ch in _ILLEGAL_DIR_CHARS else ch for ch in title.strip()
    )
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-.")[:_DIR_TITLE_MAX_CHARS]


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


def _validate_output_dir(output_dir: str | None) -> str | None:
    """校验任务产物保存路径（ADR-028）。

    路径为 Agent 节点本地目录语义，hub 不校验存在性，仅拒绝明显非法值：
    空串、含 ``..`` 越界片段、反斜杠（统一 POSIX 风格）。

    Raises:
        BusinessError: 非法路径时抛出（422 + 202012）。
    """
    if output_dir is None:
        return None
    trimmed = output_dir.strip()
    if not trimmed or ".." in trimmed.split("/") or "\\" in trimmed:
        raise BusinessError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            biz_code=ErrorCode.OUTPUT_DIR_INVALID,
            detail="产物保存路径非法：不能为空、不能包含 .. 或反斜杠",
        )
    return trimmed


def _compute_dispatch_window_until() -> datetime:
    """计算本次任务的派发窗口截止时间（UTC）。

    等于 ``now() + TASK_DISPATCH_WINDOW_SECONDS``。
    0 = 窗口已过（落地值是 now），下一轮调度器即可接管。
    """
    seconds = get_settings().task_dispatch_window_seconds
    return datetime.now(UTC) + timedelta(seconds=seconds)


class TaskService:
    """任务相关业务能力封装。"""

    async def create_task(
        self,
        session: AsyncSession,
        request: TaskCreateRequest,
    ) -> TaskResponse:
        """创建任务。

        如果请求包含 specification，直接保存为三件套任务；否则调用任务规划 Agent
        解析需求后落库，并记录关键日志。

        Args:
            session: 异步数据库会话。
            request: 创建任务请求。

        Returns:
            创建成功的任务响应。

        Raises:
            BusinessError: DeepSeek 服务不可用、LLM 配置缺失或规划解析失败时抛出。
            TaskNotFoundError: 指定的 parent_task_id 不存在时抛出。
        """
        _bind_request_id()
        explicit_dir = _validate_output_dir(request.output_dir)
        # 会话内自动挂链（ADR-036）：显式 parent 缺席且带 session_id 时，
        # 挂接本会话最近创建的任务为父任务（含链尾子任务）
        await self._auto_link_parent(session, request)
        chain_fields = await self._resolve_chain_fields(session, request)
        # 执行模型选择（ADR-029）：归一化后随 chain_fields 落库（两条创建链路共用）
        chain_fields["executor_model"] = normalize_executor_model(request.executor_model)
        # 规则文档内容快照（ADR-048）：创建时锁定，文档后续迭代/删除不影响本任务；
        # 未选择时为 None（不下发规则包）
        chain_fields["rule_docs"] = await StandardDocService().snapshot_rule_docs(
            session, request.rule_doc_ids or []
        )
        # 环境要求清单（ADR-048 P2）：随派发内联下发，子节点 AI 首次开发时检查并安装
        chain_fields["environment_requirements"] = request.environment_requirements or None
        # 可读目录名需含任务 ID 前缀，故 flush 前显式生成主键（ADR-036）
        task_id = uuid.uuid4()
        output_dir = await self._resolve_output_dir(
            session, request, explicit_dir, chain_fields, task_id
        )
        if request.specification:
            response = await self._create_task_from_spec(
                session, request, chain_fields, output_dir, task_id
            )
        else:
            response = await self._create_task_from_planning(
                session, request, chain_fields, output_dir, task_id
            )
        # 会话附件随任务创建回填 task_id（ADR-039），派发时随 payload 下发
        if request.session_id:
            await AttachmentService().attach_to_task(session, request.session_id, task_id)
        return response

    async def _auto_link_parent(
        self, session: AsyncSession, request: TaskCreateRequest
    ) -> Task | None:
        """同会话内自动挂链：显式 parent 缺席时挂接本会话最近任务（ADR-036）。

        会话语义即迭代上下文（用户提出修改需求时留在原会话，全新需求新建
        会话），故最近一条任务（含链尾子任务）即为本轮的父任务；链字段
        （root_task_id）由 ``_resolve_chain_fields`` 按既有逻辑派生。
        挂链成功且 change_brief 缺省时，以 requirement 截断填充本轮修改说明。

        Args:
            session: 异步数据库会话。
            request: 创建任务请求（可能被写入 parent_task_id / change_brief）。

        Returns:
            挂接到的父任务实例；未挂链时为 None。
        """
        if request.parent_task_id is not None or request.session_id is None:
            return None
        result = await session.execute(
            select(Task)
            .where(Task.session_id == request.session_id)
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        parent = result.scalar_one_or_none()
        if parent is None:
            logger.info(
                "task_auto_link_miss",
                extra={"event": "task_auto_link_miss", "session_id": str(request.session_id)},
            )
            return None
        request.parent_task_id = parent.id
        if not (request.change_brief or "").strip():
            request.change_brief = request.requirement[:500]
        logger.info(
            "task_auto_linked",
            extra={
                "event": "task_auto_linked",
                "session_id": str(request.session_id),
                "parent_task_id": str(parent.id),
                "root_task_id": str(parent.root_task_id or parent.id),
            },
        )
        return parent

    async def _resolve_output_dir(
        self,
        session: AsyncSession,
        request: TaskCreateRequest,
        explicit_dir: str | None,
        chain_fields: dict,
        task_id: uuid.UUID,
    ) -> str | None:
        """解析产物保存目录：显式 > 继承链根 > 自动可读命名（ADR-036）。

        - 显式：用户指定（已经 ``_validate_output_dir`` 校验）；
        - 挂链/迭代：继承链根任务的 output_dir（链根为空则 None，Agent 端
          回退 ``{workspace}/{root_id}``，保证同链同目录）;
        - 新链根：``{净化中文标题}-{任务ID前8位}``，用户在节点文件管理器中
          可直接辨认目录归属。

        Args:
            session: 异步数据库会话。
            request: 创建任务请求（requirement 为命名标题来源）。
            explicit_dir: 用户显式指定的目录（可为 None）。
            chain_fields: 链字段字典（含 root_task_id）。
            task_id: 预生成的任务主键（目录名前缀来源）。

        Returns:
            最终落库的 output_dir（可为 None）。
        """
        if explicit_dir:
            return explicit_dir
        root_task_id = chain_fields.get("root_task_id")
        if root_task_id is not None:
            root_task = await session.get(Task, root_task_id)
            if root_task is not None and root_task.output_dir:
                return root_task.output_dir
            return None
        title_part = _sanitize_title_for_dir(request.requirement) or "task"
        return f"{title_part}-{str(task_id)[:8]}"

    async def _resolve_chain_fields(
        self,
        session: AsyncSession,
        request: TaskCreateRequest,
    ) -> dict:
        """解析任务迭代链与执行模式字段。

        parent_task_id 非空时查询父任务，root_task_id 取父任务的
        root_task_id（父自身处于链上时继承链根）或父任务自身 ID（父即链根）。

        Args:
            session: 异步数据库会话。
            request: 创建任务请求。

        Returns:
            供 Task 构造使用的迭代链字段字典。

        Raises:
            TaskNotFoundError: 父任务不存在时抛出。
        """
        fields: dict = {
            "parent_task_id": None,
            "root_task_id": None,
            "execution_mode": request.execution_mode or "build",
            "change_brief": request.change_brief,
            # 关联构建会话：迭代入口据此恢复父任务的历史对话与三件套
            # （见 ADR-012 迭代会话恢复决策）；不存在时由外键约束兜底报错
            "session_id": request.session_id,
        }
        if request.parent_task_id is not None:
            parent_task = await session.get(Task, request.parent_task_id)
            if parent_task is None:
                logger.warning(
                    "task_create_parent_not_found",
                    extra={"parent_task_id": str(request.parent_task_id)},
                )
                raise TaskNotFoundError(str(request.parent_task_id))
            fields["parent_task_id"] = parent_task.id
            fields["root_task_id"] = parent_task.root_task_id or parent_task.id
        return fields

    async def _bind_session_task_id(
        self,
        session: AsyncSession,
        task: Task,
        session_id: uuid.UUID | None,
    ) -> None:
        """创建任务后反向更新关联构建会话的 task_id，用于恢复会话时继续跟踪方案审批。

        Args:
            session: 异步数据库会话。
            task: 已创建的任务实例。
            session_id: 关联的任务构建会话 ID，为空时不操作。
        """
        if session_id is None:
            return
        session_obj = await session.get(TaskBuildingSession, session_id)
        if session_obj is None:
            logger.warning(
                "task_create_session_not_found",
                extra={"task_id": str(task.id), "session_id": str(session_id)},
            )
            return
        session_obj.task_id = task.id
        await session.commit()
        logger.info(
            "task_session_bound",
            extra={
                "event": "task_session_bound",
                "task_id": str(task.id),
                "session_id": str(session_id),
            },
        )

    async def _create_task_from_spec(
        self,
        session: AsyncSession,
        request: TaskCreateRequest,
        chain_fields: dict,
        output_dir: str | None = None,
        task_id: uuid.UUID | None = None,
    ) -> TaskResponse:
        """通过用户直接提交的 specification 创建任务（spec_chat 模式）。"""
        # ADR-062：agent_type 不再硬编码 code——按需求用规划 Agent 判定
        # （文档/PPT/查资料类任务判 office 后才会被调度到办公节点）；
        # 判定失败不阻塞创建（三件套已就绪），回退 code 并告警
        agent_type = await self._resolve_spec_agent_type(session, request.requirement)
        task = Task(
            id=task_id,
            title=request.requirement[:50],
            description=request.requirement,
            agent_type=agent_type,
            status="pending",
            progress=0,
            priority=request.priority or "P2",
            specification=request.specification.model_dump(),
            source="spec_chat",
            dispatch_type="auto",
            # ADR-067 派发确认门禁：编排 AI 创建路径置 True，调度器跳过
            dispatch_hold=request.dispatch_hold,
            dispatch_window_until=_compute_dispatch_window_until(),
            output_dir=output_dir,
            # 任务级预算 override（ADR-061 M5b，Q9）：按字段覆盖全局；NULL=用全局
            budget=budget_policy_service.normalize_policy(request.budget)
            if request.budget
            else None,
            **chain_fields,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        await self._bind_session_task_id(session, task, request.session_id)

        logger.info(
            "task_created_from_spec_chat",
            extra={
                "event": "task_created_from_spec_chat",
                "task_id": str(task.id),
                "user_id": None,
                "task_type": task.agent_type,
                "source": task.source,
                "dispatch_type": task.dispatch_type,
                "dispatch_window_until": task.dispatch_window_until.isoformat()
                if task.dispatch_window_until
                else None,
            },
        )
        return TaskResponse.model_validate(task)

    async def _resolve_spec_agent_type(self, session: AsyncSession, requirement: str) -> str:
        """spec_chat 模式判定任务 agent_type（ADR-062）。

        用规划 Agent 按需求判定（office/code/...）；DeepSeek 异常或解析失败时
        回退 ``code`` 并记录告警——三件套已就绪，类型判定失败不应阻塞任务创建。
        """
        try:
            plan_result = await TaskPlanningAgent(session).plan(requirement)
            return plan_result.agent_type
        except Exception as exc:  # noqa: BLE001 — 任何规划失败均回退 code
            self._log_planning_failure(
                exc,
                "spec_chat_agent_type_fallback",
                {"fallback_agent_type": "code", "requirement_preview": requirement[:80]},
            )
            return "code"

    async def _create_task_from_planning(
        self,
        session: AsyncSession,
        request: TaskCreateRequest,
        chain_fields: dict,
        output_dir: str | None = None,
        task_id: uuid.UUID | None = None,
    ) -> TaskResponse:
        """通过规划 Agent 解析需求后创建任务（planning 模式）。"""
        planning_agent = TaskPlanningAgent(session)
        try:
            plan_result = await planning_agent.plan(request.requirement)
        except DeepSeekAPIError as exc:
            self._log_planning_failure(
                exc, "deepseek_api_call_failed", {"status_code": exc.status_code}
            )
            raise BusinessError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                biz_code=ErrorCode.AI_PLANNING_FAILED,
                detail="AI 规划服务暂不可用，请稍后重试",
            ) from exc
        except ValueError as exc:
            self._log_planning_failure(exc, str(exc))
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.LLM_CONFIG_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except TaskPlanningError as exc:
            self._log_planning_failure(
                exc, "task_planning_parse_failed", {"planning_error_reason": exc.reason}
            )
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.AI_PLANNING_FAILED,
                detail=f"任务需求解析失败: {exc.reason}",
            ) from exc

        task = Task(
            id=task_id,
            title=plan_result.task_name,
            description=plan_result.description,
            agent_type=plan_result.agent_type,
            status="pending",
            progress=0,
            priority=request.priority or "P2",
            dispatch_type="auto",
            # ADR-067 派发确认门禁：编排 AI 创建路径置 True，调度器跳过
            dispatch_hold=request.dispatch_hold,
            dispatch_window_until=_compute_dispatch_window_until(),
            output_dir=output_dir,
            # 任务级预算 override（ADR-061 M5b，Q9）：按字段覆盖全局；NULL=用全局
            budget=budget_policy_service.normalize_policy(request.budget)
            if request.budget
            else None,
            **chain_fields,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        await self._bind_session_task_id(session, task, request.session_id)

        logger.info(
            "task_created",
            extra={
                "event": "task_created",
                "task_id": str(task.id),
                "user_id": None,
                "task_type": task.agent_type,
                "dispatch_type": task.dispatch_type,
                "dispatch_window_until": task.dispatch_window_until.isoformat()
                if task.dispatch_window_until
                else None,
            },
        )
        return TaskResponse.model_validate(task)

    def _log_planning_failure(
        self,
        exc: Exception,
        reason: str,
        extra_fields: dict | None = None,
    ) -> None:
        """记录任务规划失败的错误日志，统一 extra 字段结构。"""
        _bind_request_id()
        log_extra: dict = {
            "event": "task_failed",
            "task_id": None,
            "agent_id": None,
            "error_code": type(exc).__name__,
            "reason": reason,
        }
        if extra_fields:
            log_extra.update(extra_fields)
        logger.error("task_failed", extra=log_extra, exc_info=True)

    async def list_tasks(
        self,
        session: AsyncSession,
        status_filter: str | None,
        agent_type: str | None,
        limit: int,
        offset: int,
        keyword: str | None = None,
    ) -> TaskListResponse:
        """分页查询任务列表（JOIN agent_nodes 取 assigned_node_name，ADR-017）。

        Args:
            session: 异步数据库会话。
            status_filter: 可选，按任务状态筛选。
            agent_type: 可选，按 Agent 类型筛选。
            limit: 每页返回条数。
            offset: 分页偏移量。
            keyword: 可选，按任务标题模糊搜索（ILIKE，ADR-038）。

        Returns:
            任务列表分页响应。
        """
        _bind_request_id()
        assigned_node = aliased(AgentNode)
        target_node = aliased(AgentNode)
        stmt = (
            select(Task, assigned_node.name, target_node.name)
            .outerjoin(assigned_node, assigned_node.id == Task.assigned_node_id)
            .outerjoin(target_node, target_node.id == Task.target_node_id)
            .order_by(Task.created_at.desc())
        )
        if status_filter:
            stmt = stmt.where(Task.status == status_filter)
        if agent_type:
            stmt = stmt.where(Task.agent_type == agent_type)
        if keyword:
            stmt = stmt.where(Task.title.ilike(f"%{keyword}%"))

        total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
        result = await session.execute(stmt.offset(offset).limit(limit))
        rows = result.all()
        total_count = total or 0

        items = [
            self._build_task_response(task, node_name=node_name, target_node_name=target_node_name)
            for task, node_name, target_node_name in rows
        ]

        response = TaskListResponse(
            items=items,
            pagination=PaginationInfo(
                page=offset // limit + 1,
                page_size=limit,
                total=total_count,
                total_pages=math.ceil(total_count / limit) if limit else 0,
            ),
        )

        logger.debug(
            "task_list_queried",
            extra={
                "event": "task_list_queried",
                "total": total_count,
            },
        )
        return response

    @staticmethod
    def _build_task_response(
        task: Task,
        *,
        node_name: str | None,
        target_node_name: str | None = None,
    ) -> TaskResponse:
        """从 ORM 任务 + JOIN 出的节点名构造响应。

        ``TaskResponse.assigned_node_name`` / ``target_node_name`` 不在
        ``Task`` 模型上，需手动注入（ADR-059：指定节点也要展示名字）。
        """
        response = TaskResponse.model_validate(task)
        return response.model_copy(
            update={
                "assigned_node_name": node_name,
                "target_node_name": target_node_name,
            }
        )

    async def get_task(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
    ) -> TaskResponse:
        """根据 ID 查询任务详情。

        Args:
            session: 异步数据库会话。
            task_id: 任务 UUID。

        Returns:
            任务详情响应。

        Raises:
            TaskNotFoundError: 任务不存在时抛出。
        """
        _bind_request_id()
        assigned_node = aliased(AgentNode)
        target_node = aliased(AgentNode)
        result = await session.execute(
            select(Task, assigned_node.name, target_node.name)
            .outerjoin(assigned_node, assigned_node.id == Task.assigned_node_id)
            .outerjoin(target_node, target_node.id == Task.target_node_id)
            .where(Task.id == task_id)
        )
        row = result.first()
        if row is None:
            logger.warning(
                "task_detail_not_found",
                extra={
                    "event": "task_detail_not_found",
                    "task_id": str(task_id),
                },
            )
            raise TaskNotFoundError(str(task_id))
        task, node_name, target_node_name = row

        logger.debug(
            "task_queried",
            extra={
                "event": "task_queried",
                "task_id": str(task_id),
            },
        )
        return self._build_task_response(
            task, node_name=node_name, target_node_name=target_node_name
        )

    async def set_dispatch_preference(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        *,
        target_node_id: uuid.UUID | None,
        force: bool,
    ) -> TaskResponse:
        """设置派发偏好：指定目标节点 / 要求强行派送（ADR-059 D2）。

        用户或中枢 AI 通过本方法表达偏好，**偏好参与并决定最终选择**：

        - ``target_node_id`` 非空：任务排队只等该节点（不经 AI 决策）；
        - ``target_node_id`` 为 None：清除指定，回到常规预筛；
        - ``force=True``：**唯一**允许越过准入门槛的开关，落库
          ``forced_dispatch=true`` 供审计；任务立即回 ``pending`` 等待调度器
          派往指定节点（未指定时派往任意合格节点）。
        - ``force`` 由真转假时清除标记，恢复严格排队。
        - **``failed`` 任务可借此改派**（ADR-059 D5）：失败任务回到 ``pending``
          重新入队，**复用既有文档与产物**，不重新生成三件套（这正是用户明确
          要求的「失败可改派，不必重写文档」）。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskDispatchError: 任务 ``completed``（已完成无需改派）或正在执行
                （``running``/``assigned``，需先取消或等待终态）。
            AgentNotFoundError: 指定节点不存在。
        """
        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.status == TaskStatus.COMPLETED.value:
            raise TaskDispatchError(
                task_id=str(task_id),
                reason="任务已完成，无需改派；如需继续修改请走任务链迭代",
            )
        if task.status in (TaskStatus.RUNNING.value, TaskStatus.ASSIGNED.value):
            raise TaskDispatchError(
                task_id=str(task_id),
                reason=f"任务正在执行（{task.status}），请先取消或等待终态再改派",
            )

        target_name: str | None = None
        if target_node_id is not None:
            node = await session.get(AgentNode, target_node_id)
            if node is None:
                raise AgentNotFoundError(str(target_node_id))
            target_name = node.name

        is_redispatch = task.status == TaskStatus.FAILED.value
        task.target_node_id = target_node_id
        task.forced_dispatch = bool(force)
        if task.status == TaskStatus.REVIEW.value:
            # 待确认任务不参与派发；仅记录偏好，状态不变
            pass
        else:
            # 回到 pending 由调度器按新偏好派发；清空回避记录，让指定节点
            # 立即可被重新考虑（用户显式指定即视为覆盖上轮拒单）
            task.status = TaskStatus.PENDING.value
            task.assigned_node_id = None
            if is_redispatch:
                # 失败改派 = 重新入队：恢复 auto 与派发窗口，避免卡在 pending+manual
                task.progress = 0
                task.dispatch_type = "auto"
                task.dispatch_window_until = _compute_dispatch_window_until()
            task.dispatch_hold_reason = None
            from app.services import dispatch_backoff

            dispatch_backoff.clear_rejections(str(task_id))
        await session.commit()
        await session.refresh(task)
        logger.info(
            "task_dispatch_preference_set",
            extra={
                "event": "task_dispatch_preference_set",
                "task_id": str(task_id),
                "target_node_id": str(target_node_id) if target_node_id else None,
                "forced_dispatch": task.forced_dispatch,
                "status": task.status,
            },
        )
        return self._build_task_response(task, node_name=None, target_node_name=target_name)

    async def confirm_redispatch(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
    ) -> TaskResponse:
        """确认改派：``requeue_pending`` → ``pending``（ADR-059 D5）。

        节点离线且 **AI 智能调度关闭** 时，任务停在 `requeue_pending` 等待用户
        确认（避免「悄悄把任务挪到不合适的节点」）。本方法即用户的「确认」动作：
        交回调度器重新派发，**复用既有文档与产物**，不重新生成三件套。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskDispatchError: 任务不在 ``requeue_pending`` 状态（幂等保护：
                重复点击不会把执行中的任务拉回队列）。
        """
        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.status != TaskStatus.REQUEUE_PENDING.value:
            raise TaskDispatchError(
                task_id=str(task_id),
                reason=(
                    f"任务当前状态为 {task.status}，仅 requeue_pending（待确认改派）可执行本操作"
                ),
            )

        task.status = TaskStatus.PENDING.value
        task.dispatch_hold_reason = None
        task.assigned_node_id = None
        # 确认改派即视为「交回调度器」，同时清空回避记录，让原节点可被重新考虑
        from app.services import dispatch_backoff

        dispatch_backoff.clear_rejections(str(task_id))
        await session.commit()
        await session.refresh(task)
        logger.info(
            "task_redispatch_confirmed",
            extra={
                "event": "task_redispatch_confirmed",
                "task_id": str(task_id),
                "status": task.status,
                "target_node_id": str(task.target_node_id) if task.target_node_id else None,
            },
        )
        return self._build_task_response(task, node_name=None, target_node_name=None)

    async def get_dispatch_eligibility(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
    ) -> TaskEligibilityResponse:
        """诊断任务的派发准入状态（ADR-059 D3：排队时告知「在等哪台、缺什么」）。

        Raises:
            TaskNotFoundError: 任务不存在。
        """
        from app.services import dispatch_backoff
        from app.services.dispatch_decision_service import diagnose_eligibility

        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        diagnostics = await diagnose_eligibility(
            session,
            task.agent_type,
            dispatch_backoff.excluded_nodes(str(task_id)),
        )
        eligible_count = sum(1 for d in diagnostics if d["eligible"])
        candidates = [
            TaskEligibilityCandidate(
                node_id=uuid.UUID(d["node_id"]),
                name=d["name"],
                online=d["online"],
                available_memory_gb=d["available_memory_gb"],
                load_score=d["load_score"],
                slots_free=d["slots_free"],
                eligible=d["eligible"],
                blocking=d["blocking"],
                blocking_reasons=d.get("blocking_reasons", []),
            )
            for d in diagnostics
        ]
        if task.forced_dispatch:
            hint = "已要求强行派送：调度器将跳过准入门槛派发（审计记 forced_dispatch=true）"
        elif eligible_count:
            hint = f"当前有 {eligible_count} 台可自主节点，调度器将在 10 秒内派发"
        elif task.target_node_id is not None:
            hint = "指定节点尚未达标：可继续等待其资源释放，或改派其他节点"
        else:
            hint = "无节点处于健康水位：可指定节点排队等待，或明确要求强行派送"
        return TaskEligibilityResponse(
            task_id=task_id,
            target_node_id=task.target_node_id,
            forced_dispatch=bool(task.forced_dispatch),
            eligible_count=eligible_count,
            candidates=candidates,
            hint=hint,
        )

    async def delete_task(self, session: AsyncSession, task_id: uuid.UUID) -> None:
        """删除任务（ADR-028 期增强：任务队列删除功能）。

        约束：
        - ``running`` / ``assigned`` 状态禁止删除（需等任务终态或先失败）；
        - 删除前解除构建会话的 ``task_id`` 外键引用（会话本身保留）。

        Raises:
            TaskNotFoundError: 任务不存在时抛出。
            TaskDispatchError: 任务正在执行或已分配节点时抛出。
        """
        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.status in (TaskStatus.RUNNING.value, TaskStatus.ASSIGNED.value):
            raise TaskDispatchError(
                task_id=str(task_id),
                reason=f"任务状态为 {task.status}，禁止删除；请等待终态",
            )

        await session.execute(
            sa_update(TaskBuildingSession)
            .where(TaskBuildingSession.task_id == task_id)
            .values(task_id=None)
        )
        await session.delete(task)
        await session.commit()
        logger.info(
            "task_deleted",
            extra={
                "event": "task_deleted",
                "task_id": str(task_id),
                "status": task.status,
            },
        )

    async def cancel_task(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        reason: str = "用户手动取消",
    ) -> dict:
        """用户手动取消任务（ADR-048 实测修复：子节点开发中无法打断）。

        - pending：未派发，直接置 failed（无节点需要中断）；
        - assigned/running：置 failed 后向分配节点推送 ``hub.task_cancel``，
          Agent 端中断 tilacode 会话并上报 failed（重复上报被终态忽略）；
        - 终态（completed/failed/review）：409 不可取消。

        Returns:
            {task_id, status, node_notified}——node_notified 表示取消指令
            是否成功推送到节点（节点离线时为 False，任务仍标记取消）。
        """
        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.status in (
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.REVIEW.value,
        ):
            raise TaskDispatchError(
                task_id=str(task_id),
                reason=f"任务已是终态（{task.status}），无法取消",
            )

        previous_status = task.status
        task.status = TaskStatus.FAILED.value
        # 取消原因可由调用方改写（ADR-061 M5b：预算闸硬停写「预算耗尽」而非「用户手动取消」）
        task.change_summary = reason[:200_000]
        await session.commit()
        await session.refresh(task)

        # ADR-067 修订：任务取消后中枢待办不应残留「进行中」——in_progress 重置
        # 回 pending（旁路，失败不影响取消）
        try:
            from app.services import orchestrator_todo_service

            await orchestrator_todo_service.reset_in_progress(session, task_id=task.id)
        except Exception:  # noqa: BLE001 — 清理旁路，失败仅告警
            logger.warning(
                "task_cancel_todos_reset_failed",
                extra={"event": "task_cancel_todos_reset_failed", "task_id": str(task_id)},
                exc_info=True,
            )

        node_notified = False
        node_id: uuid.UUID | None = task.assigned_node_id
        if previous_status in (TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value) and node_id:
            from datetime import UTC, datetime

            from app.schemas.ws_messages import HUB_TASK_CANCEL
            from app.services.connection_manager import connection_manager

            node_notified = await connection_manager.send_to_node(
                str(node_id),
                {
                    "event": HUB_TASK_CANCEL,
                    "payload": {"task_id": str(task_id), "reason": "用户手动取消"},
                    "timestamp": datetime.now(UTC).isoformat(),
                    "request_id": None,
                },
            )
        logger.info(
            "task_cancelled_by_user",
            extra={
                "event": "task_cancelled_by_user",
                "task_id": str(task_id),
                "previous_status": previous_status,
                "node_notified": node_notified,
            },
        )
        return {"task_id": str(task_id), "status": task.status, "node_notified": node_notified}

    async def decide_verify_blocked(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        decision: str,
        note: str | None = None,
    ) -> Task:
        """处理验收阻断决策（ADR-061 M4，Q7）。

        - ``accept``：接受现状 → 任务置 ``completed``（progress=100），审计记录「验收未通过但用户接受」；
        - ``retry``：继续修复 → 任务回到 ``running``，把失败摘要（+ 可选 note）经 steer 送给节点会话。

        仅 ``verify_blocked`` 状态可决策（其它状态 409），避免误操作正常任务。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskDispatchError: 状态不允许 / 节点离线（retry 时）。
        """
        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.status != TaskStatus.VERIFY_BLOCKED.value:
            raise TaskDispatchError(
                task_id=str(task_id),
                reason=f"任务不在验收阻断态（当前 {task.status}），无需决策",
            )

        report = task.verify_report if isinstance(task.verify_report, dict) else {}
        summary = str(report.get("summary") or "自动验收未通过")[:2000]

        if decision == "accept":
            task.status = TaskStatus.COMPLETED.value
            task.progress = 100
            task.verify_blocked_at = None
            task.change_summary = (
                ((task.change_summary + "\n\n") if task.change_summary else "")
                + "【验收未通过，用户选择接受现状】"
                + (f"\n{note}" if note else "")
            )[:200_000]
            await session.commit()
            await session.refresh(task)
            await tool_call_log_service.record(
                kind="agent_task_event",
                name="verify_decision_accept",
                task_id=str(task_id),
                result_summary={"decision": "accept", "verify_status": report.get("status")},
            )
            logger.info(
                "verify_blocked_accepted",
                extra={"event": "verify_blocked_accepted", "task_id": str(task_id)},
            )
            return task

        # retry：验收失败摘要拼入 change_brief 并重新派发。节点会话 ID 固定
        # 派生自任务（ses_task_{base_task_id or task_id}），重新执行会 resume
        # 同一 tilacode 会话——上下文延续、工作区产物保留。此前实现为「状态回
        # running + steer」，但 blocked 后节点会话可能已结束，steer 被
        # task_steer_ignored 忽略导致任务假死（ADR-061 验收 #5 实测复现）。
        task.verify_blocked_at = None
        task.change_brief = (
            ((task.change_brief + "\n\n") if task.change_brief else "")
            + f"自动验收未通过，请继续修复后重新自检：\n{summary}"
            + (f"\n\n补充要求：{note}" if note else "")
        )[:200_000]
        await session.commit()
        await session.refresh(task)
        dispatched = await self.dispatch_task(
            session,
            task_id,
            target=task.execution_target or "code",
        )
        await tool_call_log_service.record(
            kind="agent_task_event",
            name="verify_decision_retry",
            task_id=str(task_id),
            result_summary={
                "decision": "retry",
                "verify_status": report.get("status"),
                "redispatched": dispatched.get("status"),
            },
        )
        logger.info(
            "verify_blocked_retry",
            extra={
                "event": "verify_blocked_retry",
                "task_id": str(task_id),
                "dispatch_status": dispatched.get("status"),
            },
        )
        await session.refresh(task)
        return task

    async def steer_task(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        text: str,
    ) -> dict:
        """向执行中任务的节点会话定向发言（ADR-061 M5a-steer）。

        与 ``cancel_task`` 的关键区别：**不改变任务状态**，只把一段话以 steer 方式送进
        该任务正在跑的 tilacode 会话，影响其后续行为；任务继续执行到自身终态。

        约束：
        - 仅 ``assigned`` / ``running`` 可发言（其它状态没有活动会话）；
        - 任务未分配节点、或节点当前离线 → 明确失败（不静默丢弃，否则用户以为发出去了）；
        - 空内容拒绝。

        Returns:
            {task_id, delivered, text_length}。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskDispatchError: 状态不允许 / 未分配节点 / 节点离线 / 内容为空。
        """
        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.status not in (TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value):
            raise TaskDispatchError(
                task_id=str(task_id),
                reason=f"任务状态为 {task.status}，只有执行中（assigned/running）的任务可以对话",
            )
        content = (text or "").strip()
        if not content:
            raise TaskDispatchError(task_id=str(task_id), reason="发言内容不能为空")
        node_id: uuid.UUID | None = task.assigned_node_id
        if node_id is None:
            raise TaskDispatchError(task_id=str(task_id), reason="任务未分配节点，无法发言")

        from app.schemas.ws_messages import HUB_TASK_STEER
        from app.services.connection_manager import connection_manager

        delivered = await connection_manager.send_to_node(
            str(node_id),
            {
                "event": HUB_TASK_STEER,
                "payload": {"task_id": str(task_id), "text": content, "reason": "中枢定向发言"},
                "timestamp": datetime.now(UTC).isoformat(),
                "request_id": None,
            },
        )
        logger.info(
            "task_steer_sent",
            extra={
                "event": "task_steer_sent",
                "task_id": str(task_id),
                "node_id": str(node_id),
                "delivered": delivered,
                "text_length": len(content),
            },
        )
        if delivered:
            # ADR-061 M5c：发言写入事件表，供只读时间线离线回看（含内容摘要）
            await tool_call_log_service.record(
                kind="steering",
                name="task_steer_sent",
                task_id=str(task_id),
                result_summary={"agent_id": str(node_id), "text": content[:500]},
            )
            # ADR-064 D5：steer 用户消息同样落会话消息（刷新不丢；旁路原则）
            node = await session.get(AgentNode, node_id)
            mention = node.name if node is not None and node.name else str(node_id)[:8]
            await TaskBuildingSessionService.append_message(
                session,
                task.session_id,
                {
                    "role": "user",
                    "content": content,
                    "mention_node": mention,
                    "ts": datetime.now(UTC).isoformat(),
                },
            )
        if not delivered:
            raise TaskDispatchError(task_id=str(task_id), reason="节点当前离线，发言未送达")
        return {"task_id": str(task_id), "delivered": True, "text_length": len(content)}

    async def node_chat(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        node_id: uuid.UUID,
        text: str,
    ) -> dict[str, str]:
        """`@空闲节点` = 节点续聊对话（ADR-064 D1/D2，替代旧建任务路径）。

        语义：**不建任务、不走规划/派发、完全绕开任务状态机**——把用户文本经
        ``hub.node_chat`` 下行送到目标节点，节点对任务链的既有 tilacode 会话
        （``ses_task_{root_task_id}``）``send_prompt(delivery="queue")``，回复经
        ``agent.node_chat_reply`` 回流并落会话消息。

        分支判定：
        - 任务仍在执行（assigned/running）→ 拒绝（该场景 `@节点` 走 ``steer_task``）；
        - 目标节点不存在或离线 → 422 明确失败（不静默丢弃）；
        - 空内容拒绝。

        持久化：用户 @消息写入任务所属会话（``tasks.session_id`` →
        ``task_building_sessions.messages``），WS 下发**之前**落库——即使下发失败
        消息也不丢（ADR-064 降级链路）。

        Args:
            session: 异步数据库会话。
            task_id: 当前任务 ID（用于定位任务链与会话）。
            node_id: 目标节点 ID。
            text: 用户发给节点的文本。

        Returns:
            ``{"reply_to": "<uuid>"}``，供前端关联后续的节点回复/错误事件。

        Raises:
            TaskNotFoundError: 任务不存在。
            TaskDispatchError: 内容为空 / 任务执行中。
            BusinessError: 目标节点不存在或离线（422）。
        """
        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        content = (text or "").strip()
        if not content:
            raise TaskDispatchError(task_id=str(task_id), reason="对话内容不能为空")
        if task.status in (TaskStatus.ASSIGNED.value, TaskStatus.RUNNING.value):
            raise TaskDispatchError(
                task_id=str(task_id),
                reason="任务执行中，@节点 将作为补充发言送达当前会话（应走 steer 接口）",
            )
        node = await session.get(AgentNode, node_id)
        if node is None:
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.AGENT_NODE_NOT_FOUND,
                detail=f"目标节点不存在：{node_id}",
            )
        if node.status == NodeStatus.OFFLINE.value:
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.AGENT_NODE_OFFLINE,
                detail=f"节点「{node.name or node_id}」当前离线，无法续聊",
            )

        # ADR-064 D4：续聊轮轨迹接续——trace_task_id 取任务链最后任务，
        # last_seq 取该任务当前 max(seq)（无轨迹为 0），节点据此从 last_seq+1 续
        root = task.root_task_id or task.id
        trace_task_id = await session.scalar(
            select(Task.id)
            .where(or_(Task.root_task_id == root, Task.id == root))
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(1)
        )
        if trace_task_id is None:
            # 防御：任务自身必命中上面的查询，正常不会走到
            trace_task_id = task.id
        last_seq = await session.scalar(
            select(func.max(TaskAgentTrace.seq)).where(TaskAgentTrace.task_id == trace_task_id)
        )
        reply_to = uuid.uuid4()

        # 用户消息先落库（旁路原则：落库失败不阻塞对话主链路）
        mention = node.name or str(node_id)[:8]
        await TaskBuildingSessionService.append_message(
            session,
            task.session_id,
            {
                "role": "user",
                "content": content,
                "mention_node": mention,
                "ts": datetime.now(UTC).isoformat(),
            },
        )

        from app.schemas.ws_messages import HUB_NODE_CHAT
        from app.services.connection_manager import connection_manager

        delivered = await connection_manager.send_to_node(
            str(node_id),
            {
                "event": HUB_NODE_CHAT,
                "payload": {
                    "task_id": str(trace_task_id),
                    "root_task_id": str(root),
                    "node_id": str(node_id),
                    "text": content,
                    "reply_to": str(reply_to),
                    "trace_task_id": str(trace_task_id),
                    "last_seq": int(last_seq or 0),
                },
                "timestamp": datetime.now(UTC).isoformat(),
                "request_id": None,
            },
        )
        if not delivered:
            # WS 层面不在线（agent_nodes 状态尚未与连接同步）：消息已落库不丢失
            raise BusinessError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                biz_code=ErrorCode.AGENT_NODE_OFFLINE,
                detail="节点当前离线，续聊未送达（消息已保留在会话中）",
            )
        await tool_call_log_service.record(
            kind="steering",
            name="node_chat_sent",
            task_id=str(task_id),
            result_summary={
                "node_id": str(node_id),
                "reply_to": str(reply_to),
                "trace_task_id": str(trace_task_id),
                "last_seq": int(last_seq or 0),
                "text": content[:500],
            },
        )
        logger.info(
            "node_chat_sent",
            extra={
                "event": "node_chat_sent",
                "task_id": str(task_id),
                "trace_task_id": str(trace_task_id),
                "node_id": str(node_id),
                "reply_to": str(reply_to),
                "last_seq": int(last_seq or 0),
                "text_length": len(content),
            },
        )
        return {"reply_to": str(reply_to)}

    async def dispatch_task(
        self,
        session: AsyncSession,
        task_id: uuid.UUID,
        target: str,
        mode: str | None = None,
        node_id: uuid.UUID | None = None,
        force: bool = False,
    ) -> dict[str, str]:
        """手动派发任务：阻断自动派发 + 重新入队（ADR-017）。

        行为变更（vs 旧版）：
        1. ``dispatch_type`` 显式写为 ``manual``，调度器扫描时 WHERE 过滤
           ``dispatch_type='auto'`` 会跳过本任务，真正阻断自动派发。
        2. ``dispatch_window_until`` 清空（NULL），与 dispatch_type=manual
           保持一致。
        3. 防重复派发仅拦截在途状态（running/assigned）；pending+manual
           （plan 批准后的初始态）允许再次派发（ADR-017 修正）。
        4. ``node_id`` 非空时绕过调度器直接指派给该节点并立即下发
           （ADR-017 增强：手动派发支持选择具体节点）。
        5. ``failed`` 终态任务允许重新派发（ADR-017 补充）：带 ``node_id``
           时直接指派（dispatch_type 归位 manual）；不带时视为重新入队，
           恢复 ``auto`` 与派发窗口交还调度器，避免卡死在 pending+manual。

        Args:
            session: 异步数据库会话。
            task_id: 任务 UUID。
            target: 目标子系统标识，记录到 execution_target 备查。
            mode: 可选执行模式（plan/build），传入时更新任务 execution_mode。
            node_id: 可选手动指定执行节点 ID。

        Returns:
            派发结果字典，包含 task_id、target、dispatch_type、status。

        Raises:
            TaskNotFoundError: 任务不存在时抛出。
            AgentNotFoundError: 指定节点不存在时抛出。
            TaskDispatchError: 任务缺少规范、处于在途状态（running/assigned）、
                节点类型不匹配或节点离线时抛出。
        """
        _bind_request_id()
        task = await session.get(Task, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.specification is None:
            raise TaskDispatchError(
                task_id=str(task_id),
                reason="任务缺少规范文档，无法派发",
            )
        # 并发防重（2026-10-03 用户实测）：identity map 可能缓存派发前的旧快照
        # （skill 决策期间调度器已把任务派发为 assigned），直接读实体缓存会漏掉
        # 在途状态 → 同一任务被两条路径各派一次，节点两轮执行互相取消、悬挂 0%。
        # 用列查询直读最新已提交状态——不能 refresh 实体：refresh 会覆盖同
        # session 内未提交的本地修改（reject/approve_plan 先改 change_brief
        # 再入队的流程会被清空，2026-10-03 实测回归）。
        current_status = await session.scalar(select(Task.status).where(Task.id == task_id))
        if current_status == TaskStatus.RUNNING.value:
            raise TaskDispatchError(
                task_id=str(task_id),
                reason="任务正在执行中，无法重新派发",
            )
        if current_status == TaskStatus.ASSIGNED.value:
            raise TaskDispatchError(
                task_id=str(task_id),
                reason="任务已分配节点等待执行，无法重复派发",
            )
        # 防重复派发仅拦截在途状态（running/assigned）。pending+manual 是
        # "等待手动派发"的合法状态（plan 批准后的初始态，见 ADR-017 修正），
        # 必须允许再次派发（含补选节点），否则任务将永久卡死在待分配。
        is_redispatch = current_status == TaskStatus.FAILED.value

        # ADR-067 派发确认门禁：走到 dispatch_task 即代表派发已确认
        # （用户确认后 smart_dispatch / 任务队列手动派发），清除挂起标记
        task.dispatch_hold = False
        task.execution_target = target
        if mode is not None:
            task.execution_mode = mode
        if is_redispatch:
            # 失败重派 = 重新入队：先回 pending，恢复 auto 与派发窗口
            task.status = TaskStatus.PENDING.value
            task.progress = 0
            task.dispatch_type = "auto"
            task.dispatch_window_until = _compute_dispatch_window_until()
        else:
            task.dispatch_type = "manual"
            task.dispatch_window_until = None

        # 手动指定节点（ADR-017 增强）：绕过调度器直接指派并立即下发
        if node_id is not None:
            node = await session.get(AgentNode, node_id)
            if node is None:
                raise AgentNotFoundError(str(node_id))
            if node.agent_type != task.agent_type:
                raise TaskDispatchError(
                    task_id=str(task_id),
                    reason=f"节点类型不匹配：任务需要 {task.agent_type}，节点为 {node.agent_type}",
                )
            if node.status == "offline":
                raise TaskDispatchError(
                    task_id=str(task_id),
                    reason="目标节点离线，无法派发",
                )
            # ADR-059 D2：指定节点但未达标 → 排队等待该节点（不静默改派他处），
            # 除非用户明确要求强行派送（唯一例外，审计 forced_dispatch=true）
            from app.services.dispatch_decision_service import eligibility_gap, is_node_eligible

            if not is_node_eligible(node):
                gap = eligibility_gap(node) or "未达准入门槛"
                if not force:
                    task.target_node_id = node.id
                    task.forced_dispatch = False
                    task.status = TaskStatus.PENDING.value
                    task.assigned_node_id = None
                    task.dispatch_hold_reason = (
                        f"指定节点「{node.name}」暂不可自主：{gap}（可等待或要求强行派送）"
                    )[:200]
                    await session.commit()
                    logger.info(
                        "task_dispatch_deferred_to_target",
                        extra={
                            "event": "task_dispatch_deferred_to_target",
                            "task_id": str(task_id),
                            "target_node_id": str(node.id),
                            "gap": gap,
                        },
                    )
                    return {
                        "task_id": str(task_id),
                        "target": target,
                        "dispatch_type": task.dispatch_type,
                        "status": task.status,
                        "queued_on_node": node.name,
                        "hold_reason": task.dispatch_hold_reason,
                    }
                task.forced_dispatch = True
                logger.warning(
                    "task_forced_dispatch_requested",
                    extra={
                        "event": "task_forced_dispatch_requested",
                        "task_id": str(task_id),
                        "target_node_id": str(node.id),
                        "gap": gap,
                    },
                )
            task.target_node_id = node.id
            task.status = TaskStatus.ASSIGNED.value
            task.assigned_node_id = node.id
            task.progress = 0
            task.dispatch_hold_reason = None
            # 指派节点后阻断调度器接管（失败重派时上面先置了 auto，这里归位）
            task.dispatch_type = "manual"
            task.dispatch_window_until = None
            # ADR-032：节点 busy 为纯展示态（由 Agent 心跳在跑数派生），
            # 手动派发与自动派发语义一致，不再直接改写节点行
            await session.commit()
            await session.refresh(task)
            # 事务提交后再下发 WS 消息（复用调度器发送函数，失败仅记日志：
            # 节点断线重连对账机制会重新感知任务）。延迟导入避免模块循环依赖。
            from app.services.scheduler import (
                _build_dispatch_message,
                _build_executor_model_entry,
                _send_dispatch_to_node,
            )
            from app.services.standard_doc_service import build_rule_docs_payload
            from app.services.task_handoff_service import build_handoff_payload

            message = _build_dispatch_message(task)
            # 权限策略（ADR-061 M2b，Q1 全局单条）：手动派发同样下发
            manual_policy = await permission_policy_service.load_policy(session)
            message["payload"]["permission_policy"] = manual_policy
            # 执行模型与凭据注入（ADR-029）
            message["payload"]["executor_model"] = await _build_executor_model_entry(
                session, task, {}
            )
            # 现场迁移（ADR-034）：链上有历史现场时附带 handoff
            handoff = await build_handoff_payload(session, task)
            if handoff is not None:
                message["payload"]["handoff"] = handoff
            # 规则文档（ADR-048）：规范正文+元规则自动附加；传入目标节点按环境签名召回经验（ADR-061 M6）
            rule_docs = await build_rule_docs_payload(session, task, node)
            if rule_docs:
                message["payload"]["rule_docs"] = rule_docs
            await _send_dispatch_to_node(str(node.id), message)
            await tool_call_log_service.record(
                kind="dispatch",
                name=str(node.name or node.id),
                task_id=str(task_id),
                result_summary={
                    "dispatch_type": "manual",
                    "agent_id": str(node.id),
                    **permission_policy_service.audit_summary(manual_policy),
                },
            )
            logger.info(
                "task_manual_dispatched_to_node",
                extra={
                    "event": "task_manual_dispatched_to_node",
                    "task_id": str(task_id),
                    "node_id": str(node.id),
                    "node_status_before": node.status,
                },
            )
            return {
                "task_id": str(task_id),
                "target": target,
                "node_id": str(node.id),
                "dispatch_type": task.dispatch_type,
                "status": "assigned",
            }

        task.status = TaskStatus.PENDING.value
        task.assigned_node_id = None
        task.progress = 0
        await session.commit()
        await session.refresh(task)

        logger.info(
            "task_manual_dispatched",
            extra={
                "event": "task_manual_dispatched",
                "task_id": str(task_id),
                "target": target,
                "execution_mode": task.execution_mode,
                "dispatch_type": task.dispatch_type,
                "status": task.status,
            },
        )
        return {
            "task_id": str(task_id),
            "target": target,
            "dispatch_type": task.dispatch_type,
            "status": "queued",
        }
