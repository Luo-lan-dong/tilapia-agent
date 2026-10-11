"""集群仪表盘聚合 API 端点（ADR-038）。

路径前缀：/dashboard。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_async_session
from app.schemas.common import ApiResponse
from app.schemas.dashboard import (
    AlertListResponse,
    AlertReadAllResponse,
    ClusterMetricsResponse,
    DeliverableListResponse,
)
from app.services.dashboard_service import DashboardService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["集群仪表盘"])


@router.get(
    "/metrics",
    response_model=ApiResponse[ClusterMetricsResponse],
    response_model_exclude_unset=True,
    summary="查询集群统计指标",
    description="聚合仪表盘 8 项统计：任务计数、在线节点、集群负载、告警数、今日 token 消耗与排队数。",
)
async def get_cluster_metrics(
    session: AsyncSession = Depends(get_async_session),
    service: DashboardService = Depends(),
) -> ApiResponse[ClusterMetricsResponse]:
    """返回集群统计指标聚合。"""
    logger.info("dashboard_metrics_started", extra={"event": "dashboard_metrics_started"})
    data = await service.get_metrics(session)
    logger.info(
        "dashboard_metrics_completed",
        extra={
            "event": "dashboard_metrics_completed",
            "total_tasks": data.total_tasks,
            "alert_count": data.alert_count,
        },
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.get(
    "/alerts",
    response_model=ApiResponse[AlertListResponse],
    response_model_exclude_unset=True,
    summary="查询异常告警列表",
    description=(
        "后端推导告警：danger=节点离线；warning=失败任务/待确认任务；"
        "info=创建超 10 分钟仍 pending 的排队任务。"
    ),
)
async def get_cluster_alerts(
    limit: int = Query(50, ge=1, le=200, description="返回条数上限，默认 50"),
    session: AsyncSession = Depends(get_async_session),
    service: DashboardService = Depends(),
) -> ApiResponse[AlertListResponse]:
    """返回推导告警列表。"""
    logger.info("dashboard_alerts_started", extra={"event": "dashboard_alerts_started"})
    data = await service.get_alerts(session, limit=limit)
    logger.info(
        "dashboard_alerts_completed",
        extra={"event": "dashboard_alerts_completed", "total": data.total},
    )
    return ApiResponse(code=0, message="ok", data=data)


@router.post(
    "/alerts/read-all",
    response_model=ApiResponse[AlertReadAllResponse],
    response_model_exclude_unset=True,
    summary="告警一键已读",
    description=(
        "将告警已读水位线推进到当前时刻（system_settings.alerts_read_at）；"
        "created_at 早于等于水位线的告警不再返回，此后新产生的告警正常出现。"
    ),
)
async def mark_all_alerts_read(
    session: AsyncSession = Depends(get_async_session),
    service: DashboardService = Depends(),
) -> ApiResponse[AlertReadAllResponse]:
    """告警一键已读。"""
    logger.info(
        "dashboard_alerts_read_all_started", extra={"event": "dashboard_alerts_read_all_started"}
    )
    read_at = await service.mark_all_alerts_read(session)
    return ApiResponse(code=0, message="ok", data=AlertReadAllResponse(read_at=read_at))


@router.get(
    "/deliverables",
    response_model=ApiResponse[DeliverableListResponse],
    response_model_exclude_unset=True,
    summary="查询交付物聚合列表",
    description="聚合最近任务的产物文件清单（只读 hub 缓存，不触发向 Agent 的网络拉取）。",
)
async def get_cluster_deliverables(
    limit: int = Query(20, ge=1, le=100, description="返回条数上限，默认 20"),
    session: AsyncSession = Depends(get_async_session),
    service: DashboardService = Depends(),
) -> ApiResponse[DeliverableListResponse]:
    """返回交付物聚合列表。"""
    logger.info("dashboard_deliverables_started", extra={"event": "dashboard_deliverables_started"})
    data = await service.get_deliverables(session, limit=limit)
    logger.info(
        "dashboard_deliverables_completed",
        extra={"event": "dashboard_deliverables_completed", "total": data.total},
    )
    return ApiResponse(code=0, message="ok", data=data)
