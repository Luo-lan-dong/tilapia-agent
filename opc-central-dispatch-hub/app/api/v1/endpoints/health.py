"""健康检查端点。"""

import logging

from fastapi import APIRouter, status

from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["健康检查"])


@router.get(
    "/",
    response_model=HealthResponse,
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="健康检查",
    description="返回中央调度中枢服务的健康状态。",
)
async def health_check() -> HealthResponse:
    """执行健康检查并返回服务状态。"""
    logger.info(
        "health_check_started",
        extra={"event": "health_check_started"},
    )
    health_status = "ok"
    logger.info(
        "health_check_completed",
        extra={
            "event": "health_check_completed",
            "status": health_status,
        },
    )
    return HealthResponse(
        status=health_status,
        service="opc-central-dispatch-hub",
        version="0.1.0",
    )
