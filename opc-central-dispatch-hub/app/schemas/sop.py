"""SOP 知识管理 Schema（ADR-051）。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

SOP_SOURCES = ("builtin", "learned")


class SopCreate(BaseModel):
    """创建/固化 SOP。"""

    name: str = Field(min_length=1, max_length=100, description="SOP 名称（唯一）")
    scene: str = Field(min_length=1, max_length=30, description="场景标识（小写）")
    content: str = Field(min_length=1, description="SOP 正文（Markdown）")
    source: str = Field(default="learned", description="来源：builtin/learned")
    enabled: bool = Field(default=True, description="是否启用")


class SopUpdate(BaseModel):
    """编辑 SOP（全部可选，缺省不更新）。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    scene: str | None = Field(default=None, min_length=1, max_length=30)
    content: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class SopResponse(BaseModel):
    """SOP 条目响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    scene: str
    content: str
    source: str
    enabled: bool
    hit_count: int
    created_at: datetime
    updated_at: datetime


class SopListResponse(BaseModel):
    """SOP 分页列表。"""

    total: int
    page: int
    page_size: int
    items: list[SopResponse]
