"""FastAPI 应用工厂。"""

import asyncio
import logging
import os
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from structlog import contextvars

from app.api.v1.api import api_router
from app.core.config import get_settings
from app.core.errors import (
    AgentNotFoundError,
    AppError,
    ErrorCode,
    InvalidConfigError,
    MissingConfigError,
    TaskDispatchError,
    TaskNotFoundError,
    TaskPlanningError,
)
from app.core.logging import configure_logging
from app.core.middleware import (
    RequestIdInjectionMiddleware,
    _generate_request_id,
    _get_request_id,
)
from app.core.resource_static import register_resource_static, resolve_baseline_static_dir
from app.core.static_spa import mount_web_console, resolve_web_dist_dir
from app.models.base import AsyncSessionLocal
from app.services.agent_reverse_notify import start_reverse_notify
from app.services.llm_provider_service import LLMProviderService
from app.services.node_heartbeat import start_heartbeat_monitor, stop_heartbeat_monitor
from app.services.resource_check_scheduler import start_resource_check_loop
from app.services.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)
request_logger = logging.getLogger("api.middleware.request_logger")


def _setup_logging() -> None:
    """初始化日志配置：按环境设置级别与格式，单独屏蔽 SQLAlchemy 引擎日志刷屏。"""
    settings = get_settings()
    configure_logging(
        environment=settings.app_env,
        log_level=settings.log_level,
        log_dir=settings.log_dir,
        ring_buffer_size=settings.log_ring_buffer_size,
    )


