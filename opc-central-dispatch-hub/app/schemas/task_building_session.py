"""任务构建会话相关 Schema。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PaginatedResponse


class TaskBuildingSessionBase(BaseModel):
    """任务构建会话基础字段。"""

    title: str = Field(..., min_length=1, max_length=255, description="会话标题")
    messages: list[dict] = Field(default_factory=list, description="对话历史")
    specification: dict | None = Field(None, description="当前三件套草案")
    mode: str = Field(
        default="thinking",
        pattern=r"^(fast|thinking)$",
        description="对话模式：fast / thinking",
    )


class TaskBuildingSessionCreate(TaskBuildingSessionBase):
    """创建会话请求。"""

    model: str | None = Field(None, description="当前使用的模型标识")


class TaskBuildingSessionUpdate(BaseModel):
    """更新会话请求（全部可选）。"""

    title: str | None = Field(None, min_length=1, max_length=255)
    messages: list[dict] | None = None
    specification: dict | None = None
    mode: str | None = Field(None, pattern=r"^(fast|thinking)$")
    model: str | None = None


class TaskBuildingSessionResponse(BaseModel):
    """会话响应模型。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="会话 ID")
    title: str = Field(..., description="会话标题")
    messages: list[dict] | None = Field(None, description="对话历史")
    specification: dict | None = Field(None, description="当前三件套草案")
    mode: str = Field(..., description="对话模式")
    model: str | None = Field(None, description="当前使用的模型标识")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="更新时间")


class TaskBuildingSessionListResponse(PaginatedResponse[TaskBuildingSessionResponse]):
    """会话列表分页响应。"""
