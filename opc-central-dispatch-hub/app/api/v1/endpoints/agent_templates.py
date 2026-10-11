"""Agent 模板 API 端点。"""

import logging

from fastapi import APIRouter, Depends, Query, status

from app.schemas.agent_template import AgentTemplateListResponse
from app.schemas.common import ApiResponse
from app.services.agent_template_service import AgentTemplateService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agent 模板"])


@router.get(
    "",
    response_model=ApiResponse[AgentTemplateListResponse],
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
    summary="获取 Agent 模板列表",
    description="扫描静态目录返回可用 Agent 模板版本列表，支持按 agent_type 过滤。",
)
async def list_agent_templates(
    agent_type: str | None = Query(
        default=None,
        description="Agent 类型过滤，如 programming；不传则返回全部类型",
    ),
    service: AgentTemplateService = Depends(),
) -> ApiResponse[AgentTemplateListResponse]:
    """获取 Agent 模板版本列表。

    Args:
        agent_type: 可选的 Agent 类型过滤。
        service: 模板扫描服务实例。

    Returns:
        统一响应，data 为模板版本列表。
    """
    logger.info(
        "agent_templates_list_started",
        extra={
            "event": "agent_templates_list_started",
            "agent_type": agent_type or "all",
        },
    )
    data = service.list_templates(agent_type)
    logger.info(
        "agent_templates_list_completed",
        extra={
            "event": "agent_templates_list_completed",
            "agent_type": agent_type or "all",
            "count": len(data.templates),
        },
    )
    return ApiResponse(code=0, message="ok", data=data)