async def _reconcile_hub_public_url_port(session) -> None:
    """启动时校准 hub_public_url 的端口（ADR-031）。

    桌面模式端口避让后实际端口可能与用户配置的 Hub URL 端口不一致：
    此时自动把 URL 端口部分替换为实际端口，保证新装 Agent 的安装命令
    使用正确地址（已装 Agent 由端口探测自愈）。

    仅在 APP_ENV=desktop（launcher 注入 PORT）时生效：源码模式手动设
    PORT 启动的实例（如临时验证实例）不得静默改写持久化配置。
    hub_public_url 未配置时跳过；任何异常只记日志不阻断启动。
    """
    if get_settings().app_env != "desktop":
        return
    raw_port = os.environ.get("PORT")
    if not raw_port:
        return
    try:
        actual_port = int(raw_port)
    except ValueError:
        return
    try:
        from sqlalchemy import select

        from app.models.system_settings import SystemSettings

        result = await session.execute(select(SystemSettings).limit(1))
        row = result.scalar_one_or_none()
        if row is None or not row.hub_public_url:
            return
        parsed = urlparse(row.hub_public_url)
        if parsed.port == actual_port:
            return
        netloc = parsed.hostname or ""
        if parsed.username:
            netloc = f"{parsed.username}:{parsed.password or ''}@{netloc}"
        netloc = f"{netloc}:{actual_port}"
        new_url = urlunparse(
            (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
        )
        old_url = row.hub_public_url
        row.hub_public_url = new_url
        await session.commit()
        logger.info(
            "hub_public_url_port_reconciled",
            extra={
                "event": "hub_public_url_port_reconciled",
                "old_url": old_url,
                "new_url": new_url,
                "actual_port": actual_port,
            },
        )
    except Exception:  # noqa: BLE001 校准失败不阻断启动
        logger.warning("hub_public_url_port_reconcile_failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动与关闭事件。"""
    settings = get_settings()
    app.state.settings = settings

    async with AsyncSessionLocal() as session:
        await LLMProviderService().ensure_default_provider(session)
        await _reconcile_hub_public_url_port(session)

    heartbeat_stop_event, heartbeat_task = start_heartbeat_monitor()
    scheduler_stop_event, scheduler_task = start_scheduler()
    # ADR-022：资源云端检查循环（与调度器共享停止事件）
    resource_check_task = start_resource_check_loop(scheduler_stop_event)
    # ADR-035：中枢重启主动反连（一次性后台任务，通知离线 Agent 新地址）
    reverse_notify_task = start_reverse_notify()
    try:
        yield
    finally:
        await stop_scheduler(scheduler_stop_event, scheduler_task)
        await stop_heartbeat_monitor(heartbeat_stop_event, heartbeat_task)
        if resource_check_task is not None:
            scheduler_stop_event.set()
            try:
                await asyncio.wait_for(resource_check_task, timeout=5)
            except TimeoutError:
                resource_check_task.cancel()
        reverse_notify_task.cancel()


def _resolve_biz_code(exc: HTTPException) -> int:
    """根据异常解析业务错误码。"""
    biz_code = getattr(exc, "biz_code", None)
    if isinstance(biz_code, int):
        return biz_code

    if exc.status_code == status.HTTP_404_NOT_FOUND:
        return ErrorCode.RESOURCE_NOT_FOUND
    if exc.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY):
        return ErrorCode.REQUEST_VALIDATION_FAILED
    return ErrorCode.INTERNAL_SERVER_ERROR


def _resolve_app_error_status_and_code(exc: AppError) -> tuple[int, int]:
    """将业务域异常映射为 HTTP 状态码和业务错误码。"""
    if isinstance(exc, TaskNotFoundError):
        return status.HTTP_404_NOT_FOUND, ErrorCode.TASK_NOT_FOUND
    if isinstance(exc, TaskDispatchError):
        return status.HTTP_409_CONFLICT, ErrorCode.TASK_STATE_NOT_ALLOWED
    if isinstance(exc, AgentNotFoundError):
        return status.HTTP_404_NOT_FOUND, ErrorCode.AGENT_NODE_NOT_FOUND
    if isinstance(exc, TaskPlanningError):
        return status.HTTP_422_UNPROCESSABLE_ENTITY, ErrorCode.AI_PLANNING_FAILED
    if isinstance(exc, MissingConfigError):
        return status.HTTP_404_NOT_FOUND, ErrorCode.LLM_CONFIG_NOT_FOUND
    if isinstance(exc, InvalidConfigError):
        return status.HTTP_400_BAD_REQUEST, ErrorCode.REQUEST_VALIDATION_FAILED
    return status.HTTP_500_INTERNAL_SERVER_ERROR, ErrorCode.INTERNAL_SERVER_ERROR


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    settings = get_settings()
    _setup_logging()

    app = FastAPI(
        title="OPC 中央调度中枢",
        description="OPC 中央调度中枢 REST API 服务",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_logging_middleware(
        request: Request,
        call_next: Callable[[Request], Response],
    ) -> Response:
        """为每个请求生成唯一 request_id 并记录请求日志。

        request_id 由外层的 ``RequestIdInjectionMiddleware`` 生成并写入
        ``scope["state"]``；本中间件优先从 ``request.state`` 读取，保证日志与
        响应体中的 request_id 一致。
        """
        request_id = getattr(request.state, "request_id", None) or _generate_request_id()
        request.state.request_id = request_id
        contextvars.bind_contextvars(request_id=request_id)
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")

        request_logger.info(
            "request_started",
            extra={
                "method": request.method,
                "path": request.url.path,
                "request_id": request_id,
                "client_ip": client_ip,
                "user_agent": user_agent,
            },
        )

        start_time = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000

            request_logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            # 慢请求阈值走 Pydantic Settings（environment-config-standards §5.2），
            # 每次请求从 lru_cache 单例读取，开销可忽略
            slow_request_threshold_ms = get_settings().slow_request_threshold_ms
            if duration_ms > slow_request_threshold_ms:
                request_logger.warning(
                    "slow_request_detected",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "request_id": request_id,
                        "duration_ms": round(duration_ms, 2),
                        "threshold_ms": slow_request_threshold_ms,
                    },
                )

            return response
        finally:
            contextvars.unbind_contextvars("request_id")

    app.add_middleware(RequestIdInjectionMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        """将 HTTPException / BusinessError 转换为统一响应格式。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": _resolve_biz_code(exc),
                "message": str(exc.detail),
                "data": None,
                "request_id": _get_request_id(request),
            },
        )

    @app.exception_handler(AppError)
    async def app_error_handler(
        request: Request,
        exc: AppError,
    ) -> JSONResponse:
        """将业务域异常（AppError 子类）转换为统一响应格式。"""
        status_code, biz_code = _resolve_app_error_status_and_code(exc)
        logger.warning(
            "business_error_occurred",
            extra={
                "request_id": _get_request_id(request),
                "exc_type": type(exc).__name__,
                "biz_code": biz_code,
                "status_code": status_code,
            },
        )
        return JSONResponse(
            status_code=status_code,
            content={
                "code": biz_code,
                "message": str(exc),
                "data": None,
                "request_id": _get_request_id(request),
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """将请求参数校验错误转换为统一响应格式。"""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": ErrorCode.REQUEST_VALIDATION_FAILED,
                "message": "请求参数校验失败",
                "data": None,
                "request_id": _get_request_id(request),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """捕获未处理异常，返回统一内部错误响应。"""
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": _get_request_id(request),
                "exc_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": ErrorCode.INTERNAL_SERVER_ERROR,
                "message": "服务内部错误",
                "data": None,
                "request_id": _get_request_id(request),
            },
        )

    # ADR-022：/static 动态服务（运行期缓存优先 → 内置基线回退）
    register_resource_static(
        app,
        cache_dir=Path(settings.resource_cache_dir),
        baseline_dir=resolve_baseline_static_dir(),
    )
    app.include_router(api_router, prefix="/api/v1")

    # ADR-021：web/ 前端产物存在时由后端托管（根路径返回前端页面），
    # 否则保持纯后端行为（根路径返回服务信息 JSON）
    web_dist_dir = resolve_web_dist_dir()
    if web_dist_dir is not None:
        mount_web_console(app, web_dist_dir)
    else:

        @app.get("/", tags=["根路径"], summary="服务根路径")
        async def root() -> dict[str, str]:
            """根路径返回服务基本信息。"""
            return {
                "service": "opc-central-dispatch-hub",
                "version": "0.1.0",
                "docs": "/docs",
            }

    return app


app = create_app()
